from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from graphagentservice.common.logging import context_extra
from graphagentservice.common.failures import GraphRecoveryState, RunStatus
from graphagentservice.graphs.runtime import GraphRunContext
from graphagentservice.graphs.tool_agent.prompts import SYSTEM_PROMPT
from graphagentservice.graphs.tool_agent.state import ToolAgentGraphState
from graphagentservice.schemas.tool_agent import ToolCallTrace
from graphagentservice.tools import build_toolset

_logger = logging.getLogger(__name__)


class ToolAgentNodes:
    def __init__(
        self,
        llm_binding: str = "agent",
        tools: Sequence[BaseTool] | None = None,
    ) -> None:
        self._llm_binding = llm_binding
        self._local_tools = tuple(tools) if tools is not None else None

    def _build_local_tools(self) -> list[BaseTool]:
        if self._local_tools is not None:
            return list(self._local_tools)
        return build_toolset()

    def prepare(self, state: ToolAgentGraphState) -> ToolAgentGraphState:
        _logger.info(
            "Tool agent prepare started",
            extra=context_extra(
                event="graph_node_started",
                graph="tool-agent",
                node="prepare",
                status="started",
            ),
        )
        query = state.get("query", "").strip()
        if not query:
            _logger.info(
                "Tool agent prepare completed",
                extra=context_extra(
                    event="graph_node_completed",
                    graph="tool-agent",
                    node="prepare",
                    status="completed",
                ),
            )
            return {"query": "", "messages": []}
        result = {
            "query": query,
            "messages": [HumanMessage(content=query)],
            "recovery": GraphRecoveryState(
                run_status=RunStatus.CLEAN.value,
                last_stable_message_count=1,
            ).to_dict(),
        }
        _logger.info(
            "Tool agent prepare completed",
            extra=context_extra(
                event="graph_node_completed",
                graph="tool-agent",
                node="prepare",
                status="completed",
            ),
        )
        return result

    def route_after_prepare(self, state: ToolAgentGraphState) -> str:
        return "agent" if state.get("query") else "empty"

    async def agent(
        self,
        state: ToolAgentGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> ToolAgentGraphState:
        started = time.perf_counter()
        _logger.info(
            "Tool agent node started",
            extra=context_extra(
                event="graph_node_started",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="agent",
                status="started",
            ),
        )
        try:
            tools = await self._resolve_tools(runtime)
            model = runtime.context.tool_model(
                binding=self._llm_binding,
                tools=tools,
                tags=("tool-calling",),
            )
            response = await model.ainvoke(self.build_messages(state.get("messages", [])))
            recovery = GraphRecoveryState.from_mapping(state.get("recovery"))
            if not response.tool_calls:
                recovery.last_stable_message_count = len(state.get("messages", [])) + 1
                recovery.run_status = RunStatus.CLEAN.value
            result = {"messages": [response], "recovery": recovery.to_dict()}
        except Exception as exc:
            _logger.exception(
                "Tool agent node failed",
                extra=context_extra(
                    event="graph_node_failed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="agent",
                    status="failed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                    errorType=type(exc).__name__,
                ),
            )
            raise
        _logger.info(
            "Tool agent node completed",
            extra=context_extra(
                event="graph_node_completed",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="agent",
                status="completed",
                elapsedMs=round((time.perf_counter() - started) * 1000),
            ),
        )
        return result

    async def tools(
        self,
        state: ToolAgentGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict[str, list[ToolMessage]]:
        started = time.perf_counter()
        _logger.info(
            "Tool agent tools node started",
            extra=context_extra(
                event="graph_node_started",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="tools",
                status="started",
            ),
        )
        try:
            resolved_tools = await self._resolve_tools(runtime)
            tool_node = _build_tool_node(resolved_tools, runtime)
            result = await tool_node.ainvoke(state, runtime=runtime)
            if isinstance(result, dict):
                payload = result
            else:
                payload = {"messages": list(result)}
            tool_messages = payload.get("messages", [])
            if "__error__" in payload:
                raise payload["__error__"]
            recovery = GraphRecoveryState.from_mapping(payload.get("recovery") or state.get("recovery"))
            if tool_messages:
                recovery.last_stable_message_count = len(state.get("messages", [])) + len(tool_messages)
                if all(
                    isinstance(message, ToolMessage) and message.status != "error"
                    for message in tool_messages
                ):
                    recovery.run_status = RunStatus.CLEAN.value
            payload["recovery"] = recovery.to_dict()
        except Exception as exc:
            _logger.exception(
                "Tool agent tools node failed",
                extra=context_extra(
                    event="graph_node_failed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="tools",
                    status="failed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                    errorType=type(exc).__name__,
                ),
            )
            raise
        _logger.info(
            "Tool agent tools node completed",
            extra=context_extra(
                event="graph_node_completed",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="tools",
                status="completed",
                elapsedMs=round((time.perf_counter() - started) * 1000),
            ),
        )
        return payload

    def empty(self, state: ToolAgentGraphState) -> ToolAgentGraphState:
        _logger.info(
            "Tool agent empty node completed",
            extra=context_extra(
                event="graph_node_completed",
                graph="tool-agent",
                node="empty",
                status="completed",
            ),
        )
        return {
            "answer": "No query provided.",
            "tools_used": [],
            "recovery": GraphRecoveryState().to_dict(),
        }

    def finalize(self, state: ToolAgentGraphState) -> ToolAgentGraphState:
        messages = state.get("messages", [])
        answer = state.get("answer") or self._extract_final_answer(messages)
        tools_used = state.get("tools_used") or self._collect_tool_trace(messages)
        result = {
            "answer": answer,
            "tools_used": tools_used,
        }
        _logger.info(
            "Tool agent finalize completed",
            extra=context_extra(
                event="graph_node_completed",
                graph="tool-agent",
                node="finalize",
                status="completed",
            ),
        )
        return result

    async def _resolve_tools(
        self,
        runtime: Runtime[GraphRunContext],
    ) -> list[BaseTool]:
        resolver = runtime.context.mcp_tool_resolver
        if resolver is None:
            return self._build_local_tools()

        return await resolver.resolve_tools(
            graph_name=runtime.context.graph_name,
            server_names=runtime.context.mcp_servers,
            current_user=runtime.context.current_user,
            request_headers=dict(runtime.context.request_headers),
        )

    @staticmethod
    def build_messages(messages: Sequence[Any]) -> list[object]:
        return [SystemMessage(content=SYSTEM_PROMPT), *messages]

    @staticmethod
    def _extract_final_answer(messages: Sequence[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                text = ToolAgentNodes._content_to_text(message.content).strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _collect_tool_trace(messages: Sequence[Any]) -> list[ToolCallTrace]:
        tool_results = {
            str(message.tool_call_id): ToolAgentNodes._content_to_text(message.content)
            for message in messages
            if isinstance(message, ToolMessage)
        }
        traces: list[ToolCallTrace] = []
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            for call in message.tool_calls:
                traces.append(
                    ToolCallTrace(
                        tool_name=str(call.get("name", "")),
                        tool_args=dict(call.get("args", {})),
                        result=tool_results.get(str(call.get("id", "")), ""),
                    )
                )
        return traces

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
            return "\n".join(parts)
        return str(content)


def _build_tool_node(
    tools: list[BaseTool],
    runtime: Runtime[GraphRunContext],
) -> ToolNode:
    from graphagentservice.services.tool_execution import ObservedToolNode

    emitter = runtime.context.tool_stream_emitter
    return ObservedToolNode(tools, emitter=emitter)
