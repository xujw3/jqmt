from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import time
from typing import Any, Callable, Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging, reset_request_id, set_request_id
from app.core.metrics import http_request_duration_seconds, http_requests_total
from app.schemas.signal import ErrorResponse, ValidationErrorResponse
from app.services.queue_service import get_queue_service


settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


class StartupQueueService(Protocol):
    def ping(self) -> bool: ...


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = settings
    service_factory: Callable[[], StartupQueueService] = app.state.queue_service_factory
    service = service_factory()
    service.ping()
    logger.info("application startup completed app_name=%s redis=up", settings.app_name)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="聚宽到 QMT 的交易信号中转服务，基于 Redis 实现 pending/processing/ack 可靠消费。",
    lifespan=lifespan,
)
app.state.settings = settings
app.state.queue_service_factory = get_queue_service


@app.middleware("http")
async def record_http_metrics(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id
    token = set_request_id(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        duration = time.perf_counter() - start
        reset_request_id(token)

    path = request.url.path
    response.headers["X-Request-ID"] = request_id
    http_requests_total.labels(method=request.method, path=path, status=str(response.status_code)).inc()
    http_request_duration_seconds.labels(method=request.method, path=path).observe(duration)
    logger.info("request completed method=%s path=%s status=%s duration_ms=%.2f", request.method, path, response.status_code, duration * 1000)
    return response


app.include_router(router)


@app.exception_handler(RedisError)
async def redis_error_handler(_: Request, exc: RedisError) -> JSONResponse:
    logger.exception("redis unavailable: %s", exc)
    return JSONResponse(
        status_code=503,
        content=ErrorResponse(message=f"Redis 不可用：{exc}", error_code="redis_unavailable").model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [dict(item) for item in exc.errors()]
    return JSONResponse(
        status_code=422,
        content=ValidationErrorResponse(
            message="请求参数校验失败",
            error_code="validation_error",
            details=details,
        ).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    error_code_map = {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
    }
    message = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content=ErrorResponse(
            message=message,
            error_code=error_code_map.get(exc.status_code, "http_error"),
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled application error: %s", exc)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(message="服务内部异常", error_code="internal_error").model_dump(),
    )


@app.get("/", tags=["root"])
def root() -> dict:
    return {
        "status": "success",
        "message": "服务已启动",
        "app_name": settings.app_name,
    }
