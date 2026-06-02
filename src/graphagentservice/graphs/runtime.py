from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, ValidationError

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
        if _is_qwen_profile(resolved_profile) and "multimodal" in tags:
            repair_model = model.bind(**_qwen_json_object_bind_kwargs(bind_kwargs))
            return RunnableLambda(
                partial(
                    _qwen_multimodal_structured_response,
                    model=model,
                    repair_model=repair_model,
                    schema=schema,
                    profile_name=str(getattr(resolved_profile, "name", "")),
                    model_name=str(getattr(resolved_profile, "model", "")),
                    graph_name=self.graph_name,
                )
            )

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
        elif _is_qwen_profile(resolved_profile):
            bind_kwargs = _qwen_json_object_bind_kwargs(bind_kwargs)
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


async def _qwen_multimodal_structured_response(
    messages: Any,
    *,
    model: Any,
    repair_model: Any,
    schema: type[BaseModel],
    profile_name: str,
    model_name: str,
    graph_name: str,
) -> BaseModel:
    response = await model.ainvoke(messages)
    try:
        return _validate_json_object_response(response, schema=schema)
    except ValidationError as exc:
        logger.warning(
            "Qwen multimodal response did not validate as JSON; attempting repair. "
            "graph=%s profile=%s model=%s schema=%s error=%s",
            graph_name,
            profile_name or "-",
            model_name or "-",
            schema.__name__,
            _error_summary(exc),
        )

    repair_response = await repair_model.ainvoke(
        _build_qwen_json_repair_messages(
            response_text=_response_to_text(response),
            schema=schema,
        )
    )
    try:
        return _validate_json_object_response(repair_response, schema=schema)
    except ValidationError as exc:
        raise RuntimeError(
            "Qwen multimodal structured output repair failed "
            f"graph={graph_name} profile={profile_name or '-'} "
            f"model={model_name or '-'} schema={schema.__name__} "
            f"error={_error_summary(exc)}"
        ) from exc


def _build_qwen_json_repair_messages(
    *,
    response_text: str,
    schema: type[BaseModel],
) -> list[object]:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    return [
        SystemMessage(
            content=(
                "你是 JSON 结构化专家。只输出一个合法 JSON object，不要 Markdown，"
                "不要解释文字。必须严格符合用户给出的 schema。"
            )
        ),
        HumanMessage(
            content=(
                "请将下面的多模态视觉分析结果整理为 JSON。\n"
                "要求：保留原始视觉语义；中文文本字段使用简体中文；数值字段必须输出数字，"
                "不要带单位；缺失的列表输出 []，缺失的对象输出 {}。\n\n"
                f"JSON schema:\n{schema_json}\n\n"
                f"视觉分析结果:\n{_truncate_text(response_text)}"
            )
        ),
    ]


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
    if _is_qwen_profile(profile):
        return False
    model_name = _normalized_model_name(profile)
    return any(prefix in model_name for prefix in _JSON_SCHEMA_FORMAT_ALLOWLIST)


def _supports_json_object_response_format(profile: Any) -> bool:
    """Return True only for models known to honour ``response_format: json_object``."""
    model_name = _normalized_model_name(profile)
    return not any(blocked in model_name for blocked in _JSON_OBJECT_FORMAT_BLOCKLIST)


def _is_qwen_profile(profile: Any) -> bool:
    model_name = _normalized_model_name(profile)
    return any(part.startswith("qwen") for part in model_name.replace("-", "_").split("/"))


def _normalized_model_name(profile: Any) -> str:
    return str(getattr(profile, "model", "")).strip().lower()


def _qwen_json_object_bind_kwargs(base_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    bind_kwargs = dict(base_kwargs)
    bind_kwargs["response_format"] = {"type": "json_object"}

    extra_body = dict(bind_kwargs.get("extra_body") or {})
    extra_body["enable_thinking"] = False
    bind_kwargs["extra_body"] = extra_body
    return bind_kwargs


def _truncate_text(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _error_summary(exc: Exception, limit: int = 300) -> str:
    text = str(exc).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[+{len(text) - limit} chars]"
