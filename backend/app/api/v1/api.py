from fastapi import APIRouter

from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.backtests import router as backtests_router
from app.api.v1.endpoints.cron import router as cron_router
from app.api.v1.endpoints.dart import router as dart_router
from app.api.v1.endpoints.graph import router as graph_router
from app.api.v1.endpoints.macro import router as macro_router
from app.api.v1.endpoints.master import router as master_router
from app.api.v1.endpoints.prices import router as prices_router
from app.api.v1.endpoints.relationships import router as relationships_router
from app.api.v1.endpoints.scores import router as scores_router
from app.api.v1.endpoints.ui import router as ui_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(master_router)
api_router.include_router(admin_router)
api_router.include_router(backtests_router)
api_router.include_router(prices_router)
api_router.include_router(cron_router)
api_router.include_router(dart_router)
api_router.include_router(macro_router)
api_router.include_router(graph_router)
api_router.include_router(relationships_router)
api_router.include_router(scores_router)
api_router.include_router(ui_router)
