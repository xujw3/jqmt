from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import uuid4

from app.clients.redis_client import get_redis
from app.core.config import Settings, get_settings
from app.core.metrics import (
    queue_depth,
    signal_ack_missing_total,
    signal_ack_not_processing_total,
    signals_acked_total,
    signals_claimed_total,
    signals_dead_lettered_total,
    signals_enqueued_total,
    signals_replayed_total,
    signals_requeued_total,
)
from app.schemas.signal import SignalCreate


logger = logging.getLogger(__name__)


class QueueService:
    def __init__(self, redis_client: Any, settings: Settings) -> None:
        self.redis = redis_client
        self.settings = settings

    @property
    def pending_queue(self) -> str:
        return self.settings.queue_pending_name

    @property
    def processing_queue(self) -> str:
        return self.settings.queue_processing_name

    @property
    def payload_hash(self) -> str:
        return self.settings.queue_payload_hash_name

    @property
    def dead_letter_queue(self) -> str:
        return self.settings.queue_dead_letter_name

    def ping(self) -> bool:
        return bool(self.redis.ping())

    def enqueue_signal(self, signal: SignalCreate) -> dict:
        now = time.time()
        payload = {
            **signal.model_dump(),
            "signal_id": str(uuid4()),
            "received_at": now,
            "status": "pending",
            "delivery_attempts": 0,
            "last_delivered_at": None,
            "requeued_at": None,
            "dead_lettered_at": None,
        }

        with self.redis.pipeline() as pipe:
            pipe.hset(self.payload_hash, payload["signal_id"], self._serialize(payload))
            pipe.lpush(self.pending_queue, payload["signal_id"])
            pipe.execute()

        logger.info(
            "signal enqueued signal_id=%s strategy_id=%s source=%s",
            payload["signal_id"],
            payload["strategy_id"],
            payload["source"],
        )
        signals_enqueued_total.inc()
        self._update_queue_depth_metrics()

        return payload

    def claim_signals(self, limit: int | None = None) -> tuple[list[dict], int]:
        batch_size = self.settings.poll_batch_size if limit is None else min(limit, self.settings.poll_batch_size)
        batch_size = max(1, batch_size)
        claimed: list[dict] = []

        for _ in range(batch_size):
            signal_id = self.redis.rpoplpush(self.pending_queue, self.processing_queue)
            if signal_id is None:
                break

            payload = self._load_payload(signal_id)
            if payload is None:
                self.redis.lrem(self.processing_queue, 1, signal_id)
                continue

            claimed_at = time.time()
            delivery_attempts = int(payload.get("delivery_attempts", 0)) + 1
            payload["delivery_attempts"] = delivery_attempts

            if delivery_attempts > self.settings.max_delivery_attempts:
                self._move_to_dead_letter(signal_id, payload, reason="delivery attempts exceeded while claiming")
                continue

            payload["status"] = "processing"
            payload["last_delivered_at"] = claimed_at
            payload["requeued_at"] = None
            payload["dead_lettered_at"] = None
            self._save_payload(payload)
            claimed.append(payload)

        if claimed:
            logger.info(
                "signals claimed count=%s signal_ids=%s",
                len(claimed),
                ",".join(item["signal_id"] for item in claimed),
            )
            signals_claimed_total.inc(len(claimed))
        else:
            logger.debug("no signals claimed batch_size=%s", batch_size)

        self._update_queue_depth_metrics()

        return claimed, 0

    def ack_signals(self, signal_ids: list[str]) -> dict:
        acked: list[str] = []
        missing: list[str] = []
        not_processing: list[str] = []

        for signal_id in signal_ids:
            with self.redis.pipeline() as pipe:
                pipe.hexists(self.payload_hash, signal_id)
                pipe.lrem(self.processing_queue, 1, signal_id)
                payload_exists, removed = pipe.execute()

            if removed:
                self.redis.hdel(self.payload_hash, signal_id)
                acked.append(signal_id)
            elif payload_exists:
                not_processing.append(signal_id)
            else:
                missing.append(signal_id)

        logger.info(
            "signals ack result acked=%s missing=%s not_processing=%s",
            len(acked),
            len(missing),
            len(not_processing),
        )
        signals_acked_total.inc(len(acked))
        signal_ack_missing_total.inc(len(missing))
        signal_ack_not_processing_total.inc(len(not_processing))
        self._update_queue_depth_metrics()

        return {
            "message": "确认完成",
            "acked": acked,
            "missing": missing,
            "not_processing": not_processing,
        }

    def requeue_timed_out_signals(self) -> tuple[int, int]:
        timeout = self.settings.ack_timeout_seconds
        if timeout <= 0:
            return 0, 0

        now = time.time()
        requeued = 0
        dead_lettered = 0
        signal_ids = self.redis.lrange(self.processing_queue, 0, -1)

        for signal_id in signal_ids:
            payload = self._load_payload(signal_id)
            if payload is None:
                self.redis.lrem(self.processing_queue, 1, signal_id)
                continue

            last_delivered_at = payload.get("last_delivered_at")
            if last_delivered_at is None:
                last_delivered_at = payload.get("received_at") or payload.get("requeued_at") or 0

            try:
                delivered_at = float(last_delivered_at)
            except (TypeError, ValueError):
                delivered_at = 0

            if now - delivered_at < timeout:
                continue

            if int(payload.get("delivery_attempts", 0)) >= self.settings.max_delivery_attempts:
                self._move_to_dead_letter(signal_id, payload, reason="delivery attempts exceeded while requeueing")
                dead_lettered += 1
                continue

            payload["status"] = "pending"
            payload["requeued_at"] = now
            payload["last_delivered_at"] = None

            with self.redis.pipeline() as pipe:
                pipe.lrem(self.processing_queue, 1, signal_id)
                removed = pipe.execute()[0]

            if not removed:
                continue

            with self.redis.pipeline() as pipe:
                pipe.hset(self.payload_hash, payload["signal_id"], self._serialize(payload))
                pipe.rpush(self.pending_queue, signal_id)
                pipe.execute()
            requeued += 1

        if requeued:
            logger.warning("timed out signals requeued count=%s", requeued)
            signals_requeued_total.inc(requeued)

        if dead_lettered:
            logger.error("signals moved to dead letter count=%s", dead_lettered)

        self._update_queue_depth_metrics()

        return requeued, dead_lettered

    def get_queue_status(self) -> dict:
        self._update_queue_depth_metrics()
        return {
            "pending_count": self.redis.llen(self.pending_queue),
            "processing_count": self.redis.llen(self.processing_queue),
            "payload_count": self.redis.hlen(self.payload_hash),
            "dead_letter_count": self.redis.llen(self.dead_letter_queue),
            "ack_timeout_seconds": self.settings.ack_timeout_seconds,
            "max_delivery_attempts": self.settings.max_delivery_attempts,
        }

    def list_dead_letter_signals(self, limit: int | None = None) -> list[dict]:
        signal_ids = self.redis.lrange(self.dead_letter_queue, 0, -1)
        if limit is not None:
            signal_ids = signal_ids[:limit]

        signals: list[dict] = []
        for signal_id in signal_ids:
            payload = self._load_payload(signal_id)
            if payload is None:
                self.redis.lrem(self.dead_letter_queue, 1, signal_id)
                continue
            signals.append(payload)

        return signals

    def replay_dead_letter_signals(self, signal_ids: list[str]) -> dict:
        replayed: list[str] = []
        missing: list[str] = []
        not_dead_letter: list[str] = []

        for signal_id in signal_ids:
            payload = self._load_payload(signal_id)
            if payload is None:
                self.redis.lrem(self.dead_letter_queue, 1, signal_id)
                missing.append(signal_id)
                continue

            with self.redis.pipeline() as pipe:
                pipe.lrem(self.dead_letter_queue, 1, signal_id)
                removed = pipe.execute()[0]

            if not removed:
                not_dead_letter.append(signal_id)
                continue

            payload["status"] = "pending"
            payload["delivery_attempts"] = 0
            payload["requeued_at"] = time.time()
            payload["last_delivered_at"] = None
            payload["dead_lettered_at"] = None
            extra = payload.setdefault("extra", {})
            if "dead_letter_reason" in extra:
                extra["last_dead_letter_reason"] = extra.pop("dead_letter_reason")
            extra["replayed_from_dead_letter"] = True

            with self.redis.pipeline() as pipe:
                pipe.hset(self.payload_hash, payload["signal_id"], self._serialize(payload))
                pipe.rpush(self.pending_queue, signal_id)
                pipe.execute()

            replayed.append(signal_id)

        logger.info(
            "dead letter replay result replayed=%s missing=%s not_dead_letter=%s",
            len(replayed),
            len(missing),
            len(not_dead_letter),
        )
        signals_replayed_total.inc(len(replayed))
        self._update_queue_depth_metrics()

        return {
            "message": "死信重放完成",
            "replayed": replayed,
            "missing": missing,
            "not_dead_letter": not_dead_letter,
        }

    def sync_metrics(self) -> None:
        self._update_queue_depth_metrics()

    def _move_to_dead_letter(self, signal_id: str, payload: dict, reason: str) -> None:
        payload["status"] = "dead_letter"
        payload["dead_lettered_at"] = time.time()
        payload.setdefault("extra", {})["dead_letter_reason"] = reason
        payload["last_delivered_at"] = None

        with self.redis.pipeline() as pipe:
            pipe.lrem(self.processing_queue, 1, signal_id)
            removed = pipe.execute()[0]

        if not removed:
            return

        with self.redis.pipeline() as pipe:
            pipe.hset(self.payload_hash, payload["signal_id"], self._serialize(payload))
            pipe.rpush(self.dead_letter_queue, signal_id)
            pipe.execute()

        logger.error("signal moved to dead letter signal_id=%s reason=%s", signal_id, reason)
        signals_dead_lettered_total.inc()
        self._update_queue_depth_metrics()

    def _load_payload(self, signal_id: str) -> dict | None:
        raw = self.redis.hget(self.payload_hash, signal_id)
        if raw is None:
            return None
        return json.loads(raw)

    def _save_payload(self, payload: dict) -> None:
        self.redis.hset(self.payload_hash, payload["signal_id"], self._serialize(payload))

    def _update_queue_depth_metrics(self) -> None:
        queue_depth.labels(queue="pending").set(self.redis.llen(self.pending_queue))
        queue_depth.labels(queue="processing").set(self.redis.llen(self.processing_queue))
        queue_depth.labels(queue="dead_letter").set(self.redis.llen(self.dead_letter_queue))

    @staticmethod
    def _serialize(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False)


def get_queue_service() -> QueueService:
    return QueueService(get_redis(), get_settings())

