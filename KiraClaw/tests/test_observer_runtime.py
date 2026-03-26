from __future__ import annotations

import asyncio

from kiraclaw_agentd.observer_runtime import maybe_route_inflight_message, run_heartbeat_loop
from kiraclaw_agentd.observer_service import ObserverDecision


class _FakeObserverService:
    def classify_inflight(self, prompt: str, snapshot: dict) -> ObserverDecision:
        if "어디까지" in prompt:
            return ObserverDecision("status_query", "지금 상태를 보고 있습니다.")
        return ObserverDecision("queue_next", "끝난 뒤 이어서 처리할게요.")

    def summarize_heartbeat(self, snapshot: dict) -> str:
        return f"heartbeat:{snapshot['state']}"


class _FakeSessionManager:
    def __init__(self, *, active: bool = True) -> None:
        self.active = active
        self.snapshots = [
            {"session_id": "slack:C1:main", "state": "running"},
            {"session_id": "slack:C1:main", "state": "running"},
        ]

    def has_active_run(self, session_id: str) -> bool:
        return self.active and session_id == "slack:C1:main"

    def build_observer_snapshot(self, session_id: str) -> dict | None:
        if not self.has_active_run(session_id):
            return None
        if self.snapshots:
            return self.snapshots.pop(0)
        return {"session_id": session_id, "state": "running"}


def test_maybe_route_inflight_message_returns_observer_decision() -> None:
    async def scenario() -> None:
        decision = await maybe_route_inflight_message(
            _FakeSessionManager(),
            _FakeObserverService(),
            session_id="slack:C1:main",
            prompt="지금 어디까지 했어?",
        )
        assert decision is not None
        assert decision.intent == "status_query"

    asyncio.run(scenario())


def test_run_heartbeat_loop_sends_updates_until_run_finishes() -> None:
    async def scenario() -> None:
        sent: list[str] = []

        async def send_update(text: str) -> None:
            sent.append(text)

        async def _finish() -> str:
            await asyncio.sleep(0.08)
            return "done"

        run_task = asyncio.create_task(_finish())
        await run_heartbeat_loop(
            _FakeSessionManager(),
            _FakeObserverService(),
            session_id="slack:C1:main",
            run_task=run_task,
            send_update=send_update,
            initial_delay_seconds=0.01,
            interval_seconds=0.02,
        )

        assert sent
        assert sent[0] == "heartbeat:running"

    asyncio.run(scenario())
