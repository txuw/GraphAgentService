from fastapi import FastAPI

from overmindagent.api import router as api_router
from overmindagent.common import create_checkpoint_provider, get_settings
from overmindagent.graphs import create_graph_registry
from overmindagent.llm import LLMSessionFactory
from overmindagent.services import GraphService


def create_app() -> FastAPI:
    settings = get_settings()
    llm_factory = LLMSessionFactory(settings.llm)
    checkpoint_provider = create_checkpoint_provider(settings.graph)
    graph_registry = create_graph_registry(
        llm_factory=llm_factory,
        checkpoint_provider=checkpoint_provider,
    )

    app = FastAPI(title=settings.app_name)
    app.state.graph_service = GraphService(graph_registry)
    app.include_router(api_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "Hello World"}

    @app.get("/hello/{name}")
    async def say_hello(name: str) -> dict[str, str]:
        return {"message": f"Hello {name}"}

    return app


app = create_app()
