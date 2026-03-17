from .state import ImageGraphState
from overmindagent.graphs.runtime import GraphRunContext
from langgraph.runtime import Runtime

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
            tags=("structured-output",),
        )
        analysis = await model.ainvoke(self.build_messages(state["normalized_text"]))
        return {"analysis": analysis}