from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from overmindagent.graphs.runtime import GraphRunContext

from .prompts import (
    ANALYSIS_PROMPT_TEMPLATE,
    PLAN_PROMPT_TEMPLATE,
    SYSTEM_ANALYSIS_PROMPT,
    SYSTEM_PLAN_PROMPT,
)
from .state import PlanAnalyzeGraphState


class PlanAnalyzeNodes:
    def __init__(
        self,
        planner_binding: str = "planner",
        analysis_binding: str = "analysis",
    ) -> None:
        self._planner_binding = planner_binding
        self._analysis_binding = analysis_binding

    def prepare(self, state: PlanAnalyzeGraphState) -> PlanAnalyzeGraphState:
        query = state.get("query", "").strip()
        return {"query": query}

    def route_after_prepare(self, state: PlanAnalyzeGraphState) -> str:
        return "plan" if state.get("query") else "empty"

    async def plan(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> PlanAnalyzeGraphState:
        model = runtime.context.resolve_model(
            binding=self._planner_binding,
            tags=("planning",),
        )
        response = await model.ainvoke(self.build_plan_messages(state["query"]))
        return {"plan": self._content_to_text(response)}

    async def analyze(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> PlanAnalyzeGraphState:
        model = runtime.context.resolve_model(
            binding=self._analysis_binding,
            tags=("analysis",),
        )
        response = await model.ainvoke(
            self.build_analysis_messages(
                query=state["query"],
                plan=state.get("plan", ""),
            )
        )
        return {"analysis": self._content_to_text(response)}

    def empty(self, state: PlanAnalyzeGraphState) -> PlanAnalyzeGraphState:
        return {
            "plan": "",
            "analysis": "No query provided.",
        }

    def finalize(self, state: PlanAnalyzeGraphState) -> PlanAnalyzeGraphState:
        return {
            "plan": state.get("plan", ""),
            "analysis": state.get("analysis", ""),
        }

    @staticmethod
    def build_plan_messages(query: str) -> list[object]:
        return [
            SystemMessage(content=SYSTEM_PLAN_PROMPT),
            HumanMessage(content=PLAN_PROMPT_TEMPLATE.format(query=query)),
        ]

    @staticmethod
    def build_analysis_messages(*, query: str, plan: str) -> list[object]:
        return [
            SystemMessage(content=SYSTEM_ANALYSIS_PROMPT),
            HumanMessage(
                content=ANALYSIS_PROMPT_TEMPLATE.format(
                    query=query,
                    plan=plan,
                )
            ),
        ]

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
