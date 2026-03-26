from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from kiraclaw_agentd.engine import _ensure_provider_credentials, create_model
from kiraclaw_agentd.settings import KiraClawSettings


@dataclass
class ObserverDecision:
    intent: str
    reply_text: str


def _clip(text: str, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _format_snapshot(snapshot: dict[str, Any]) -> str:
    lines = [
        f"session_id: {snapshot.get('session_id') or ''}",
        f"state: {snapshot.get('state') or ''}",
        f"elapsed_seconds: {snapshot.get('elapsed_seconds') or 0}",
        f"queued_runs: {snapshot.get('queued_runs') or 0}",
        f"current_request: {_clip(str(snapshot.get('prompt') or ''), 300)}",
    ]

    stream_tail = _clip(str(snapshot.get("streamed_text_tail") or ""), 600)
    if stream_tail:
        lines.append(f"stream_tail: {stream_tail}")

    recent_tools = snapshot.get("recent_tool_events") or []
    if recent_tools:
        formatted = ", ".join(
            _clip(f"{item.get('phase')}:{item.get('name')}", 60)
            for item in recent_tools
            if item.get("name")
        )
        if formatted:
            lines.append(f"recent_tools: {formatted}")

    active_processes = snapshot.get("active_processes") or []
    if active_processes:
        summaries: list[str] = []
        for item in active_processes[:2]:
            command = _clip(str(item.get("command") or ""), 120)
            status = str(item.get("status") or "unknown")
            summaries.append(f"{item.get('session_id')}: {status}: {command}")
        if summaries:
            lines.append(f"active_processes: {' | '.join(summaries)}")

    return "\n".join(lines)


class ObserverService:
    def __init__(
        self,
        settings: KiraClawSettings,
        *,
        model_factory: Callable[[str, str | None, int], Any] | None = None,
        credential_checker: Callable[[KiraClawSettings, str], None] | None = None,
    ) -> None:
        self.settings = settings
        self._model_factory = model_factory or create_model
        self._credential_checker = credential_checker or _ensure_provider_credentials

    def classify_inflight(
        self,
        user_message: str,
        snapshot: dict[str, Any],
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> ObserverDecision:
        fallback = self._fallback_classification(user_message, snapshot)
        try:
            parsed = self._chat_json(
                system_prompt=(
                    "You are a read-only observer for an in-progress KiraClaw run.\n"
                    "You cannot do work, call tools, or modify the current task.\n"
                    "Choose exactly one intent:\n"
                    "- status_query: the user is asking what is happening, progress, status, timing, or why it is taking time\n"
                    "- queue_next: the message is a new follow-up task that should wait until the current run completes\n"
                    "- unsupported_control: the user wants to cancel, stop, interrupt, reprioritize, or steer the current run\n"
                    "Return strict JSON only: {\"intent\":\"...\",\"reply_text\":\"...\"}\n"
                    "reply_text rules:\n"
                    "- status_query: answer from the snapshot only, in one or two short sentences\n"
                    "- queue_next: briefly acknowledge that the new request will wait until the current run completes\n"
                    "- unsupported_control: briefly say that the current in-progress run cannot be modified or canceled yet\n"
                    "Never invent progress beyond the snapshot."
                ),
                user_prompt=(
                    f"Incoming user message:\n{_clip(user_message, 400)}\n\n"
                    f"Current run snapshot:\n{_format_snapshot(snapshot)}"
                ),
                provider=provider,
                model=model,
            )
        except Exception:
            return fallback
        if not parsed:
            return fallback

        intent = str(parsed.get("intent") or "").strip().lower()
        reply_text = str(parsed.get("reply_text") or "").strip()
        if intent not in {"status_query", "queue_next", "unsupported_control"}:
            return fallback
        if not reply_text:
            reply_text = fallback.reply_text
        return ObserverDecision(intent=intent, reply_text=reply_text)

    def summarize_heartbeat(
        self,
        snapshot: dict[str, Any],
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> str:
        fallback = self._fallback_heartbeat(snapshot)
        try:
            parsed = self._chat_json(
                system_prompt=(
                    "You are a read-only observer for an in-progress KiraClaw run.\n"
                    "Write one short user-facing progress update from the snapshot only.\n"
                    "Do not mention internal implementation details unless they clearly help.\n"
                    "Do not promise completion times.\n"
                    "Return strict JSON only: {\"reply_text\":\"...\"}"
                ),
                user_prompt=f"Current run snapshot:\n{_format_snapshot(snapshot)}",
                provider=provider,
                model=model,
            )
        except Exception:
            return fallback
        if not parsed:
            return fallback
        reply_text = str(parsed.get("reply_text") or "").strip()
        return reply_text or fallback

    def _chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        provider: str | None,
        model: str | None,
    ) -> dict[str, Any] | None:
        selected_provider = provider or self.settings.provider
        selected_model = model or self.settings.model
        self._credential_checker(self.settings, selected_provider)
        model_impl = self._model_factory(
            selected_provider,
            selected_model,
            min(self.settings.max_tokens, 512),
        )
        response = model_impl.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=[],
        )
        return _extract_json_object(response.text or "")

    def _fallback_classification(self, user_message: str, snapshot: dict[str, Any]) -> ObserverDecision:
        message = str(user_message or "").strip()
        lowered = message.lower()
        if any(token in lowered for token in ["cancel", "stop", "pause", "interrupt"]):
            return ObserverDecision(
                intent="unsupported_control",
                reply_text="현재 진행 중인 작업은 수정하거나 취소할 수 없어요. 작업이 끝난 뒤 이어서 반영할게요.",
            )
        if any(token in message for token in ["취소", "중단", "멈춰", "그만", "바꿔", "대신", "말고"]):
            return ObserverDecision(
                intent="unsupported_control",
                reply_text="현재 진행 중인 작업은 수정하거나 취소할 수 없어요. 작업이 끝난 뒤 이어서 반영할게요.",
            )
        if any(token in lowered for token in ["status", "progress", "doing", "how long", "what are you", "still"]):
            return ObserverDecision(intent="status_query", reply_text=self._fallback_heartbeat(snapshot))
        if any(token in message for token in ["상태", "진행", "어디까지", "뭐 하고", "얼마나", "어떻게 됐"]):
            return ObserverDecision(intent="status_query", reply_text=self._fallback_heartbeat(snapshot))
        return ObserverDecision(
            intent="queue_next",
            reply_text="현재 작업이 끝난 뒤 이어서 처리할게요.",
        )

    def _fallback_heartbeat(self, snapshot: dict[str, Any]) -> str:
        recent_tools = snapshot.get("recent_tool_events") or []
        for item in reversed(recent_tools):
            name = str(item.get("name") or "").strip()
            if name:
                return f"현재 {name} 작업을 진행 중입니다. 완료되면 이어서 알려드릴게요."

        active_processes = snapshot.get("active_processes") or []
        if active_processes:
            command = _clip(str(active_processes[0].get("command") or ""), 80)
            if command:
                return f"현재 `{command}` 작업을 계속 진행 중입니다."

        prompt = _clip(str(snapshot.get("prompt") or ""), 120)
        if prompt:
            return f"현재 요청을 계속 처리 중입니다: {prompt}"
        return "현재 작업을 계속 진행 중입니다."
