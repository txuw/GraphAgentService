from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from graphagentservice.graphs.runtime import GraphRunContext
from graphagentservice.schemas.body_report import BodyReportInfo

from .prompts import ANALYZE_SYSTEM_PROMPT, DEFAULT_ANALYSIS_PROMPT
from .state import BodyReportAnalyzeGraphState


class BodyReportAnalyzeNodes:
    def __init__(self, llm_binding: str = "analysis") -> None:
        self._llm_binding = llm_binding

    async def analyze(
        self,
        state: BodyReportAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> BodyReportAnalyzeGraphState:
        model = runtime.context.structured_model_with_json_object(
            schema=BodyReportInfo,
            binding=self._llm_binding,
            tags=("multimodal", "body-report"),
        )
        response = await model.ainvoke(
            self.build_messages(
                prompt=state.get("text", "").strip() or DEFAULT_ANALYSIS_PROMPT,
                image_url=state["image_url"].strip(),
            )
        )
        return {"answer": response}

    @staticmethod
    def build_messages(
        *,
        prompt: str,
        image_url: str,
    ) -> list[object]:
        return [
            SystemMessage(content=ANALYZE_SYSTEM_PROMPT),
            HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]
            ),
        ]
