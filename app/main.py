"""Scrappy Pro API.

Security posture in one place:
  * CORS is an explicit allow-list, never ``*``.
  * Every route except /health and /auth/login requires a bearer token.
  * A global rate limit protects the login endpoint from credential stuffing.
  * Security headers are set on every response.
  * Unhandled exceptions return an opaque message; details go to the log only.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.api.v1 import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.services.seed import run_all

configure_logging()
log = get_logger(__name__)

limiter = Limiter(key_func=get_remote_address, default_limits=["300/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.arq = await create_pool(RedisSettings.from_dsn(str(settings.REDIS_URL)))
    async with SessionLocal() as db:
        await run_all(db)
    log.info("app.started", env=settings.ENV)
    yield
    await app.state.arq.close()
    log.info("app.stopped")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Bulk product data collection for Bangladeshi electronics retailers.",
    docs_url="/docs" if settings.ENV != "production" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.ENV != "production" else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


@app.middleware("http")
async def security_and_timing(request: Request, call_next):  # noqa: ANN001, ANN201
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if settings.ENV == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("request.unhandled", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our side. The team has been notified."},
    )


app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"name": settings.APP_NAME, "docs": "/docs", "health": f"{settings.API_V1_PREFIX}/health"}
