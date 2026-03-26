from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def summarize_tool_events(tool_events: list[dict] | None) -> str:
    counts: dict[str, int] = {}
    for event in tool_events or []:
        if event.get("phase") != "start":
            continue
        name = str(event.get("name") or "").strip()
        if not name or name == "submit":
            continue
        counts[name] = counts.get(name, 0) + 1

    if not counts:
        return ""

    parts = [f"{name} x{count}" if count > 1 else name for name, count in counts.items()]
    return f"Used: {', '.join(parts)}"


def append_tool_summary(text: str, tool_events: list[dict] | None) -> str:
    summary = summarize_tool_events(tool_events)
    if not summary:
        return text

    base = str(text or "").rstrip()
    if not base:
        return summary
    return f"{base}\n\n{summary}"


def _parse_timestamp(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _format_elapsed_seconds(total_seconds: float) -> str:
    seconds = max(0.0, float(total_seconds))
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{int(round(seconds))}s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60.0
    return f"{hours:.1f}h"


def summarize_elapsed(created_at: str | None, started_at: str | None, finished_at: str | None) -> str:
    start = _parse_timestamp(started_at) or _parse_timestamp(created_at)
    end = _parse_timestamp(finished_at) or datetime.now(timezone.utc)
    if start is None:
        return ""
    return f"Elapsed: {_format_elapsed_seconds((end - start).total_seconds())}"


def summarize_background_sessions(process_manager: Any, owner_session_id: str | None) -> str:
    if process_manager is None or not owner_session_id:
        return ""
    try:
        sessions = process_manager.list_sessions(tail_chars=0, owner_session_id=owner_session_id)
    except Exception:
        return ""

    running_ids = [
        str(session.get("session_id") or "").strip()
        for session in sessions
        if str(session.get("status") or "").strip().lower() == "running"
    ]
    running_ids = [session_id for session_id in running_ids if session_id]
    if not running_ids:
        return ""
    return f"Background: {', '.join(running_ids[:3])}"


def summarize_warnings(tool_events: list[dict] | None, error: str | None = None) -> str:
    warnings: list[str] = []
    if str(error or "").strip():
        warnings.append("run error")

    for event in tool_events or []:
        if event.get("phase") != "end":
            continue
        text = str(event.get("result") or "").lower()
        if "timeout" in text and "timeout" not in warnings:
            warnings.append("timeout")
        if "fallback" in text and "fallback" not in warnings:
            warnings.append("fallback")

    if not warnings:
        return ""
    return f"Warnings: {', '.join(warnings)}"


def build_response_trace(record: Any, *, process_manager: Any = None, enabled: bool = True) -> str:
    if not enabled:
        return ""

    parts = [
        summarize_tool_events(getattr(getattr(record, "result", None), "tool_events", None)),
        summarize_elapsed(
            getattr(record, "created_at", None),
            getattr(record, "started_at", None),
            getattr(record, "finished_at", None),
        ),
        summarize_background_sessions(process_manager, getattr(record, "session_id", None)),
        summarize_warnings(
            getattr(getattr(record, "result", None), "tool_events", None),
            getattr(record, "error", None),
        ),
    ]
    return "\n".join(part for part in parts if part)


def append_response_trace(text: str, record: Any, *, process_manager: Any = None, enabled: bool = True) -> str:
    summary = build_response_trace(record, process_manager=process_manager, enabled=enabled)
    if not summary:
        return text

    base = str(text or "").rstrip()
    if not base:
        return summary
    return f"{base}\n\n{summary}"


def should_publish_terminal_fallback(record: Any) -> bool:
    result = getattr(record, "result", None)
    if result is None:
        return False
    if getattr(result, "spoken_messages", None):
        return False
    if getattr(result, "submitted", False):
        return False
    if not (getattr(result, "max_turns_reached", False) or getattr(result, "doom_loop_hard_stop", False)):
        return False
    final_response = str(getattr(result, "final_response", "") or "").strip()
    if not final_response:
        return False

    metadata = getattr(record, "metadata", {}) or {}
    return bool(metadata.get("is_private") or metadata.get("mention"))


def build_terminal_fallback_response(record: Any, *, process_manager: Any = None, enabled: bool = True) -> str:
    if not should_publish_terminal_fallback(record):
        return ""
    final_response = str(getattr(getattr(record, "result", None), "final_response", "") or "").strip()
    if not final_response:
        return ""
    return append_response_trace(final_response, record, process_manager=process_manager, enabled=enabled)
