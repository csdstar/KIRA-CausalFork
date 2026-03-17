from __future__ import annotations

from kiraclaw_agentd.memory_models import MemoryWriteRequest
from kiraclaw_agentd.memory_store import MemoryStore


class MemorySaver:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def save(self, request: MemoryWriteRequest) -> None:
        self.store.save_exchange(request)
