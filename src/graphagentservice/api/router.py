from fastapi import APIRouter, Depends

from .dependencies import require_current_user
from .routes.chat import router as chat_router
from .routes.graphs import router as graphs_router
from .routes.system import router as system_router

router = APIRouter()
router.include_router(system_router)
router.include_router(
    chat_router,
    prefix="/api",
    dependencies=[Depends(require_current_user)],
)
router.include_router(
    graphs_router,
    prefix="/api",
    dependencies=[Depends(require_current_user)],
)
