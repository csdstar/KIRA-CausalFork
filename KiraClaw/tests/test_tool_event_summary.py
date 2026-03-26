from __future__ import annotations

from kiraclaw_agentd.engine import RunResult
from kiraclaw_agentd.session_manager import RunRecord
from kiraclaw_agentd.tool_event_summary import (
    append_response_trace,
    build_response_trace,
    build_terminal_fallback_response,
    should_publish_terminal_fallback,
)


class _FakeProcessManager:
    def list_sessions(self, *, tail_chars: int | None = None, owner_session_id: str | None = None) -> list[dict[str, object]]:
        assert tail_chars == 0
        assert owner_session_id == "slack:C1:main"
        return [
            {"session_id": "proc_running", "status": "running"},
            {"session_id": "proc_done", "status": "completed"},
        ]


def _build_record() -> RunRecord:
    return RunRecord(
        run_id="run-1",
        session_id="slack:C1:main",
        state="completed",
        prompt="테스트 실행해줘",
        created_at="2026-03-26T00:00:00Z",
        started_at="2026-03-26T00:00:01Z",
        finished_at="2026-03-26T00:00:13Z",
        result=RunResult(
            final_response="done",
            streamed_text="working",
            tool_events=[
                {"phase": "start", "name": "exec", "args": {"command": "pytest"}},
                {"phase": "end", "name": "exec", "result": "timeout fallback used"},
                {"phase": "start", "name": "read", "args": {"path": "README.md"}},
                {"phase": "end", "name": "read", "result": "ok"},
            ],
            spoken_messages=["완료했습니다."],
        ),
    )


def test_build_response_trace_includes_expected_sections() -> None:
    trace = build_response_trace(_build_record(), process_manager=_FakeProcessManager(), enabled=True)

    assert "Used: exec, read" in trace
    assert "Elapsed: 12s" in trace
    assert "Background: proc_running" in trace
    assert "Warnings: timeout, fallback" in trace


def test_append_response_trace_is_noop_when_disabled() -> None:
    record = _build_record()

    assert append_response_trace("완료했습니다.", record, process_manager=_FakeProcessManager(), enabled=False) == "완료했습니다."


def test_append_response_trace_appends_below_message() -> None:
    message = append_response_trace("완료했습니다.", _build_record(), process_manager=_FakeProcessManager(), enabled=True)

    assert message.startswith("완료했습니다.\n\nUsed: exec, read")


def test_terminal_fallback_requires_terminal_guard_and_direct_address() -> None:
    record = _build_record()
    record.result.spoken_messages = []
    record.result.submitted = False
    record.result.max_turns_reached = True
    record.metadata = {"source": "slack-group", "mention": False, "is_private": False}

    assert should_publish_terminal_fallback(record) is False

    record.metadata["mention"] = True
    assert should_publish_terminal_fallback(record) is True


def test_build_terminal_fallback_response_uses_final_response_and_trace() -> None:
    record = _build_record()
    record.result.spoken_messages = []
    record.result.final_response = "최종 정리입니다."
    record.result.submitted = False
    record.result.max_turns_reached = True
    record.metadata = {"source": "slack-dm", "mention": False, "is_private": True}

    message = build_terminal_fallback_response(record, process_manager=_FakeProcessManager(), enabled=True)

    assert message.startswith("최종 정리입니다.\n\nUsed: exec, read")
