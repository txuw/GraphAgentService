from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Any


class StreamEventKind(str, Enum):
    PLAN_STATUS = "plan_status"
    AI_TOKEN = "ai_token"
    TOOL_START = "tool_start"
    TOOL_DONE = "tool_done"
    TOOL_ERROR = "tool_error"
    AI_DONE = "ai_done"
    AI_ERROR = "ai_error"


@dataclass(frozen=True, slots=True)
class StreamEventTarget:
    graph_name: str
    session_id: str
    request_id: str
    trace_id: str
    user_id: str | None
    page_id: str | None = None


class StreamEventSequence:
    def __init__(self) -> None:
        self._lock = Lock()
        self._value = 0

    def next(self) -> int:
        with self._lock:
            self._value += 1
            return self._value


@dataclass(frozen=True, slots=True)
class StreamEvent:
    target: StreamEventTarget
    kind: StreamEventKind
    seq: int
    event_id: str
    content: str | None = None
    code: str | None = None
    message: str | None = None
    retriable: bool | None = None
    finish_reason: str | None = None


class StreamEventFactory:
    def __init__(
        self,
        *,
        target: StreamEventTarget,
        sequence: StreamEventSequence | None = None,
    ) -> None:
        self._target = target
        self._sequence = sequence or StreamEventSequence()

    @property
    def target(self) -> StreamEventTarget:
        return self._target

    def build_request_accepted(self) -> StreamEvent:
        return self.build_plan_status(
            code="REQUEST_ACCEPTED",
            message="已接收请求，正在分析你的诉求",
        )

    def build_plan_status(self, *, code: str, message: str) -> StreamEvent:
        seq = self._sequence.next()
        return StreamEvent(
            target=self._target,
            kind=StreamEventKind.PLAN_STATUS,
            seq=seq,
            event_id=f"{self._target.request_id}:status:{seq}",
            content=message,
            code=code,
            message=message,
            retriable=False,
        )

    def build_ai_token(self, content: str) -> StreamEvent:
        seq = self._sequence.next()
        return StreamEvent(
            target=self._target,
            kind=StreamEventKind.AI_TOKEN,
            seq=seq,
            event_id=f"{self._target.request_id}:{seq}",
            content=content,
            retriable=False,
        )

    def build_done(self) -> StreamEvent:
        seq = self._sequence.next()
        return StreamEvent(
            target=self._target,
            kind=StreamEventKind.AI_DONE,
            seq=seq,
            event_id=f"{self._target.request_id}:done",
            content="",
            finish_reason="stop",
            retriable=False,
        )

    def build_error(self, *, code: str, message: str, retriable: bool) -> StreamEvent:
        seq = self._sequence.next()
        return StreamEvent(
            target=self._target,
            kind=StreamEventKind.AI_ERROR,
            seq=seq,
            event_id=f"{self._target.request_id}:error",
            content="",
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
    ) -> StreamEvent:
        seq = self._sequence.next()
        kind_map = {
            "start": StreamEventKind.TOOL_START,
            "done": StreamEventKind.TOOL_DONE,
            "error": StreamEventKind.TOOL_ERROR,
        }
        kind = kind_map[phase]
        content_payload: dict[str, str] = {"toolName": tool_name, "phase": phase}
        if error_message:
            content_payload["errorMessage"] = error_message
        return StreamEvent(
            target=self._target,
            kind=kind,
            seq=seq,
            event_id=f"{self._target.request_id}:tool:{tool_name}:{phase}:{seq}",
            content=json.dumps(content_payload, ensure_ascii=False, separators=(",", ":")),
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


class LangGraphStreamAdapter:
    """Adapts raw LangGraph astream chunks into internal StreamEvent lists."""

    def __init__(self, *, factory: StreamEventFactory) -> None:
        self._factory = factory
        self._has_ai_text = False
        self._last_status: tuple[str, str] | None = None

    def initial_events(self) -> list[StreamEvent]:
        return [self._factory.build_request_accepted()]

    def adapt(self, event_name: str, data: dict[str, object]) -> list[StreamEvent]:
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

    def _adapt_update_event(self, data: dict[str, object]) -> list[StreamEvent]:
        node_name = self._resolve_node_name(data)
        status = self._factory.status_for_node(node_name)
        if status == self._last_status:
            return []
        self._last_status = status
        code, message = status
        return [self._factory.build_plan_status(code=code, message=message)]

    def _adapt_message_event(self, data: dict[str, object]) -> list[StreamEvent]:
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

    def _adapt_result_event(self, data: dict[str, object]) -> list[StreamEvent]:
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
