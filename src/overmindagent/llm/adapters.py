from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from overmindagent.common.config import LLMSettings

from .schemas import LLMEvent, LLMEventType, LLMRequest, LLMResponse, ToolCall
from .session import LLMSession
from .tools import ToolRegistry

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class MissingLLMConfigurationError(RuntimeError):
    pass


class UnsupportedLLMConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_stream: bool
    supports_tools: bool
    supports_structured_output: bool


class BaseOpenAISession(LLMSession):
    provider_name = "openai"

    def __init__(
        self,
        settings: LLMSettings,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._tool_registry = tool_registry or ToolRegistry()

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        client = self._create_client()
        return await self._invoke_with_client(client, request)

    def _create_client(self) -> AsyncOpenAI:
        api_key = self._settings.api_key.get_secret_value() if self._settings.api_key else None
        if not api_key:
            raise MissingLLMConfigurationError(
                "Missing OVERMIND_LLM_API_KEY for LLM invocation."
            )

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise MissingLLMConfigurationError(
                "The openai package is required. Run `uv sync` to install dependencies."
            ) from exc

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": self._settings.timeout,
        }
        if self._settings.base_url:
            kwargs["base_url"] = self._settings.base_url
        return AsyncOpenAI(**kwargs)

    @staticmethod
    def _build_input_messages(request: LLMRequest) -> list[dict[str, Any]]:
        input_messages: list[dict[str, Any]] = []
        if request.system_prompt:
            input_messages.append({"role": "system", "content": request.system_prompt})
        for message in request.messages:
            input_messages.append({"role": message.role, "content": message.content})
        return input_messages

    @staticmethod
    def _build_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
        return {
            "type": "json_schema",
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
            "strict": True,
        }

    def _merge_options(self, request: LLMRequest) -> dict[str, Any]:
        options: dict[str, Any] = {
            "model": self._settings.model,
        }
        temperature = self._settings.temperature if request.temperature is None else request.temperature
        if temperature is not None:
            options["temperature"] = temperature
        max_tokens = self._settings.max_tokens if request.max_tokens is None else request.max_tokens
        if max_tokens is not None:
            options["max_output_tokens"] = max_tokens
        options.update(self._settings.provider_options)
        options.update(request.provider_options)
        return options

    async def _invoke_with_client(self, client: AsyncOpenAI, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError


class OpenAIResponsesSession(BaseOpenAISession):
    async def _invoke_with_client(self, client: AsyncOpenAI, request: LLMRequest) -> LLMResponse:
        response = await client.responses.create(**self._build_create_params(request))
        return self._parse_response_payload(response, request)

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        client = self._create_client()
        params = self._build_create_params(request)
        async with client.responses.stream(**params) as stream:
            async for event in stream:
                mapped = self._map_stream_event(event)
                if mapped is not None:
                    yield mapped

            final_response = await stream.get_final_response()
            for tool_event in await self._run_tool_loop(client, final_response, request):
                yield tool_event

            # Downstream code can reuse the provider response to avoid a second LLM call.
            yield LLMEvent(
                type=LLMEventType.COMPLETED,
                raw=final_response,
                extensions={"response_id": getattr(final_response, "id", None)},
            )

    def _build_create_params(self, request: LLMRequest) -> dict[str, Any]:
        params = self._merge_options(request)
        params["input"] = self._build_input_messages(request)
        params["parallel_tool_calls"] = self._settings.parallel_tool_calls
        if request.tools:
            params["tools"] = [self._tool_to_openai(tool) for tool in request.tools]
        if request.response_schema is not None:
            params["text"] = {"format": self._build_json_schema(request.response_schema)}
        return params

    def _parse_response_payload(self, response: Any, request: LLMRequest) -> LLMResponse:
        tool_calls = self._extract_tool_calls(getattr(response, "output", []))
        structured = self._parse_structured_output(getattr(response, "output_text", ""), request.response_schema)
        return LLMResponse(
            text=getattr(response, "output_text", "") or "",
            structured=structured,
            tool_calls=tool_calls,
            usage=self._usage_to_dict(getattr(response, "usage", None)),
            finish_reason=getattr(response, "status", None),
            provider_name=self.provider_name,
            model=self._settings.model,
            raw=response,
            extensions={"response_id": getattr(response, "id", None)},
        )

    async def _run_tool_loop(
        self,
        client: AsyncOpenAI,
        response: Any,
        request: LLMRequest,
    ) -> list[LLMEvent]:
        events: list[LLMEvent] = []
        tool_calls = self._extract_tool_calls(getattr(response, "output", []))
        if not tool_calls:
            return events

        follow_up_items: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            events.append(LLMEvent(type=LLMEventType.TOOL_CALL, tool_call=tool_call, raw=response))
            tool_result = await self._tool_registry.execute(tool_call)
            events.append(LLMEvent(type=LLMEventType.TOOL_RESULT, tool_result=tool_result, raw=response))
            # Responses API continues tool flows by feeding function outputs back as new input items.
            follow_up_items.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call.id,
                    "output": tool_result.output,
                }
            )

        if not follow_up_items:
            return events

        rounds = 1
        next_response = response
        while follow_up_items and rounds <= self._settings.max_tool_rounds:
            next_response = await client.responses.create(
                **self._merge_options(request),
                input=follow_up_items,
                previous_response_id=getattr(next_response, "id", None),
            )
            follow_up_items = []
            for tool_call in self._extract_tool_calls(getattr(next_response, "output", [])):
                events.append(LLMEvent(type=LLMEventType.TOOL_CALL, tool_call=tool_call, raw=next_response))
                tool_result = await self._tool_registry.execute(tool_call)
                events.append(
                    LLMEvent(type=LLMEventType.TOOL_RESULT, tool_result=tool_result, raw=next_response)
                )
                follow_up_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call.id,
                        "output": tool_result.output,
                    }
                )
            rounds += 1

        if follow_up_items:
            events.append(
                LLMEvent(
                    type=LLMEventType.ERROR,
                    raw=next_response,
                    extensions={"message": "Tool execution exceeded max_tool_rounds."},
                )
            )
        return events

    @staticmethod
    def _map_stream_event(event: Any) -> LLMEvent | None:
        event_type = getattr(event, "type", "")
        if event_type == "response.output_text.delta":
            return LLMEvent(type=LLMEventType.TEXT_DELTA, text_delta=getattr(event, "delta", ""), raw=event)
        if event_type == "response.error":
            return LLMEvent(
                type=LLMEventType.ERROR,
                raw=event,
                extensions={"message": getattr(event, "message", "LLM stream error")},
            )
        return None

    @staticmethod
    def _tool_to_openai(tool: Any) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }

    @staticmethod
    def _extract_tool_calls(output: Any) -> list[ToolCall]:
        tool_calls: list[ToolCall] = []
        for item in output or []:
            if getattr(item, "type", None) != "function_call":
                continue
            arguments = getattr(item, "arguments", "{}")
            tool_calls.append(
                ToolCall(
                    id=getattr(item, "call_id", getattr(item, "id", "")),
                    name=getattr(item, "name", ""),
                    arguments=json.loads(arguments) if isinstance(arguments, str) else arguments,
                )
            )
        return tool_calls

    @staticmethod
    def _parse_structured_output(text: str, schema: type[BaseModel] | None) -> BaseModel | None:
        if schema is None:
            return None
        payload = json.loads(text or "{}")
        return schema.model_validate(payload)

    @staticmethod
    def _usage_to_dict(usage: Any) -> dict[str, Any]:
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        return dict(usage)


class OpenAIChatSession(BaseOpenAISession):
    async def _invoke_with_client(self, client: AsyncOpenAI, request: LLMRequest) -> LLMResponse:
        completion = await client.chat.completions.create(**self._build_create_params(request))
        message = completion.choices[0].message
        content = self._extract_chat_content(message)
        structured = self._parse_structured_output(content, request.response_schema)
        return LLMResponse(
            text=content,
            structured=structured,
            tool_calls=self._extract_chat_tool_calls(message),
            usage=self._usage_to_dict(getattr(completion, "usage", None)),
            finish_reason=getattr(completion.choices[0], "finish_reason", None),
            provider_name=self.provider_name,
            model=self._settings.model,
            raw=completion,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        client = self._create_client()
        stream = await client.chat.completions.create(**self._build_create_params(request), stream=True)
        async for chunk in stream:
            choice = chunk.choices[0]
            delta = getattr(choice.delta, "content", None)
            if delta:
                yield LLMEvent(type=LLMEventType.TEXT_DELTA, text_delta=delta, raw=chunk)
        yield LLMEvent(type=LLMEventType.COMPLETED, raw=None)

    def _build_create_params(self, request: LLMRequest) -> dict[str, Any]:
        params = self._merge_options(request)
        params["messages"] = self._build_input_messages(request)
        if request.tools:
            params["tools"] = [self._tool_to_openai(tool) for tool in request.tools]
        if request.response_schema is not None:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": self._build_json_schema(request.response_schema),
            }
        return params

    @staticmethod
    def _extract_chat_content(message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Some SDKs return segmented content blocks instead of a single string.
            return "".join(
                item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
            )
        return ""

    @staticmethod
    def _extract_chat_tool_calls(message: Any) -> list[ToolCall]:
        tool_calls: list[ToolCall] = []
        for item in getattr(message, "tool_calls", []) or []:
            arguments = getattr(item.function, "arguments", "{}")
            tool_calls.append(
                ToolCall(
                    id=getattr(item, "id", ""),
                    name=getattr(item.function, "name", ""),
                    arguments=json.loads(arguments) if isinstance(arguments, str) else arguments,
                )
            )
        return tool_calls

    @staticmethod
    def _tool_to_openai(tool: Any) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    @staticmethod
    def _parse_structured_output(text: str, schema: type[BaseModel] | None) -> BaseModel | None:
        if schema is None:
            return None
        payload = json.loads(text or "{}")
        return schema.model_validate(payload)

    @staticmethod
    def _usage_to_dict(usage: Any) -> dict[str, Any]:
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        return dict(usage)


class ProviderAdapter:
    provider_name: str
    protocol_name: str
    capabilities: ProviderCapabilities

    def build_session(
        self,
        settings: LLMSettings,
        tool_registry: ToolRegistry | None = None,
    ) -> LLMSession:
        raise NotImplementedError


class OpenAIResponsesAdapter(ProviderAdapter):
    provider_name = "openai"
    protocol_name = "responses"
    capabilities = ProviderCapabilities(
        supports_stream=True,
        supports_tools=True,
        supports_structured_output=True,
    )

    def build_session(
        self,
        settings: LLMSettings,
        tool_registry: ToolRegistry | None = None,
    ) -> LLMSession:
        return OpenAIResponsesSession(settings=settings, tool_registry=tool_registry)


class OpenAIChatAdapter(ProviderAdapter):
    provider_name = "openai"
    protocol_name = "chat"
    capabilities = ProviderCapabilities(
        supports_stream=True,
        supports_tools=True,
        supports_structured_output=True,
    )

    def build_session(
        self,
        settings: LLMSettings,
        tool_registry: ToolRegistry | None = None,
    ) -> LLMSession:
        return OpenAIChatSession(settings=settings, tool_registry=tool_registry)
