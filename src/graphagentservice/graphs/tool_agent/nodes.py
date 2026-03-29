from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from graphagentservice.graphs.runtime import GraphRunContext
from graphagentservice.graphs.tool_agent.prompts import SYSTEM_PROMPT
from graphagentservice.graphs.tool_agent.state import ToolAgentGraphState
from graphagentservice.schemas.tool_agent import ToolCallTrace
from graphagentservice.tools import build_toolset


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
        query = state.get("query", "").strip()
        if not query:
            return {"query": "", "messages": []}
        return {
            "query": query,
            "messages": [HumanMessage(content=query)],
        }

    def route_after_prepare(self, state: ToolAgentGraphState) -> str:
        return "agent" if state.get("query") else "empty"

    async def agent(
        self,
        state: ToolAgentGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> ToolAgentGraphState:
        tools = await self._resolve_tools(runtime)
        model = runtime.context.tool_model(
            binding=self._llm_binding,
            tools=tools,
            tags=("tool-calling",),
        )
        response = await model.ainvoke(self.build_messages(state.get("messages", [])))
        return {"messages": [response]}

    async def tools(
        self,
        state: ToolAgentGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict[str, list[ToolMessage]]:
        resolved_tools = await self._resolve_tools(runtime)
        tool_node = _build_tool_node(resolved_tools, runtime)
        result = await tool_node.ainvoke(state, runtime=runtime)
        if isinstance(result, dict):
            return result
        return {"messages": list(result)}

    def empty(self, state: ToolAgentGraphState) -> ToolAgentGraphState:
        return {
            "answer": "No query provided.",
            "tools_used": [],
        }

    def finalize(self, state: ToolAgentGraphState) -> ToolAgentGraphState:
        messages = state.get("messages", [])
        answer = state.get("answer") or self._extract_final_answer(messages)
        tools_used = state.get("tools_used") or self._collect_tool_trace(messages)
        return {
            "answer": answer,
            "tools_used": tools_used,
        }

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
    if emitter is not None:
        return ObservedToolNode(tools, emitter=emitter)
    return ToolNode(tools)
