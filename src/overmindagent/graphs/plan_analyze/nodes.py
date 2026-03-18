from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.runtime import Runtime

from overmindagent.graphs.runtime import GraphRunContext

from .prompts import ANALYSIS_PROMPT_TEMPLATE, SYSTEM_ANALYSIS_PROMPT
from .state import PlanAnalyzeGraphState


class PlanAnalyzeNodes:
    def __init__(
        self,
        analysis_binding: str = "analysis",
    ) -> None:
        self._analysis_binding = analysis_binding

    async def analyze(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> PlanAnalyzeGraphState:
        query = state.get("query", "").strip()
        if not query:
            return {
                "query": "",
                "plan": "",
                "analysis": "No query provided.",
                "messages": [],
            }

        messages = self.build_analysis_messages(
            query=query,
            plan=state.get("plan", ""),
            messages=state.get("messages", ()),
        )
        tools = await self._resolve_tools(runtime)
        if tools:
            model = runtime.context.tool_model(
                binding=self._analysis_binding,
                tools=tools,
                tags=("analysis", "tool-calling"),
            )
            response = await model.ainvoke(messages)
            result: PlanAnalyzeGraphState = {
                "query": query,
                "plan": state.get("plan", ""),
                "messages": [response],
            }
            if not response.tool_calls:
                result["analysis"] = self._content_to_text(response)
            return result

        model = runtime.context.resolve_model(
            binding=self._analysis_binding,
            tags=("analysis",),
        )
        response = await model.ainvoke(messages)
        return {
            "query": query,
            "plan": state.get("plan", ""),
            "analysis": self._content_to_text(response),
        }

    async def tools(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict[str, list[object]]:
        tools = await self._resolve_tools(runtime)
        tool_node = ToolNode(tools)
        result = await tool_node.ainvoke(state, runtime=runtime)
        if isinstance(result, dict):
            return result
        return {"messages": list(result)}

    def route_after_analyze(self, state: PlanAnalyzeGraphState) -> str:
        if not state.get("messages"):
            return "__end__"
        return tools_condition(state)

    @staticmethod
    def build_analysis_messages(
        *,
        query: str,
        plan: str,
        messages: Sequence[object] = (),
    ) -> list[object]:
        if messages:
            return [SystemMessage(content=SYSTEM_ANALYSIS_PROMPT), *list(messages)]
        return [
            SystemMessage(content=SYSTEM_ANALYSIS_PROMPT),
            HumanMessage(
                content=ANALYSIS_PROMPT_TEMPLATE.format(
                    query=query,
                    plan=plan,
                )
            ),
        ]

    async def _resolve_tools(
        self,
        runtime: Runtime[GraphRunContext],
    ) -> list[BaseTool]:
        if not runtime.context.mcp_servers:
            return []

        resolver = runtime.context.mcp_tool_resolver
        if resolver is None:
            return []

        return await resolver.resolve_tools(
            graph_name=runtime.context.graph_name,
            server_names=runtime.context.mcp_servers,
            current_user=runtime.context.current_user,
            request_headers=dict(runtime.context.request_headers),
        )

    @staticmethod
    def _content_to_text(response: Any) -> str:
        content = response.content if isinstance(response, AIMessage) else response
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
                else:
                    parts.append(str(block))
            return "\n".join(parts).strip()
        return str(content).strip()
