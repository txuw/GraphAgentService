from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from overmindagent.graphs.runtime import GraphRunContext
from overmindagent.graphs.tool_agent.prompts import SYSTEM_PROMPT
from overmindagent.graphs.tool_agent.state import ToolAgentGraphState
from overmindagent.schemas.tool_agent import ToolCallTrace
from overmindagent.tools import build_toolset


class ToolAgentNodes:
    def __init__(
        self,
        llm_binding: str = "agent",
        tools: Sequence[BaseTool] | None = None,
    ) -> None:
        self._llm_binding = llm_binding
        self._tools = list(tools or build_toolset())
        self._tool_node = ToolNode(self._tools)

    @property
    def tool_node(self) -> ToolNode:
        return self._tool_node

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
        model = runtime.context.tool_model(
            binding=self._llm_binding,
            tools=self._tools,
            tags=("tool-calling",),
        )
        response = await model.ainvoke(self.build_messages(state.get("messages", [])))
        return {"messages": [response]}

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
