import pytest

from overmindagent.common.config import LLMSettings
from overmindagent.llm import LLMSessionFactory, MissingLLMConfigurationError
from overmindagent.llm.adapters import OpenAIChatSession, OpenAIResponsesSession
from overmindagent.llm.schemas import LLMMessage, LLMRequest
from overmindagent.schemas.analysis import StructuredTextAnalysis


def test_llm_factory_requires_api_key() -> None:
    factory = LLMSessionFactory(LLMSettings(provider="openai", protocol="responses"))
    session = factory.create()

    with pytest.raises(MissingLLMConfigurationError):
        session._create_client()


def test_llm_factory_creates_responses_session() -> None:
    factory = LLMSessionFactory(
        LLMSettings(
            api_key="test-key",
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
            api_key="test-key",
            provider="openai",
            protocol="chat",
            model="gpt-test",
        )
    )

    session = factory.create()

    assert isinstance(session, OpenAIChatSession)


def test_chat_session_uses_json_object_for_structured_output() -> None:
    session = OpenAIChatSession(
        settings=LLMSettings(
            api_key="test-key",
            provider="openai",
            protocol="chat",
            model="gpt-test",
            temperature=0.0,
            timeout=30,
            max_tokens=None,
            provider_options={},
        )
    )

    params = session._build_create_params(
        LLMRequest(
            system_prompt="Analyze text precisely.",
            messages=[LLMMessage(role="user", content="hello world")],
            response_schema=StructuredTextAnalysis,
        )
    )

    assert params["response_format"] == {"type": "json_object"}
    assert "matches this JSON Schema exactly" in params["messages"][0]["content"]
