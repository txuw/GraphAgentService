from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from graphagentservice.common.auth import AuthenticatedUser
from graphagentservice.common.trace import TRACE_ID_HEADER
from graphagentservice.llm.router import LLMRouter

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from graphagentservice.mcp import MCPToolResolver


class ToolEventEmitterProtocol:
    """
    Structural protocol for the tool stream event emitter.

    Graph nodes depend on this interface only – they never import the concrete
    implementation, keeping the graphs package free of services imports.
    """

    async def emit_start(self, tool_name: str) -> None: ...

    async def emit_done(self, tool_name: str) -> None: ...

    async def emit_error(self, tool_name: str, error_message: str) -> None: ...


@dataclass(frozen=True, slots=True)
class GraphRunContext:
    llm_router: LLMRouter
    graph_name: str
    llm_bindings: Mapping[str, str] = field(default_factory=dict)
    current_user: AuthenticatedUser = field(default_factory=AuthenticatedUser.anonymous)
    trace_id: str = ""
    request_headers: Mapping[str, str] = field(default_factory=dict)
    mcp_tool_resolver: MCPToolResolver | None = None
    mcp_servers: tuple[str, ...] = ()
    tool_stream_emitter: ToolEventEmitterProtocol | None = None

    def resolve_model(
        self,
        *,
        binding: str | None = None,
        profile: str | None = None,
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ):
        resolved_profile_name = profile
        if resolved_profile_name is None and binding is not None:
            resolved_profile_name = self.llm_bindings.get(binding, binding)

        resolved_profile = self.llm_router.resolve_profile(resolved_profile_name)
        base_tags = [
            f"graph:{self.graph_name}",
            f"profile:{resolved_profile.name}",
        ]
        if binding:
            base_tags.append(f"binding:{binding}")
        base_tags.extend(tags)

        base_metadata = {
            "graph_name": self.graph_name,
            "profile": resolved_profile.name,
            "trace_id": self.trace_id,
        }
        if binding:
            base_metadata["binding"] = binding
        if metadata:
            base_metadata.update(dict(metadata))

        default_headers = None
        if self.trace_id:
            default_headers = {TRACE_ID_HEADER: self.trace_id}

        return self.llm_router.create_model(
            profile=resolved_profile.name,
            tags=tuple(base_tags),
            metadata=base_metadata,
            default_headers=default_headers,
        )

    def structured_model(
        self,
        *,
        schema: type[BaseModel],
        binding: str | None = None,
        profile: str | None = None,
        method: str = "json_schema",
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ):
        model = self.resolve_model(
            binding=binding,
            profile=profile,
            tags=tags,
            metadata=metadata,
        )
        return model.with_structured_output(schema, method=method, **kwargs)

    def structured_model_with_json_object(
        self,
        *,
        schema: type[BaseModel],
        binding: str | None = None,
        profile: str | None = None,
        method: str = "json_object",
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ):
        resolved_profile = self._resolve_profile(binding=binding, profile=profile)
        model = self.resolve_model(
            binding=binding,
            profile=profile,
            tags=tags,
            metadata=metadata,
        )
        bind_kwargs = dict(kwargs)
        if _supports_json_schema_response_format(resolved_profile):
            # json_schema 模式：将完整 Pydantic schema 下发给模型，
            # Gemini 等支持此模式的模型会严格按 schema 生成 JSON，
            # 比 json_object 更可靠，尤其是多模态（图片）请求。
            bind_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": False,
                },
            }
        elif _supports_json_object_response_format(resolved_profile):
            bind_kwargs["response_format"] = {"type": "json_object"}
        return model.bind(**bind_kwargs) | RunnableLambda(
            partial(_validate_json_object_response, schema=schema)
        )

    def _resolve_profile(
        self,
        *,
        binding: str | None = None,
        profile: str | None = None,
    ):
        resolved_profile_name = profile
        if resolved_profile_name is None and binding is not None:
            resolved_profile_name = self.llm_bindings.get(binding, binding)
        return self.llm_router.resolve_profile(resolved_profile_name)

    def image_model(
        self,
        *,
        binding: str | None = None,
        profile: str | None = None,
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ):
        return self.resolve_model(
            binding=binding,
            profile=profile,
            tags=tags,
            metadata=metadata,
        )

    def tool_model(
        self,
        *,
        tools: Sequence[Any],
        binding: str | None = None,
        profile: str | None = None,
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ):
        model = self.resolve_model(
            binding=binding,
            profile=profile,
            tags=tags,
            metadata=metadata,
        )
        return model.bind_tools(tools, **kwargs)


@dataclass(frozen=True, slots=True)
class GraphRuntime:
    name: str
    description: str
    graph: Any
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    llm_bindings: Mapping[str, str] = field(default_factory=dict)
    mcp_servers: tuple[str, ...] = ()
    stream_modes: tuple[str, ...] = ("updates", "messages", "custom", "values")


def _validate_json_object_response(response: Any, *, schema: type[BaseModel]) -> BaseModel:
    return schema.model_validate_json(_normalize_json_payload(_response_to_text(response)))


def _response_to_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "".join(parts).strip()
    return str(content).strip()


def _normalize_json_payload(payload: str) -> str:
    candidate = payload.strip()

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1]).strip()
            # Fall through to check if the unwrapped content is already valid JSON

    # Already looks like a JSON object or array
    if candidate.startswith(("{", "[")):
        return candidate

    # Model returned natural language with (or without) embedded JSON.
    # Try to extract the outermost { ... } or [ ... ] block.
    for open_char, close_char in (("{", "}"), ("[", "]")):
        start = candidate.find(open_char)
        if start == -1:
            continue
        end = candidate.rfind(close_char)
        if end > start:
            extracted = candidate[start : end + 1].strip()
            logger.warning(
                "LLM returned non-JSON response; extracted JSON fragment from prose. "
                "Full response prefix: %.120s",
                candidate,
            )
            return extracted

    # No JSON found – return as-is so that downstream validation reports the error clearly
    return candidate


# 支持 response_format: json_schema 的模型前缀（含 LiteLLM 路由前缀）
_JSON_SCHEMA_FORMAT_ALLOWLIST = frozenset({"gemini"})

# 不支持 response_format: json_object 的模型前缀
_JSON_OBJECT_FORMAT_BLOCKLIST = frozenset({"doubao"})


def _supports_json_schema_response_format(profile: Any) -> bool:
    """Return True for models that support ``response_format: json_schema``.

    Gemini（via LiteLLM OpenAI 兼容接口）原生支持 json_schema 模式，
    可将完整 Pydantic schema 下发，比 json_object 更可靠，尤其在多模态场景。
    """
    model_name = str(getattr(profile, "model", "")).lower()
    return any(prefix in model_name for prefix in _JSON_SCHEMA_FORMAT_ALLOWLIST)


def _supports_json_object_response_format(profile: Any) -> bool:
    """Return True only for models known to honour ``response_format: json_object``."""
    model_name = str(getattr(profile, "model", "")).lower()
    return not any(blocked in model_name for blocked in _JSON_OBJECT_FORMAT_BLOCKLIST)
