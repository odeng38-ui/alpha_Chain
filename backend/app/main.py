import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.models.schema  # Ensure models are loaded
from app.api.v1.api import api_router
from app.config import settings
from app.db.session import get_db
from app.jobs.price_jobs import get_scheduler
from app.logging_config import configure_logging, request_id_context
from app.metrics import request_metrics

configure_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 스케줄러 시작, 종료 시 정리."""
    scheduler = get_scheduler()
    scheduler.start()
    logger.info("APScheduler 시작 완료")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler 종료 완료")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    docs_url="/docs" if settings.DOCS_ENABLED and settings.APP_ENV != "production" else None,
    redoc_url="/redoc" if settings.DOCS_ENABLED and settings.APP_ENV != "production" else None,
    lifespan=lifespan,
)

app.include_router(api_router)


@app.middleware("http")
async def request_context_middleware(request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = request_id_context.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        duration = time.perf_counter() - started
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        request_metrics.observe(request.method, path, response.status_code, duration)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        logger.info(
            "request completed method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration * 1000,
        )
        return response
    except Exception:
        duration = time.perf_counter() - started
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        request_metrics.observe(request.method, path, 500, duration)
        logger.exception(
            "request failed method=%s path=%s status=500 duration_ms=%.2f",
            request.method,
            request.url.path,
            duration * 1000,
        )
        raise
    finally:
        request_id_context.reset(token)


# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

@app.get("/")
def read_root():
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "running"
    }

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("database health check failed")
        db_status = "error"
        
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "version": settings.VERSION
    }


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics():
    return request_metrics.render()
