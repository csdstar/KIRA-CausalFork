from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from kiraclaw_agentd.api import create_app
from kiraclaw_agentd.engine import RunResult


def test_runs_endpoint_returns_serializable_payload(monkeypatch) -> None:
    app = create_app()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    async def fake_run(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            run_id="run-123",
            session_id="desktop:test",
            state="completed",
            result=RunResult(
                final_response="final",
                streamed_text="",
                tool_events=[],
                spoken_messages=["spoken"],
            ),
            error=None,
        )

    monkeypatch.setattr(app.state.session_manager, "run", fake_run)

    with TestClient(app) as client:
        response = client.post(
            "/v1/runs",
            json={
                "session_id": "desktop:test",
                "prompt": "hello",
                "provider": "openai",
                "model": "gpt-5.2",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-123",
        "session_id": "desktop:test",
        "state": "completed",
        "internal_summary": "final",
        "final_response": "final",
        "spoken_messages": ["spoken"],
        "streamed_text": "",
        "tool_events": [],
        "error": None,
    }
