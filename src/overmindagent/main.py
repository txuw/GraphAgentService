from fastapi import FastAPI

from overmindagent.api import router as api_router
from overmindagent.common import create_checkpoint_provider, get_settings
from overmindagent.common.lifecycle import create_app_lifespan
from overmindagent.graphs import create_graph_registry
from overmindagent.llm import LLMRouter
from overmindagent.services import ChatStreamService, GraphService, SseConnectionRegistry


def create_app() -> FastAPI:
    settings = get_settings()
    llm_router = LLMRouter(settings.llm)
    checkpoint_provider = create_checkpoint_provider(settings.graph)
    graph_registry = create_graph_registry(
        settings=settings,
        checkpoint_provider=checkpoint_provider,
    )
    graph_service = GraphService(graph_registry, llm_router)
    sse_connection_registry = SseConnectionRegistry()
    chat_stream_service = ChatStreamService(graph_service, sse_connection_registry)
    app = FastAPI(
        title=settings.app.name,
        lifespan=create_app_lifespan(
            sse_connection_registry=sse_connection_registry,
        ),
    )
    app.state.graph_service = graph_service
    app.state.sse_connection_registry = sse_connection_registry
    app.state.chat_stream_service = chat_stream_service
    app.include_router(api_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "Hello World"}

    @app.get("/hello/{name}")
    async def say_hello(name: str) -> dict[str, str]:
        return {"message": f"Hello {name}"}

    return app


app = create_app()
