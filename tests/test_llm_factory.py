import pytest
from pydantic import SecretStr

from overmindagent.common.config import LLMSettings
from overmindagent.llm import LLMModelFactory, MissingLLMConfigurationError


def test_llm_factory_requires_api_key() -> None:
    factory = LLMModelFactory(LLMSettings())

    with pytest.raises(MissingLLMConfigurationError):
        factory.create_chat_model()


def test_llm_factory_creates_chat_model_with_base_url(monkeypatch) -> None:
    for env_name in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
        monkeypatch.delenv(env_name, raising=False)

    factory = LLMModelFactory(
        LLMSettings(
            api_key=SecretStr("test-key"),
            base_url="https://example.com/v1",
            model="gpt-test",
            temperature=0.2,
            timeout=30,
            max_tokens=256,
        )
    )

    model = factory.create_chat_model()

    assert model.model_name == "gpt-test"
