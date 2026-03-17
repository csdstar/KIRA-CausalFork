from __future__ import annotations

from typing import Any

from kiraclaw_agentd.memory_store import MemoryStore


class MemoryRetriever:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def build_context(
        self,
        prompt: str,
        session_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        return self.store.retrieve_context(prompt, session_id, metadata)
