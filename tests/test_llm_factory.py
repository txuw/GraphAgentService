import pytest
from pydantic import SecretStr

from overmindagent.common.config import LLMSettings
from overmindagent.llm import LLMSessionFactory, MissingLLMConfigurationError
from overmindagent.llm.adapters import OpenAIChatSession, OpenAIResponsesSession


def test_llm_factory_requires_api_key() -> None:
    factory = LLMSessionFactory(LLMSettings())
    session = factory.create()

    with pytest.raises(MissingLLMConfigurationError):
        session._create_client()


def test_llm_factory_creates_responses_session() -> None:
    factory = LLMSessionFactory(
        LLMSettings(
            api_key=SecretStr("test-key"),
            provider="openai",
            protocol="responses",
            base_url="https://example.com/v1",
            model="gpt-test",
            temperature=0.2,
            timeout=30,
            max_tokens=256,
        )
    )

    session = factory.create()

    assert isinstance(session, OpenAIResponsesSession)


def test_llm_factory_creates_chat_session() -> None:
    factory = LLMSessionFactory(
        LLMSettings(
            api_key=SecretStr("test-key"),
            provider="openai",
            protocol="chat",
            model="gpt-test",
        )
    )

    session = factory.create()

    assert isinstance(session, OpenAIChatSession)
