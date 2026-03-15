from .chat import router as chat_router
from .graphs import router as graphs_router
from .system import router as system_router

__all__ = ["chat_router", "graphs_router", "system_router"]
