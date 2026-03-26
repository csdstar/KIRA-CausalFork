from __future__ import annotations

from types import SimpleNamespace

from kiraclaw_agentd.observer_service import ObserverService
from kiraclaw_agentd.settings import KiraClawSettings


class _FakeModel:
    def __init__(self, text: str) -> None:
        self._text = text

    def chat(self, messages, tools):  # noqa: ANN001
        return SimpleNamespace(text=self._text, tool_calls=[], stop=True)


def test_observer_service_classifies_status_query_with_fake_model(tmp_path) -> None:
    settings = KiraClawSettings(
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        home_mode="modern",
        slack_enabled=False,
    )
    service = ObserverService(
        settings,
        model_factory=lambda provider, model, max_tokens: _FakeModel(
            '{"intent":"status_query","reply_text":"로컬 서버를 띄운 뒤 브라우저를 확인하는 중입니다."}'
        ),
        credential_checker=lambda settings, provider: None,
    )

    decision = service.classify_inflight(
        "지금 어디까지 했어?",
        {
            "session_id": "slack:C1:main",
            "state": "running",
            "elapsed_seconds": 12,
            "prompt": "로컬 html을 확인해줘",
            "streamed_text_tail": "loading localhost",
            "recent_tool_events": [{"phase": "start", "name": "browser_navigate"}],
            "active_processes": [],
        },
    )

    assert decision.intent == "status_query"
    assert "브라우저" in decision.reply_text


def test_observer_service_falls_back_when_model_output_is_invalid(tmp_path) -> None:
    settings = KiraClawSettings(
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        home_mode="modern",
        slack_enabled=False,
    )
    service = ObserverService(
        settings,
        model_factory=lambda provider, model, max_tokens: _FakeModel("not json"),
        credential_checker=lambda settings, provider: None,
    )

    decision = service.classify_inflight(
        "멈춰",
        {
            "session_id": "slack:C1:main",
            "state": "running",
            "elapsed_seconds": 5,
            "prompt": "작업 중",
            "streamed_text_tail": "",
            "recent_tool_events": [],
            "active_processes": [],
        },
    )

    assert decision.intent == "unsupported_control"
    assert "취소" in decision.reply_text or "수정" in decision.reply_text


def test_observer_service_falls_back_when_model_call_raises(tmp_path) -> None:
    settings = KiraClawSettings(
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        home_mode="modern",
        slack_enabled=False,
    )

    def raising_factory(_provider, _model, _max_tokens):
        class _BrokenModel:
            def chat(self, messages, tools):  # noqa: ANN001
                raise RuntimeError("observer unavailable")

        return _BrokenModel()

    service = ObserverService(
        settings,
        model_factory=raising_factory,
        credential_checker=lambda settings, provider: None,
    )

    decision = service.classify_inflight(
        "지금 어디까지 했어?",
        {
            "session_id": "slack:C1:main",
            "state": "running",
            "elapsed_seconds": 7,
            "prompt": "브라우저 확인",
            "streamed_text_tail": "loading localhost",
            "recent_tool_events": [{"phase": "start", "name": "browser_navigate"}],
            "active_processes": [],
        },
    )

    assert decision.intent == "status_query"
    assert "browser_navigate" in decision.reply_text
