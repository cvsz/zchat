"""FastAPI application factory."""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from . import __version__
from .api import router
from .config import Settings
from .db import make_engine
from .domain_api import router as domain_router
from .observability import StructuredLogger, log_http_request, log_startup_event, redact_db_url
from .rate_limit import SlidingWindowRateLimiter
from .zworkforce_mcp import router as zworkforce_mcp_router


def create_app(settings: Settings | None = None, *, engine=None) -> FastAPI:
    settings = settings or Settings.from_env()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.getLogger("camfrog_control_server").setLevel(log_level)

    owns_engine = engine is None
    engine = engine or make_engine(settings.database_url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log_startup_event(
            request_logger,
            version=__version__,
            db_url_redacted=redact_db_url(app.state.settings.database_url),
        )
        yield
        if app.state.owns_engine:
            app.state.engine.dispose()

    app = FastAPI(
        title="ZChat Control Server",
        version=__version__,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.owns_engine = owns_engine
    app.state.rate_limiter = SlidingWindowRateLimiter()
    request_logger = StructuredLogger(logging.getLogger("camfrog_control_server.http"))

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    if settings.trusted_hosts:
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=list(settings.trusted_hosts),
        )

    static_dir = Path(__file__).parent / "static"
    app.mount(
        "/dashboard/assets",
        StaticFiles(directory=static_dir, check_dir=True),
        name="dashboard-assets",
    )

    @app.get("/dashboard", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(
            static_dir / "dashboard.html",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.middleware("http")
    async def secure_dashboard_headers(request: Request, call_next):
        incoming_id = request.headers.get("x-request-id", "")
        request_id = (
            incoming_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", incoming_id) else uuid4().hex
        )
        request.state.request_id = request_id
        started = time.monotonic()

        try:
            if request.url.path.startswith("/api/v1/"):
                bucket = (
                    "auth"
                    if request.url.path.endswith("/auth/session")
                    else ("agent" if "/agent/" in request.url.path else "admin")
                )
                peer = request.client.host if request.client is not None else "unknown"
                limit = (
                    settings.auth_rate_limit_requests
                    if bucket == "auth"
                    else settings.api_rate_limit_requests
                )
                allowed, retry_after = await app.state.rate_limiter.allow(
                    f"{bucket}:{peer}", limit=limit
                )
                if not allowed:
                    response = JSONResponse(
                        status_code=429,
                        content={"detail": "Rate limit exceeded."},
                        headers={"Retry-After": str(retry_after)},
                    )
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        except Exception as exc:
            request_logger.error(
                "request_failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                error_type=type(exc).__name__,
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error."},
            )

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        log_http_request(
            request_logger,
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/dashboard" or request.url.path.startswith("/dashboard/assets/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            )
        return response

    health = APIRouter()

    @health.get("/healthz")
    @health.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @health.get("/readyz")
    @health.get("/health/ready")
    def ready(request: Request) -> dict[str, str]:
        with request.app.state.session_factory() as session:
            try:
                session.execute(text("SELECT 1"))
            except SQLAlchemyError as exc:
                raise HTTPException(status_code=503, detail="Database is unavailable.") from exc
        return {"status": "ready"}

    app.include_router(health)
    app.include_router(router, prefix="/api/v1")
    app.include_router(domain_router, prefix="/api/v1")
    app.include_router(zworkforce_mcp_router, prefix="/api/v1")
    return app
