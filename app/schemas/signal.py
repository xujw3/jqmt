from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SignalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, description="证券代码")
    action: str = Field(..., description="交易动作，仅支持 buy/sell")
    volume: int = Field(..., gt=0, description="交易数量")
    price: float | None = Field(default=None, gt=0, description="委托价格，可为空")
    strategy_id: str = Field(..., min_length=1, description="策略标识")
    source: str = Field(default="jq", min_length=1, description="信号来源")
    sent_at: float | None = Field(default=None, ge=0, description="源系统发出时间戳")
    extra: dict[str, Any] = Field(default_factory=dict, description="扩展字段")

    @field_validator("symbol", "strategy_id", "source")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("字段不能为空")
        return cleaned

    @field_validator("action")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"buy", "sell"}:
            raise ValueError("action 仅支持 buy 或 sell")
        return cleaned


class SignalMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    symbol: str
    action: str
    volume: int
    price: float | None = None
    strategy_id: str
    source: str
    sent_at: float | None = None
    received_at: float
    status: str
    delivery_attempts: int = 0
    last_delivered_at: float | None = None
    requeued_at: float | None = None
    dead_lettered_at: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SignalEnqueueResponse(BaseModel):
    status: str = "success"
    message: str
    data: SignalMessage


class SignalPullResponse(BaseModel):
    status: str = "success"
    message: str
    count: int
    requeued: int
    signals: list[SignalMessage]


class AckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_ids: list[str] = Field(..., min_length=1, description="需要确认的 signal_id 列表")

    @field_validator("signal_ids")
    @classmethod
    def normalize_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()

        for value in values:
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("signal_id 不能为空")
            if cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)

        if not normalized:
            raise ValueError("signal_ids 不能为空")

        return normalized


class AckResponse(BaseModel):
    status: str = "success"
    message: str
    acked: list[str]
    missing: list[str]
    not_processing: list[str]


class QueueStatusResponse(BaseModel):
    status: str = "success"
    pending_count: int
    processing_count: int
    payload_count: int
    dead_letter_count: int
    ack_timeout_seconds: int
    max_delivery_attempts: int


class HealthResponse(BaseModel):
    status: str = "success"
    service: str
    redis: str


class RequeueResponse(BaseModel):
    status: str = "success"
    message: str
    requeued: int
    dead_lettered: int


class DeadLetterReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_ids: list[str] = Field(..., min_length=1, description="需要重放的 dead-letter signal_id 列表")

    @field_validator("signal_ids")
    @classmethod
    def normalize_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()

        for value in values:
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("signal_id 不能为空")
            if cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)

        if not normalized:
            raise ValueError("signal_ids 不能为空")

        return normalized


class DeadLetterListResponse(BaseModel):
    status: str = "success"
    message: str
    count: int
    signals: list[SignalMessage]


class DeadLetterReplayResponse(BaseModel):
    status: str = "success"
    message: str
    replayed: list[str]
    missing: list[str]
    not_dead_letter: list[str]


class ErrorResponse(BaseModel):
    status: str = "error"
    message: str
    error_code: str


class ValidationErrorResponse(ErrorResponse):
    details: list[dict[str, Any]]

