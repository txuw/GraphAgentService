from pathlib import Path
from textwrap import dedent

from overmindagent.config import get_settings

CONFIG_ENV_KEYS = (
    "APP__NAME",
    "APP__ENV",
    "APP__HOST",
    "APP__PORT",
    "APP__RELOAD",
    "APP__LOG_LEVEL",
    "LLM__API_KEY",
    "LLM__BASE_URL",
    "LLM__MODEL",
    "LLM__PROVIDER",
    "LLM__PROTOCOL",
    "GRAPH__CHECKPOINT_MODE",
    "OBSERVABILITY__LOG_PAYLOADS",
    "DATABASE__HOST",
)


def _write_settings_yaml(directory: Path) -> None:
    (directory / "settings.yaml").write_text(
        dedent(
            """\
            app:
              name: TestAgent
              env: yaml
              host: 127.0.0.1
              port: 8100
              reload: false
              log_level: warning
            llm:
              model: yaml-model
            graph:
              checkpoint_mode: memory
            observability:
              log_payloads: false
            """
        ),
        encoding="utf-8",
    )


def _clear_config_env(monkeypatch) -> None:
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_settings_can_read_yaml_defaults(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    _write_settings_yaml(tmp_path)
    monkeypatch.chdir(tmp_path)

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.app.env == "yaml"
    assert settings.app.port == 8100
    assert settings.llm.model == "yaml-model"
    assert settings.graph.checkpoint_mode == "memory"

    get_settings.cache_clear()


def test_dotenv_can_override_yaml_with_nested_keys(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    _write_settings_yaml(tmp_path)
    (tmp_path / ".env").write_text(
        dedent(
            """\
            APP__ENV=dotenv
            APP__PORT=9000
            LLM__MODEL=dotenv-model
            GRAPH__CHECKPOINT_MODE=disabled
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.app.env == "dotenv"
    assert settings.app.port == 9000
    assert settings.llm.model == "dotenv-model"
    assert settings.graph.checkpoint_mode == "disabled"

    get_settings.cache_clear()


def test_environment_variables_override_dotenv(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    _write_settings_yaml(tmp_path)
    (tmp_path / ".env").write_text(
        dedent(
            """\
            APP__ENV=dotenv
            APP__PORT=9000
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP__ENV", "system")
    monkeypatch.setenv("APP__PORT", "9100")

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.app.env == "system"
    assert settings.app.port == 9100

    get_settings.cache_clear()


def test_settings_support_dynamic_sections_without_model_changes(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    _write_settings_yaml(tmp_path)
    (tmp_path / "settings.yaml").write_text(
        (tmp_path / "settings.yaml").read_text(encoding="utf-8")
        + dedent(
            """\

            database:
              driver: postgresql
              host: localhost
              port: 5432
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE__HOST", "127.0.0.1")

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.database.driver == "postgresql"
    assert settings.database.host == "127.0.0.1"
    assert settings.database.port == 5432

    get_settings.cache_clear()
