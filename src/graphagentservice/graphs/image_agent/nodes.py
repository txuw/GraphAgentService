from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from graphagentservice.graphs.runtime import GraphRunContext

from .state import ImageGraphState


SYSTEM_PROMPT = (
    "你是一个图像理解助手。请结合用户问题和图片内容直接回答。"
    "如果无法从图片确定答案，请明确说明不确定。"
)
DEFAULT_PROMPT = "请描述这张图片的主要内容。"


class ImageAgentNodes:
    def __init__(self, llm_binding: str = "analysis") -> None:
        self._llm_binding = llm_binding

    async def analyze(
        self,
        state: ImageGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> ImageGraphState:
        model = runtime.context.image_model(
            binding=self._llm_binding,
            tags=("multimodal",),
        )
        response = await model.ainvoke(
            self.build_messages(
                prompt=state.get("text", "").strip() or DEFAULT_PROMPT,
                image_url=state["image_url"].strip(),
            )
        )
        return {"answer": self._content_to_text(response)}

    @staticmethod
    def build_messages(*, prompt: str, image_url: str) -> list[object]:
        return [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]
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
            return "".join(parts).strip()
        return str(content).strip()
