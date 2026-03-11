from fastapi import APIRouter

from .routes.graphs import router as graphs_router
from .routes.system import router as system_router

router = APIRouter()
router.include_router(system_router)
router.include_router(graphs_router, prefix="/api")
