from fastapi import FastAPI

from graphagentservice.api import router as api_router
from graphagentservice.common.auth import LogtoAuthenticator
from graphagentservice.common import create_checkpoint_provider, get_settings
from graphagentservice.common.lifecycle import create_app_lifespan
from graphagentservice.graphs import create_graph_registry
from graphagentservice.llm import LLMRouter
from graphagentservice.mcp import MCPSettings, MCPToolResolver
from graphagentservice.services import (
    ChatStreamService,
    GraphService,
    GraphStreamDispatchService,
    SseConnectionRegistry,
)


def create_app() -> FastAPI:
    settings = get_settings()
    llm_router = LLMRouter(settings.llm)
    checkpoint_provider = create_checkpoint_provider(settings.graph)
    logto_authenticator = LogtoAuthenticator(settings.get("logto", {}))
    mcp_settings = MCPSettings.model_validate(settings.get("mcp", {}))
    mcp_tool_resolver = MCPToolResolver(mcp_settings)
    sse_connection_registry = SseConnectionRegistry()

    async def initialize_app_state(app: FastAPI) -> None:
        graph_registry = create_graph_registry(
            settings=settings,
            checkpoint_provider=checkpoint_provider,
        )
        graph_service = GraphService(
            graph_registry,
            llm_router,
            mcp_tool_resolver=mcp_tool_resolver,
            checkpoint_namespace_prefix=str(settings.app.name),
        )
        app.state.graph_service = graph_service
        app.state.chat_stream_service = ChatStreamService(
            graph_service,
            sse_connection_registry,
        )
        app.state.graph_stream_dispatch_service = GraphStreamDispatchService(
            graph_service,
            sse_connection_registry,
        )

    app = FastAPI(
        title=settings.app.name,
        lifespan=create_app_lifespan(
            app_initializer=initialize_app_state,
            checkpoint_provider=checkpoint_provider,
            sse_connection_registry=sse_connection_registry,
        ),
    )
    app.state.sse_connection_registry = sse_connection_registry
    app.state.logto_authenticator = logto_authenticator
    app.include_router(api_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "Hello World"}

    @app.get("/hello/{name}")
    async def say_hello(name: str) -> dict[str, str]:
        return {"message": f"Hello {name}"}

    return app


app = create_app()
