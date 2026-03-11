from overmindagent.config import get_settings


def test_settings_can_read_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("OVERMIND_APP_ENV", "test")
    monkeypatch.setenv("OVERMIND_PORT", "9000")

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.app_env == "test"
    assert settings.port == 9000

    get_settings.cache_clear()
