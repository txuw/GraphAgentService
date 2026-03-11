from overmindagent.config import get_settings


def test_settings_can_read_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("OVERMIND_APP_ENV", "test")
    monkeypatch.setenv("OVERMIND_PORT", "9000")
    monkeypatch.setenv("OVERMIND_LLM_MODEL", "test-model")
    monkeypatch.setenv("OVERMIND_GRAPH_CHECKPOINT_MODE", "disabled")

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.app_env == "test"
    assert settings.port == 9000
    assert settings.llm.model == "test-model"
    assert settings.graph.checkpoint_mode == "disabled"

    get_settings.cache_clear()
