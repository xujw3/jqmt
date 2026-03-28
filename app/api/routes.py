from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.metrics import render_metrics
from app.core.security import verify_token
from app.schemas.signal import (
    AckRequest,
    AckResponse,
    DeadLetterListResponse,
    DeadLetterReplayRequest,
    DeadLetterReplayResponse,
    HealthResponse,
    QueueStatusResponse,
    RequeueResponse,
    SignalCreate,
    SignalEnqueueResponse,
    SignalMessage,
    SignalPullResponse,
)
from app.services.queue_service import QueueService, get_queue_service


router = APIRouter()

TokenDep = Annotated[str, Depends(verify_token)]
ServiceDep = Annotated[QueueService, Depends(get_queue_service)]


@router.post("/webhook/signal", response_model=SignalEnqueueResponse, tags=["signals"])
def receive_signal(
    signal: SignalCreate,
    _: TokenDep,
    service: ServiceDep,
) -> SignalEnqueueResponse:
    payload = service.enqueue_signal(signal)

    return SignalEnqueueResponse(
        message="信号已接收并写入 pending 队列",
        data=SignalMessage.model_validate(payload),
    )


@router.get("/api/get_signals", response_model=SignalPullResponse, tags=["signals"])
def get_signals(
    _: TokenDep,
    service: ServiceDep,
    limit: int | None = Query(default=None, ge=1, le=100),
) -> SignalPullResponse:
    signals, requeued = service.claim_signals(limit)

    return SignalPullResponse(
        message="信号拉取完成",
        count=len(signals),
        requeued=requeued,
        signals=[SignalMessage.model_validate(item) for item in signals],
    )


@router.post("/api/ack_signals", response_model=AckResponse, tags=["signals"])
def ack_signals(
    request: AckRequest,
    _: TokenDep,
    service: ServiceDep,
) -> AckResponse:
    result = service.ack_signals(request.signal_ids)

    return AckResponse(**result)


@router.post("/api/queue/requeue-timeouts", response_model=RequeueResponse, tags=["monitoring"])
def requeue_timeouts(
    _: TokenDep,
    service: ServiceDep,
) -> RequeueResponse:
    requeued, dead_lettered = service.requeue_timed_out_signals()

    return RequeueResponse(
        message="超时信号处理完成",
        requeued=requeued,
        dead_lettered=dead_lettered,
    )


@router.get("/api/queue/status", response_model=QueueStatusResponse, tags=["monitoring"])
def queue_status(
    _: TokenDep,
    service: ServiceDep,
) -> QueueStatusResponse:
    result = service.get_queue_status()

    return QueueStatusResponse(**result)


@router.get("/api/queue/dead-letter", response_model=DeadLetterListResponse, tags=["monitoring"])
def list_dead_letter(
    _: TokenDep,
    service: ServiceDep,
    limit: int | None = Query(default=None, ge=1, le=100),
) -> DeadLetterListResponse:
    signals = service.list_dead_letter_signals(limit)
    return DeadLetterListResponse(
        message="死信列表获取完成",
        count=len(signals),
        signals=[SignalMessage.model_validate(item) for item in signals],
    )


@router.post("/api/queue/dead-letter/replay", response_model=DeadLetterReplayResponse, tags=["monitoring"])
def replay_dead_letter(
    request: DeadLetterReplayRequest,
    _: TokenDep,
    service: ServiceDep,
) -> DeadLetterReplayResponse:
    result = service.replay_dead_letter_signals(request.signal_ids)
    return DeadLetterReplayResponse(**result)


@router.get("/livez", response_model=HealthResponse, tags=["monitoring"])
def livez() -> HealthResponse:
    return HealthResponse(service="up", redis="unknown")


@router.get("/readyz", response_model=HealthResponse, tags=["monitoring"])
def readyz(service: ServiceDep) -> HealthResponse:
    service.ping()
    return HealthResponse(service="up", redis="up")


@router.get("/metrics", tags=["monitoring"])
def metrics(_: TokenDep, service: QueueService = Depends(get_queue_service)) -> Response:
    service.sync_metrics()
    content, media_type = render_metrics()
    return Response(content=content, media_type=media_type)

