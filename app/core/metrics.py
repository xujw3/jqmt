from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest


registry = CollectorRegistry()

http_requests_total = Counter(
    "fastapi4jq_http_requests_total",
    "Total HTTP requests",
    labelnames=("method", "path", "status"),
    registry=registry,
)

http_request_duration_seconds = Histogram(
    "fastapi4jq_http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=("method", "path"),
    registry=registry,
)

signals_enqueued_total = Counter(
    "fastapi4jq_signals_enqueued_total",
    "Total signals enqueued",
    registry=registry,
)

signals_claimed_total = Counter(
    "fastapi4jq_signals_claimed_total",
    "Total signals claimed by consumers",
    registry=registry,
)

signals_acked_total = Counter(
    "fastapi4jq_signals_acked_total",
    "Total signals acknowledged",
    registry=registry,
)

signal_ack_missing_total = Counter(
    "fastapi4jq_signal_ack_missing_total",
    "Total ack requests for missing signals",
    registry=registry,
)

signal_ack_not_processing_total = Counter(
    "fastapi4jq_signal_ack_not_processing_total",
    "Total ack requests for signals not in processing",
    registry=registry,
)

signals_requeued_total = Counter(
    "fastapi4jq_signals_requeued_total",
    "Total timed-out signals requeued",
    registry=registry,
)

signals_dead_lettered_total = Counter(
    "fastapi4jq_signals_dead_lettered_total",
    "Total signals moved to dead-letter queue",
    registry=registry,
)

signals_replayed_total = Counter(
    "fastapi4jq_signals_replayed_total",
    "Total signals replayed from dead-letter queue",
    registry=registry,
)

queue_depth = Gauge(
    "fastapi4jq_queue_depth",
    "Current queue depth by queue type",
    labelnames=("queue",),
    registry=registry,
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
