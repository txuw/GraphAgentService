from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from pydantic import ConfigDict
from langchain_core.tools import BaseTool

from graphagentservice.schemas.api import AgentStreamEvent


def dumps_event_content(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class RequestEventSequence:
    def __init__(self) -> None:
        self._lock = Lock()
        self._value = 0

    def next(self) -> int:
        with self._lock:
            self._value += 1
            return self._value


@dataclass(slots=True)
class RequestEventTarget:
    graph_name: str
    session_id: str
    request_id: str
    trace_id: str
    user_id: str | None
    page_id: str | None = None


class AgentStreamEventFactory:
    def __init__(
        self,
        *,
        target: RequestEventTarget,
        sequence: RequestEventSequence | None = None,
    ) -> None:
        self._target = target
        self._sequence = sequence or RequestEventSequence()

    def build_connected(
        self,
        *,
        session_id: str,
        page_id: str,
        connection_id: str,
        user_id: str | None,
        last_event_id: str | None,
        connected_at: datetime,
        sequence_id: int,
    ) -> AgentStreamEvent:
        return AgentStreamEvent(
            session_id=session_id,
            event_type="connected",
            event_id=f"connected:{connection_id}:{sequence_id}",
            content=dumps_event_content(
                {
                    "connectionId": connection_id,
                    "userId": user_id,
                    "sessionId": session_id,
                    "pageId": page_id,
                    "serverTime": connected_at.isoformat(),
                    "lastEventId": last_event_id,
                }
            ),
            done=False,
        )

    def build_heartbeat(
        self,
        *,
        session_id: str,
        page_id: str,
        connection_id: str,
        sequence_id: int,
    ) -> AgentStreamEvent:
        return AgentStreamEvent(
            session_id=session_id,
            event_type="heartbeat",
            event_id=f"heartbeat:{connection_id}:{sequence_id}",
            content=dumps_event_content({"ts": datetime.now(UTC).isoformat()}),
            done=False,
        )

    def build_request_accepted(self) -> AgentStreamEvent:
        return self.build_plan_status(
            code="REQUEST_ACCEPTED",
            message="已接收请求，正在分析你的诉求",
        )

    def build_plan_status(self, *, code: str, message: str) -> AgentStreamEvent:
        seq = self._sequence.next()
        return AgentStreamEvent(
            session_id=self._target.session_id,
            request_id=self._target.request_id,
            trace_id=self._target.trace_id or None,
            event_type="plan_status",
            event_id=f"{self._target.request_id}:status:{seq}",
            seq=seq,
            content=message,
            done=False,
            code=code,
            message=message,
            retriable=False,
        )

    def build_ai_token(self, content: str) -> AgentStreamEvent:
        seq = self._sequence.next()
        return AgentStreamEvent(
            session_id=self._target.session_id,
            request_id=self._target.request_id,
            trace_id=self._target.trace_id or None,
            event_type="ai_token",
            event_id=f"{self._target.request_id}:{seq}",
            seq=seq,
            content=content,
            done=False,
            retriable=False,
        )

    def build_done(self) -> AgentStreamEvent:
        return AgentStreamEvent(
            session_id=self._target.session_id,
            request_id=self._target.request_id,
            trace_id=self._target.trace_id or None,
            event_type="ai_done",
            event_id=f"{self._target.request_id}:done",
            content="",
            done=True,
            finish_reason="stop",
            retriable=False,
        )

    def build_error(
        self,
        *,
        code: str,
        message: str,
        retriable: bool,
    ) -> AgentStreamEvent:
        return AgentStreamEvent(
            session_id=self._target.session_id,
            request_id=self._target.request_id,
            trace_id=self._target.trace_id or None,
            event_type="ai_error",
            event_id=f"{self._target.request_id}:error",
            content="",
            done=True,
            code=code,
            message=message,
            retriable=retriable,
        )

    def build_tool_event(
        self,
        *,
        tool_name: str,
        phase: str,
        error_message: str | None = None,
    ) -> AgentStreamEvent:
        seq = self._sequence.next()
        event_type = {
            "start": "tool_start",
            "done": "tool_done",
            "error": "tool_error",
        }[phase]
        content = {
            "toolName": tool_name,
            "phase": phase,
        }
        if error_message:
            content["errorMessage"] = error_message
        return AgentStreamEvent(
            session_id=self._target.session_id,
            request_id=self._target.request_id,
            trace_id=self._target.trace_id or None,
            event_type=event_type,
            event_id=f"{self._target.request_id}:tool:{tool_name}:{phase}:{seq}",
            seq=seq,
            content=dumps_event_content(content),
            done=False,
            code="TOOL_CALL_ERROR" if phase == "error" else None,
            message=error_message,
            retriable=False,
        )

    @staticmethod
    def status_for_node(node_name: str) -> tuple[str, str]:
        normalized = node_name.lower()
        mapping = {
            "prepare": ("GRAPH_NODE_UPDATED", "已接收请求，准备开始处理"),
            "preprocess": ("GRAPH_NODE_UPDATED", "已接收请求，正在整理输入"),
            "analyze": ("INTENT_RESOLVED", "已进入分析阶段，正在处理你的请求"),
            "agent": ("GRAPH_NODE_UPDATED", "正在生成回复"),
            "tools": ("TOOLS_PREPARED", "已准备工具调用，正在查询所需数据"),
            "finalize": ("GRAPH_NODE_UPDATED", "正在整理最终结果"),
            "empty": ("GRAPH_NODE_UPDATED", "输入为空，正在返回默认结果"),
        }
        return mapping.get(normalized, ("GRAPH_NODE_UPDATED", f"节点更新：{node_name}"))


class StreamEventedTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    delegate: BaseTool
    emitter: "ToolEventEmitter"

    def __init__(self, *, delegate: BaseTool, emitter: "ToolEventEmitter") -> None:
        super().__init__(
            delegate=delegate,
            emitter=emitter,
            name=delegate.name,
            description=delegate.description,
            args_schema=delegate.args_schema,
            return_direct=delegate.return_direct,
            verbose=delegate.verbose,
            callbacks=delegate.callbacks,
            tags=delegate.tags,
            metadata=delegate.metadata,
            handle_tool_error=delegate.handle_tool_error,
            handle_validation_error=delegate.handle_validation_error,
            response_format=delegate.response_format,
            extras=dict(getattr(delegate, "extras", {}) or {}),
        )

    def _pack_input(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if kwargs:
            return dict(kwargs)
        if len(args) == 1:
            return args[0]
        return list(args)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        tool_name = self.delegate.name
        self.emitter.emit_tool_start(tool_name)
        try:
            result = self.delegate.invoke(self._pack_input(args, kwargs))
        except BaseException as exc:
            self.emitter.emit_tool_error(tool_name, str(exc))
            raise
        self.emitter.emit_tool_done(tool_name)
        return result

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        tool_name = self.delegate.name
        self.emitter.emit_tool_start(tool_name)
        try:
            result = await self.delegate.ainvoke(self._pack_input(args, kwargs))
        except BaseException as exc:
            self.emitter.emit_tool_error(tool_name, str(exc))
            raise
        self.emitter.emit_tool_done(tool_name)
        return result


class ToolEventEmitter:
    def __init__(
        self,
        *,
        factory: AgentStreamEventFactory,
        registry: Any,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._factory = factory
        self._registry = registry
        self._loop = loop

    @property
    def target(self) -> RequestEventTarget:
        return self._factory._target

    def wrap_tools(self, tools: list[BaseTool] | tuple[BaseTool, ...] | Any) -> list[BaseTool]:
        wrapped_tools: list[BaseTool] = []
        for tool in tools:
            if isinstance(tool, StreamEventedTool):
                wrapped_tools.append(tool)
            else:
                wrapped_tools.append(StreamEventedTool(delegate=tool, emitter=self))
        return wrapped_tools

    def emit_tool_start(self, tool_name: str) -> None:
        self._schedule(self._factory.build_tool_event(tool_name=tool_name, phase="start"))

    def emit_tool_done(self, tool_name: str) -> None:
        self._schedule(self._factory.build_tool_event(tool_name=tool_name, phase="done"))

    def emit_tool_error(self, tool_name: str, error_message: str) -> None:
        self._schedule(
            self._factory.build_tool_event(
                tool_name=tool_name,
                phase="error",
                error_message=error_message,
            )
        )

    def _schedule(self, event: AgentStreamEvent) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._registry.publish_agent_event(
                    session_id=self.target.session_id,
                    user_id=self.target.user_id,
                    page_id=self.target.page_id,
                    event=event,
                ),
                self._loop,
            )
        except RuntimeError:
            return
        future.add_done_callback(self._swallow_future_error)

    @staticmethod
    def _swallow_future_error(future: Any) -> None:
        try:
            future.result()
        except Exception:
            return


class AgentStreamEventAdapter:
    def __init__(self, *, factory: AgentStreamEventFactory) -> None:
        self._factory = factory
        self._has_ai_text = False
        self._last_status: tuple[str, str] | None = None

    def initial_events(self) -> list[AgentStreamEvent]:
        return [self._factory.build_request_accepted()]

    def adapt(self, event_name: str, data: dict[str, object]) -> list[AgentStreamEvent]:
        if event_name == "session":
            return []
        if event_name == "updates":
            return self._adapt_update_event(data)
        if event_name == "messages":
            return self._adapt_message_event(data)
        if event_name == "result":
            return self._adapt_result_event(data)
        if event_name == "completed":
            return [self._factory.build_done()]
        return []

    def _adapt_update_event(self, data: dict[str, object]) -> list[AgentStreamEvent]:
        node_name = self._resolve_node_name(data)
        status = self._factory.status_for_node(node_name)
        if status == self._last_status:
            return []
        self._last_status = status
        code, message = status
        return [self._factory.build_plan_status(code=code, message=message)]

    def _adapt_message_event(self, data: dict[str, object]) -> list[AgentStreamEvent]:
        message = data.get("message")
        if not isinstance(message, dict):
            return []

        message_type = str(message.get("type", ""))
        message_data = message.get("data")
        if not isinstance(message_data, dict):
            return []
        if not self._is_ai_message_type(message_type, message_data):
            return []

        content = self._content_to_text(message_data.get("content"))
        if not content:
            return []
        self._has_ai_text = True
        return [self._factory.build_ai_token(content)]

    def _adapt_result_event(self, data: dict[str, object]) -> list[AgentStreamEvent]:
        if self._has_ai_text:
            return []

        fallback_text = self._extract_display_text(data)
        if not fallback_text:
            return []
        self._has_ai_text = True
        return [self._factory.build_ai_token(fallback_text)]

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
    def _extract_display_text(data: dict[str, object]) -> str | None:
        for key in ("answer", "content", "text", "message", "analysis", "plan"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        analysis = data.get("analysis")
        if isinstance(analysis, dict):
            summary = analysis.get("summary")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()

        answer = data.get("answer")
        if isinstance(answer, dict):
            return answer.get("name") if isinstance(answer.get("name"), str) else None

        return None
