from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from fastapi.testclient import TestClient
from fastapi import HTTPException
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.main import app
from app.schemas.signal import SignalCreate
from app.services.queue_service import QueueService, get_queue_service
from tests.fakes import FakeRedis


@contextmanager
def build_client():
    redis = FakeRedis()
    service = QueueService(redis, get_settings())

    def override_service() -> QueueService:
        return service

    app.dependency_overrides[get_queue_service] = override_service
    app.state.queue_service_factory = override_service

    with TestClient(app) as client:
        yield client, service

    app.dependency_overrides.clear()
    app.state.queue_service_factory = get_queue_service


def test_get_signals_returns_claimed_items_without_requeue_side_effect() -> None:
    with build_client() as (client, service):
        payload = service.enqueue_signal(
            SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
        )
        service.claim_signals()
        stored = service._load_payload(payload["signal_id"])
        assert stored is not None
        stored["last_delivered_at"] = time.time() - 120
        service._save_payload(stored)

        response = client.get(
            "/api/get_signals",
            headers={"x-token": app.state.settings.secret_token},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 0
        assert body["requeued"] == 0
        assert service.get_queue_status()["processing_count"] == 1
        assert service.get_queue_status()["pending_count"] == 0


def test_requeue_endpoint_requeues_timed_out_signals() -> None:
    with build_client() as (client, service):
        payload = service.enqueue_signal(
            SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
        )
        service.claim_signals()
        stored = service._load_payload(payload["signal_id"])
        assert stored is not None
        stored["last_delivered_at"] = time.time() - 120
        service._save_payload(stored)

        response = client.post(
            "/api/queue/requeue-timeouts",
            headers={"x-token": app.state.settings.secret_token},
        )

        assert response.status_code == 200
        assert response.json()["requeued"] == 1
        assert response.json()["dead_lettered"] == 0
        assert service.get_queue_status()["pending_count"] == 1
        assert service.get_queue_status()["processing_count"] == 0


def test_requeue_endpoint_dead_letters_over_retried_signal() -> None:
    with build_client() as (client, service):
        payload = service.enqueue_signal(
            SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
        )
        service.claim_signals()
        stored = service._load_payload(payload["signal_id"])
        assert stored is not None
        stored["delivery_attempts"] = service.settings.max_delivery_attempts
        stored["last_delivered_at"] = time.time() - 120
        service._save_payload(stored)

        response = client.post(
            "/api/queue/requeue-timeouts",
            headers={"x-token": app.state.settings.secret_token},
        )

        assert response.status_code == 200
        assert response.json()["requeued"] == 0
        assert response.json()["dead_lettered"] == 1
        assert service.get_queue_status()["dead_letter_count"] == 1


def test_health_endpoints_split_liveness_and_readiness() -> None:
    with build_client() as (client, _service):
        livez = client.get("/livez")
        readyz = client.get("/readyz", headers={"x-token": app.state.settings.secret_token})

        assert livez.status_code == 200
        assert livez.json()["redis"] == "unknown"
        assert readyz.status_code == 200
        assert readyz.json()["redis"] == "up"


def test_validation_error_is_shaped_by_global_handler() -> None:
    with build_client() as (client, _service):
        response = client.post(
            "/webhook/signal",
            headers={"x-token": app.state.settings.secret_token},
            json={"symbol": "000001.XSHE"},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["status"] == "error"
        assert body["error_code"] == "validation_error"
        assert body["details"]


def test_redis_error_is_shaped_by_global_handler() -> None:
    class RequestRedisFailingService:
        def ping(self) -> bool:
            raise RedisError("boom")

    def override_service() -> RequestRedisFailingService:
        return RequestRedisFailingService()

    try:
        with build_client() as (client, _service):
            app.dependency_overrides[get_queue_service] = override_service
            response = client.get("/readyz", headers={"x-token": app.state.settings.secret_token})

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "error"
        assert body["error_code"] == "redis_unavailable"
    finally:
        app.dependency_overrides.clear()


def test_http_exception_is_shaped_by_global_handler() -> None:
    with build_client() as (client, _service):
        response = client.get("/api/get_signals", headers={"x-token": "wrong-token"})

        assert response.status_code == 401
        body = response.json()
        assert body["status"] == "error"
        assert body["error_code"] == "unauthorized"
        assert body["message"] == "Token 无效，拒绝访问。"


def test_http_exception_handler_preserves_headers() -> None:
    @app.get("/__test-http-exception")
    def raise_http_exception() -> None:
        raise HTTPException(status_code=403, detail="forbidden", headers={"X-Test": "1"})

    try:
        with build_client() as (client, _service):
            response = client.get("/__test-http-exception")

        assert response.status_code == 403
        assert response.headers["X-Test"] == "1"
        assert response.json()["error_code"] == "forbidden"
    finally:
        app.router.routes.pop()


def test_dead_letter_listing_and_replay_routes() -> None:
    with build_client() as (client, service):
        payload = service.enqueue_signal(
            SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
        )
        service.claim_signals()
        stored = service._load_payload(payload["signal_id"])
        assert stored is not None
        stored["delivery_attempts"] = service.settings.max_delivery_attempts
        stored["last_delivered_at"] = time.time() - 120
        service._save_payload(stored)
        service.requeue_timed_out_signals()

        list_response = client.get(
            "/api/queue/dead-letter",
            headers={"x-token": app.state.settings.secret_token},
        )

        assert list_response.status_code == 200
        list_body = list_response.json()
        assert list_body["count"] == 1
        assert list_body["signals"][0]["status"] == "dead_letter"
        assert list_body["signals"][0]["extra"]["dead_letter_reason"]

        replay_response = client.post(
            "/api/queue/dead-letter/replay",
            headers={"x-token": app.state.settings.secret_token},
            json={"signal_ids": [payload["signal_id"]]},
        )

        assert replay_response.status_code == 200
        replay_body = replay_response.json()
        assert replay_body["replayed"] == [payload["signal_id"]]
        assert replay_body["missing"] == []
        assert replay_body["not_dead_letter"] == []
        assert service.get_queue_status()["dead_letter_count"] == 0
        assert service.get_queue_status()["pending_count"] == 1

        claim_response = client.get(
            "/api/get_signals",
            headers={"x-token": app.state.settings.secret_token},
        )

        assert claim_response.status_code == 200
        claim_body = claim_response.json()
        assert claim_body["count"] == 1
        assert claim_body["signals"][0]["signal_id"] == payload["signal_id"]
        assert claim_body["signals"][0]["delivery_attempts"] == 1


def test_metrics_endpoint_exposes_prometheus_metrics() -> None:
    with build_client() as (client, service):
        service.enqueue_signal(
            SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
        )

        response = client.get("/metrics", headers={"x-token": app.state.settings.secret_token})

        assert response.status_code == 200
        assert "fastapi4jq_signals_enqueued_total" in response.text
        assert "fastapi4jq_queue_depth" in response.text


def test_metrics_endpoint_refreshes_queue_depth_from_current_redis_state() -> None:
    with build_client() as (client, service):
        first = service.enqueue_signal(
            SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
        )
        second = service.enqueue_signal(
            SignalCreate(symbol="000002.XSHE", action="sell", volume=100, strategy_id="demo")
        )
        service.claim_signals(limit=1)

        stored = service._load_payload(first["signal_id"])
        assert stored is not None
        stored["delivery_attempts"] = service.settings.max_delivery_attempts
        stored["last_delivered_at"] = time.time() - 120
        service._save_payload(stored)
        service.requeue_timed_out_signals()

        response = client.get("/metrics", headers={"x-token": app.state.settings.secret_token})

        assert response.status_code == 200
        text = response.text
        assert 'fastapi4jq_queue_depth{queue="pending"} 1.0' in text
        assert 'fastapi4jq_queue_depth{queue="processing"} 0.0' in text
        assert 'fastapi4jq_queue_depth{queue="dead_letter"} 1.0' in text


def test_request_id_is_returned_on_success_response() -> None:
    with build_client() as (client, _service):
        response = client.get("/livez", headers={"X-Request-ID": "req-success-1"})

        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "req-success-1"


def test_request_id_is_returned_on_error_response() -> None:
    with build_client() as (client, _service):
        response = client.get("/api/get_signals", headers={"x-token": "wrong-token", "X-Request-ID": "req-error-1"})

        assert response.status_code == 401
        assert response.headers["X-Request-ID"] == "req-error-1"


def test_request_id_is_generated_when_absent() -> None:
    with build_client() as (client, _service):
        response = client.get("/livez")

        assert response.status_code == 200
        request_id = response.headers["X-Request-ID"]
        assert request_id
        assert request_id != "-"


def test_logs_include_request_id_for_queue_operations(caplog) -> None:
    with build_client() as (client, _service):
        with caplog.at_level(logging.INFO):
            response = client.post(
                "/webhook/signal",
                headers={"x-token": app.state.settings.secret_token, "X-Request-ID": "req-log-1"},
                json={"symbol": "000001.XSHE", "action": "buy", "volume": 100, "strategy_id": "demo"},
            )

        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "req-log-1"
        assert any(getattr(record, "request_id", None) == "req-log-1" for record in caplog.records)
        assert any("signal enqueued" in record.getMessage() for record in caplog.records)


def test_startup_fails_fast_when_redis_ping_fails() -> None:
    class FailingService:
        def ping(self) -> bool:
            raise RuntimeError("redis down")

    app.state.queue_service_factory = lambda: FailingService()

    try:
        try:
            with TestClient(app):
                raise AssertionError("startup should have failed")
        except RuntimeError as exc:
            assert "redis down" in str(exc)
    finally:
        app.state.queue_service_factory = get_queue_service
