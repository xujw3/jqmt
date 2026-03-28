from __future__ import annotations

import os
import time

from app.core.config import get_settings
from app.schemas.signal import SignalCreate
from app.services.queue_service import QueueService
from tests.fakes import FakeRedis, hash_keys, list_values


def build_service() -> tuple[QueueService, FakeRedis]:
    redis = FakeRedis()
    service = QueueService(redis, get_settings())
    return service, redis


def test_claim_signals_does_not_requeue_timeouts_implicitly() -> None:
    service, redis = build_service()
    payload = service.enqueue_signal(
        SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
    )

    claimed, requeued = service.claim_signals()

    assert len(claimed) == 1
    assert requeued == 0

    stored = service._load_payload(payload["signal_id"])
    assert stored is not None
    stored["last_delivered_at"] = time.time() - 120
    service._save_payload(stored)

    claimed_again, requeued_again = service.claim_signals()

    assert claimed_again == []
    assert requeued_again == 0
    assert list_values(redis, service.processing_queue) == [payload["signal_id"]]
    assert list_values(redis, service.pending_queue) == []


def test_requeue_timed_out_signals_moves_processing_back_to_pending() -> None:
    service, redis = build_service()
    payload = service.enqueue_signal(
        SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
    )
    service.claim_signals()

    stored = service._load_payload(payload["signal_id"])
    assert stored is not None
    stored["last_delivered_at"] = time.time() - 120
    service._save_payload(stored)

    requeued, dead_lettered = service.requeue_timed_out_signals()

    assert requeued == 1
    assert dead_lettered == 0
    assert list_values(redis, service.processing_queue) == []
    assert list_values(redis, service.pending_queue) == [payload["signal_id"]]


def test_requeue_timed_out_signals_recovers_missing_last_delivered_at() -> None:
    service, redis = build_service()
    payload = service.enqueue_signal(
        SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
    )
    service.claim_signals()

    stored = service._load_payload(payload["signal_id"])
    assert stored is not None
    stored["last_delivered_at"] = None
    stored["received_at"] = 0
    service._save_payload(stored)

    requeued, dead_lettered = service.requeue_timed_out_signals()

    assert requeued == 1
    assert dead_lettered == 0
    assert list_values(redis, service.processing_queue) == []
    assert list_values(redis, service.pending_queue) == [payload["signal_id"]]


def test_requeue_timed_out_signals_moves_to_dead_letter_after_max_attempts() -> None:
    service, redis = build_service()
    payload = service.enqueue_signal(
        SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
    )
    service.claim_signals()

    stored = service._load_payload(payload["signal_id"])
    assert stored is not None
    stored["delivery_attempts"] = service.settings.max_delivery_attempts
    stored["last_delivered_at"] = time.time() - 120
    service._save_payload(stored)

    requeued, dead_lettered = service.requeue_timed_out_signals()

    assert requeued == 0
    assert dead_lettered == 1
    assert list_values(redis, service.processing_queue) == []
    assert list_values(redis, service.dead_letter_queue) == [payload["signal_id"]]


def test_claim_signals_moves_over_retried_signal_to_dead_letter() -> None:
    service, redis = build_service()
    payload = service.enqueue_signal(
        SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
    )

    stored = service._load_payload(payload["signal_id"])
    assert stored is not None
    stored["delivery_attempts"] = service.settings.max_delivery_attempts
    service._save_payload(stored)

    claimed, _ = service.claim_signals()

    assert claimed == []
    assert list_values(redis, service.pending_queue) == []
    assert list_values(redis, service.processing_queue) == []
    assert list_values(redis, service.dead_letter_queue) == [payload["signal_id"]]


def test_requeue_does_not_resurrect_signal_removed_from_processing() -> None:
    service, redis = build_service()
    payload = service.enqueue_signal(
        SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
    )
    service.claim_signals()

    stored = service._load_payload(payload["signal_id"])
    assert stored is not None
    stored["last_delivered_at"] = time.time() - 120
    service._save_payload(stored)

    redis.lrem(service.processing_queue, 1, payload["signal_id"])
    redis.hdel(service.payload_hash, payload["signal_id"])

    requeued, dead_lettered = service.requeue_timed_out_signals()

    assert requeued == 0
    assert dead_lettered == 0
    assert list_values(redis, service.pending_queue) == []
    assert list_values(redis, service.processing_queue) == []
    assert list_values(redis, service.dead_letter_queue) == []
    assert payload["signal_id"] not in hash_keys(redis, service.payload_hash)


def test_dead_letter_transition_requires_signal_still_in_processing() -> None:
    service, redis = build_service()
    payload = service.enqueue_signal(
        SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
    )
    service.claim_signals()

    stored = service._load_payload(payload["signal_id"])
    assert stored is not None
    stored["delivery_attempts"] = service.settings.max_delivery_attempts
    stored["last_delivered_at"] = time.time() - 120
    service._save_payload(stored)

    redis.lrem(service.processing_queue, 1, payload["signal_id"])
    redis.hdel(service.payload_hash, payload["signal_id"])

    requeued, dead_lettered = service.requeue_timed_out_signals()

    assert requeued == 0
    assert dead_lettered == 0
    assert list_values(redis, service.dead_letter_queue) == []
    assert payload["signal_id"] not in hash_keys(redis, service.payload_hash)


def test_list_dead_letter_signals_returns_payloads() -> None:
    service, _redis = build_service()
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
    dead_letters = service.list_dead_letter_signals()

    assert len(dead_letters) == 1
    assert dead_letters[0]["signal_id"] == payload["signal_id"]
    assert dead_letters[0]["status"] == "dead_letter"
    assert dead_letters[0]["dead_lettered_at"] is not None
    assert dead_letters[0]["extra"]["dead_letter_reason"]


def test_replay_dead_letter_signals_moves_signal_back_to_pending() -> None:
    service, redis = build_service()
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
    result = service.replay_dead_letter_signals([payload["signal_id"]])

    assert result["replayed"] == [payload["signal_id"]]
    assert result["missing"] == []
    assert result["not_dead_letter"] == []
    assert list_values(redis, service.dead_letter_queue) == []
    assert list_values(redis, service.pending_queue) == [payload["signal_id"]]

    replayed_payload = service._load_payload(payload["signal_id"])
    assert replayed_payload is not None
    assert replayed_payload["status"] == "pending"
    assert replayed_payload["delivery_attempts"] == 0
    assert replayed_payload["dead_lettered_at"] is None
    assert replayed_payload["extra"]["last_dead_letter_reason"]
    assert replayed_payload["extra"]["replayed_from_dead_letter"] is True


def test_replayed_dead_letter_signal_can_be_claimed_again() -> None:
    service, _redis = build_service()
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
    service.replay_dead_letter_signals([payload["signal_id"]])

    claimed, _ = service.claim_signals()

    assert len(claimed) == 1
    assert claimed[0]["signal_id"] == payload["signal_id"]
    assert claimed[0]["delivery_attempts"] == 1


def test_replay_dead_letter_signals_reports_missing_and_not_dead_letter() -> None:
    service, redis = build_service()
    payload = service.enqueue_signal(
        SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
    )

    result = service.replay_dead_letter_signals([payload["signal_id"], "missing-id"])

    assert result["replayed"] == []
    assert result["missing"] == ["missing-id"]
    assert result["not_dead_letter"] == [payload["signal_id"]]
    assert list_values(redis, service.pending_queue) == [payload["signal_id"]]


def test_ack_signals_reports_acked_missing_and_not_processing() -> None:
    service, redis = build_service()
    first = service.enqueue_signal(
        SignalCreate(symbol="000001.XSHE", action="buy", volume=100, strategy_id="demo")
    )
    second = service.enqueue_signal(
        SignalCreate(symbol="000002.XSHE", action="sell", volume=200, strategy_id="demo")
    )

    service.claim_signals(limit=1)

    result = service.ack_signals([first["signal_id"], second["signal_id"], "missing-id"])

    assert result["acked"] == [first["signal_id"]]
    assert result["not_processing"] == [second["signal_id"]]
    assert result["missing"] == ["missing-id"]
    assert first["signal_id"] not in hash_keys(redis, service.payload_hash)


def test_settings_reject_weak_secret_token() -> None:
    os.environ["SECRET_TOKEN"] = "change_me"

    from app.core.config import get_settings

    get_settings.cache_clear()

    try:
        get_settings()
    except ValueError as exc:
        assert "SECRET_TOKEN" in str(exc)
    else:
        raise AssertionError("Expected weak secret token to be rejected")
