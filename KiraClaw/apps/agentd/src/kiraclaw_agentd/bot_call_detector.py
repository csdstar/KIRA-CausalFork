from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BotCallDecision:
    should_respond: bool
    cleaned_text: str = ""
    reason: str = "ignore"
    direct_name_call: bool = False


def normalize_text(text: str) -> str:
    return " ".join(str(text or "").split())


def matches_exact_name_at_start(text: str, agent_name: str | None) -> bool:
    name = str(agent_name or "").strip()
    if not name:
        return False
    stripped = str(text or "").lstrip()
    if stripped[: len(name)].lower() != name.lower():
        return False
    if len(stripped) == len(name):
        return True
    next_char = stripped[len(name)]
    return next_char.isspace() or next_char in ",.:;!?)]}>'\"”’~-"


def strip_exact_name_at_start(text: str, agent_name: str | None) -> str:
    if not matches_exact_name_at_start(text, agent_name):
        return normalize_text(text)
    name = str(agent_name or "").strip()
    stripped = str(text or "").lstrip()[len(name) :]
    stripped = stripped.lstrip(" \t\r\n,.:;!?-–—")
    return normalize_text(stripped)


class BotCallDetector:
    def __init__(self, agent_name: str | None) -> None:
        self.agent_name = str(agent_name or "").strip()

    def detect_slack(
        self,
        *,
        text: str,
        is_dm: bool,
        mention: bool,
        subtype: str | None = None,
        bot_id: str | None = None,
    ) -> BotCallDecision:
        if subtype or bot_id:
            return BotCallDecision(False, reason="system_message")
        if is_dm:
            return BotCallDecision(True, cleaned_text=normalize_text(text), reason="dm")
        if mention:
            return BotCallDecision(True, cleaned_text=normalize_text(text), reason="mention")
        if matches_exact_name_at_start(text, self.agent_name):
            return BotCallDecision(
                True,
                cleaned_text=strip_exact_name_at_start(text, self.agent_name),
                reason="direct_name_call",
                direct_name_call=True,
            )
        return BotCallDecision(False, reason="group_noise")

    def detect_telegram(
        self,
        *,
        text: str,
        is_private_chat: bool,
        mention: bool,
        reply_to_bot: bool,
        sender_is_bot: bool,
    ) -> BotCallDecision:
        if sender_is_bot:
            return BotCallDecision(False, reason="system_message")
        if is_private_chat:
            return BotCallDecision(True, cleaned_text=normalize_text(text), reason="dm")
        if reply_to_bot:
            return BotCallDecision(True, cleaned_text=normalize_text(text), reason="reply")
        if mention:
            return BotCallDecision(True, cleaned_text=normalize_text(text), reason="mention")
        if matches_exact_name_at_start(text, self.agent_name):
            return BotCallDecision(
                True,
                cleaned_text=strip_exact_name_at_start(text, self.agent_name),
                reason="direct_name_call",
                direct_name_call=True,
            )
        return BotCallDecision(False, reason="group_noise")
