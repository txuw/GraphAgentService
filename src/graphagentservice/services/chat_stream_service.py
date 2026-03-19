from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from graphagentservice.graphs.registry import GraphNotFoundError
from graphagentservice.llm import ChatModelBuildError
from graphagentservice.mcp import MCPConfigurationError, MCPToolResolutionError

from .graph_service import (
    GraphPayloadValidationError,
    GraphRequestContext,
    GraphService,
    GraphStreamEvent,
)
from .sse import SseConnectionNotFoundError, SseConnectionRegistry


@dataclass(slots=True)
class ChatStreamAccepted:
    graph_name: str
    session_id: str
    page_id: str
    request_id: str


@dataclass(slots=True)
class AdaptedSseEvent:
    event: str
    payload: dict[str, object]


class SseEventAdapter:
    def __init__(
        self,
        *,
        graph_name: str,
        session_id: str,
        page_id: str,
        request_id: str,
    ) -> None:
        self._graph_name = graph_name
        self._session_id = session_id
        self._page_id = page_id
        self._request_id = request_id
        self._has_ai_text = False

    def adapt(self, event: GraphStreamEvent) -> list[AdaptedSseEvent]:
        if event.event == "session":
            return []
        if event.event == "updates":
            return [self._to_process_event(event.data)]
        if event.event == "messages":
            return self._adapt_message_event(event.data)
        if event.event == "result":
            return self._adapt_result_event(event.data)
        if event.event == "completed":
            return [
                AdaptedSseEvent(
                    event="ai_done",
                    payload=self._base_payload(
                        stage="已完成",
                        status="completed",
                    ),
                )
            ]
        if event.event == "error":
            return [
                AdaptedSseEvent(
                    event="ai_error",
                    payload=self._base_payload(
                        stage="失败",
                        code="STREAM_ERROR",
                        message=self._extract_error_message(event.data),
                    ),
                )
            ]
        return []

    def _to_process_event(self, data: dict[str, object]) -> AdaptedSseEvent:
        node_name = self._resolve_node_name(data)
        return AdaptedSseEvent(
            event="process",
            payload=self._base_payload(
                stage=self._resolve_stage(node_name=node_name),
                code="GRAPH_NODE_UPDATED",
                message=self._node_message(node_name),
                meta={
                    "ns": list(data.get("ns", [])),
                    "node": node_name,
                },
            ),
        )

    def _adapt_message_event(self, data: dict[str, object]) -> list[AdaptedSseEvent]:
        message = data.get("message")
        if not isinstance(message, dict):
            return []

        message_type = str(message.get("type", ""))
        message_data = message.get("data")
        if not isinstance(message_data, dict):
            return []

        if self._is_ai_message_type(message_type, message_data):
            tool_calls = message_data.get("tool_calls") or []
            if isinstance(tool_calls, list) and tool_calls:
                tool_names = [
                    str(call.get("name", "")).strip()
                    for call in tool_calls
                    if isinstance(call, dict) and str(call.get("name", "")).strip()
                ]
                label = "、".join(tool_names) if tool_names else "未知工具"
                return [
                    AdaptedSseEvent(
                        event="process",
                        payload=self._base_payload(
                            stage="工具执行中",
                            code="TOOL_CALLING",
                            message=f"准备调用工具：{label}",
                            meta={"tool_calls": tool_calls},
                        ),
                    )
                ]

            content = self._content_to_text(message_data.get("content"))
            if content:
                self._has_ai_text = True
                return [
                    AdaptedSseEvent(
                        event="ai_token",
                        payload=self._base_payload(
                            stage="生成回答中",
                            content=content,
                        ),
                    )
                ]
            return []

        if self._is_tool_message_type(message_type, message_data):
            content = self._content_to_text(message_data.get("content"))
            return [
                AdaptedSseEvent(
                    event="process",
                    payload=self._base_payload(
                        stage="工具执行中",
                        code="TOOL_RESULT",
                        message="工具已返回结果",
                        content=content or None,
                        meta={"tool_call_id": message_data.get("tool_call_id")},
                    ),
                )
            ]

        return []

    def _adapt_result_event(self, data: dict[str, object]) -> list[AdaptedSseEvent]:
        fallback_text = self._extract_display_text(data)
        if fallback_text and not self._has_ai_text:
            self._has_ai_text = True
            return [
                AdaptedSseEvent(
                    event="ai_token",
                    payload=self._base_payload(
                        stage="生成回答中",
                        content=fallback_text,
                    ),
                )
            ]

        return []

    def _base_payload(
        self,
        *,
        stage: str,
        code: str | None = None,
        message: str | None = None,
        content: str | None = None,
        status: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "graph_name": self._graph_name,
            "request_id": self._request_id,
            "session_id": self._session_id,
            "page_id": self._page_id,
            "stage": stage,
        }
        if code is not None:
            payload["code"] = code
        if message is not None:
            payload["message"] = message
        if content is not None:
            payload["content"] = content
        if status is not None:
            payload["status"] = status
        if meta:
            payload["meta"] = meta
        return payload

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
                else:
                    parts.append(str(block))
            return "".join(parts)
        if content is None:
            return ""
        return str(content)

    @staticmethod
    def _is_ai_message_type(message_type: str, message_data: dict[str, object]) -> bool:
        normalized = message_type.lower()
        if normalized.startswith("ai"):
            return True
        return str(message_data.get("type", "")).lower().startswith("ai")

    @staticmethod
    def _is_tool_message_type(message_type: str, message_data: dict[str, object]) -> bool:
        normalized = message_type.lower()
        return normalized == "tool" or str(message_data.get("type", "")).lower() == "tool"

    @staticmethod
    def _resolve_node_name(data: dict[str, object]) -> str:
        namespace = data.get("ns")
        if isinstance(namespace, list) and namespace:
            return str(namespace[-1])

        payload = data.get("data")
        if isinstance(payload, dict) and payload:
            return str(next(iter(payload.keys())))

        return "unknown"

    @staticmethod
    def _node_message(node_name: str) -> str:
        node_key = node_name.lower()
        mapping = {
            "prepare": "已接收请求，准备开始处理",
            "preprocess": "已接收请求，正在整理输入",
            "analyze": "正在执行分析",
            "agent": "正在生成回复",
            "tools": "正在执行工具调用",
            "finalize": "正在整理最终结果",
            "empty": "输入为空，正在返回默认结果",
        }
        return mapping.get(node_key, f"节点更新：{node_name}")

    @staticmethod
    def _resolve_stage(*, node_name: str) -> str:
        node_key = node_name.lower()
        if node_key in {"prepare", "preprocess", "analyze", "empty"}:
            return "处理中"
        if node_key in {"tools"}:
            return "工具执行中"
        if node_key in {"agent", "finalize"}:
            return "生成回答中"
        return "处理中"

    @staticmethod
    def _extract_error_message(data: dict[str, object]) -> str:
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)
        message = data.get("message")
        if isinstance(message, str):
            return message
        return "流式执行失败"

    @staticmethod
    def _extract_display_text(data: dict[str, object]) -> str | None:
        for key in ("answer", "content", "text", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        analysis = data.get("analysis")
        if isinstance(analysis, dict):
            summary = analysis.get("summary")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()

        return None


class ChatStreamService:
    def __init__(
        self,
        graph_service: GraphService,
        sse_connection_registry: SseConnectionRegistry,
    ) -> None:
        self._graph_service = graph_service
        self._sse_connection_registry = sse_connection_registry
        self._tasks: set[asyncio.Task[None]] = set()

    async def execute(
        self,
        *,
        graph_name: str,
        payload: dict[str, Any],
        session_id: str,
        page_id: str,
        request_id: str | None = None,
        request_context: GraphRequestContext | None = None,
    ) -> ChatStreamAccepted:
        self._sse_connection_registry.require(session_id=session_id, page_id=page_id)

        resolved_request_id = request_id or uuid4().hex
        task = asyncio.create_task(
            self._run_stream(
                graph_name=graph_name,
                payload=dict(payload),
                session_id=session_id,
                page_id=page_id,
                request_id=resolved_request_id,
                request_context=request_context,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        return ChatStreamAccepted(
            graph_name=graph_name,
            session_id=session_id,
            page_id=page_id,
            request_id=resolved_request_id,
        )

    async def _run_stream(
        self,
        *,
        graph_name: str,
        payload: dict[str, Any],
        session_id: str,
        page_id: str,
        request_id: str,
        request_context: GraphRequestContext | None,
    ) -> None:
        adapter = SseEventAdapter(
            graph_name=graph_name,
            session_id=session_id,
            page_id=page_id,
            request_id=request_id,
        )
        graph_payload = dict(payload)
        graph_payload["session_id"] = session_id

        try:
            async for event in self._graph_service.stream_events(
                graph_name=graph_name,
                payload=graph_payload,
                request_context=request_context,
            ):
                for adapted_event in adapter.adapt(event):
                    await self._send_adapted_event(
                        session_id=session_id,
                        page_id=page_id,
                        event=adapted_event,
                    )
        except SseConnectionNotFoundError:
            return
        except Exception as exc:
            await self._send_execution_error(
                graph_name=graph_name,
                session_id=session_id,
                page_id=page_id,
                request_id=request_id,
                exc=exc,
            )

    async def _send_adapted_event(
        self,
        *,
        session_id: str,
        page_id: str,
        event: AdaptedSseEvent,
    ) -> None:
        await self._sse_connection_registry.send(
            session_id=session_id,
            page_id=page_id,
            event=event.event,
            payload=event.payload,
        )

    async def _send_execution_error(
        self,
        *,
        graph_name: str,
        session_id: str,
        page_id: str,
        request_id: str,
        exc: Exception,
    ) -> None:
        try:
            await self._sse_connection_registry.send(
                session_id=session_id,
                page_id=page_id,
                event="ai_error",
                payload={
                    "graph_name": graph_name,
                    "request_id": request_id,
                    "session_id": session_id,
                    "page_id": page_id,
                    "stage": "失败",
                    "code": self._error_code(exc),
                    "message": str(exc),
                },
            )
        except SseConnectionNotFoundError:
            return

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, GraphNotFoundError):
            return "GRAPH_NOT_FOUND"
        if isinstance(exc, GraphPayloadValidationError):
            return "INVALID_PAYLOAD"
        if isinstance(exc, ChatModelBuildError):
            return "MODEL_BUILD_ERROR"
        if isinstance(exc, (MCPConfigurationError, MCPToolResolutionError)):
            return "MCP_ERROR"
        return "STREAM_EXECUTION_ERROR"
