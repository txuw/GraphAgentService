from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from graphagentservice.graphs.runtime import GraphRunContext

from .state import ImageAnalyzeCaloriesGraphState
from .prompts import ANALYSIS_PROMPTS, ANALYZE_SYSTEM_PROMPT
from graphagentservice.schemas.image_calories import CalorieInfo


class ImageAnalyzeCaloriesAgentNodes:
    def __init__(self, llm_binding: str = "analysis") -> None:
        self._llm_binding = llm_binding

    async def analyze(
            self,
            state: ImageAnalyzeCaloriesGraphState,
            runtime: Runtime[GraphRunContext],
    ) -> ImageAnalyzeCaloriesGraphState:
        model = runtime.context.image_model(
            binding=self._llm_binding,
            tags=("multimodal",),
        )
        response = await model.with_structured_output(
            CalorieInfo, method='json_schema'
        ).ainvoke(self.build_messages(
            prompt=state.get("text", "").strip() or ANALYSIS_PROMPTS,
            image_url=state["image_url"].strip(),
        ))
        return {"answer": response}

    @staticmethod
    def build_messages(*, prompt: str, image_url: str) -> list[object]:
        return [
            SystemMessage(content=ANALYZE_SYSTEM_PROMPT),
            HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]
            ),
        ]
