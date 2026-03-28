from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


def load_env_file() -> None:
    if not ENV_FILE.exists():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _require_positive(name: str, value: int) -> int:
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0，当前值为 {value}")
    return value


def _require_non_negative(name: str, value: int) -> int:
    if value < 0:
        raise ValueError(f"{name} 不能小于 0，当前值为 {value}")
    return value


class Settings:
    def __init__(self) -> None:
        load_env_file()
        self.app_name = os.getenv("APP_NAME", "量化交易信号中转 API")
        self.app_host = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port = _require_positive("APP_PORT", _get_int("APP_PORT", 8000))
        self.secret_token = os.getenv("SECRET_TOKEN", "change_me")

        self.redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
        self.redis_port = _require_positive("REDIS_PORT", _get_int("REDIS_PORT", 6379))
        self.redis_db = _require_non_negative("REDIS_DB", _get_int("REDIS_DB", 0))
        self.redis_password = os.getenv("REDIS_PASSWORD", "") or None
        self.redis_socket_connect_timeout = _require_positive(
            "REDIS_SOCKET_CONNECT_TIMEOUT",
            _get_int("REDIS_SOCKET_CONNECT_TIMEOUT", 5),
        )
        self.redis_socket_timeout = _require_positive(
            "REDIS_SOCKET_TIMEOUT",
            _get_int("REDIS_SOCKET_TIMEOUT", 5),
        )
        self.redis_health_check_interval = _require_positive(
            "REDIS_HEALTH_CHECK_INTERVAL",
            _get_int("REDIS_HEALTH_CHECK_INTERVAL", 30),
        )

        self.queue_pending_name = os.getenv("QUEUE_PENDING_NAME", "jq_signals:pending")
        self.queue_processing_name = os.getenv("QUEUE_PROCESSING_NAME", "jq_signals:processing")
        self.queue_payload_hash_name = os.getenv("QUEUE_PAYLOAD_HASH_NAME", "jq_signals:payloads")
        self.queue_dead_letter_name = os.getenv("QUEUE_DEAD_LETTER_NAME", "jq_signals:dead_letter")

        self.poll_batch_size = _require_positive("POLL_BATCH_SIZE", _get_int("POLL_BATCH_SIZE", 20))
        self.ack_timeout_seconds = _require_positive(
            "ACK_TIMEOUT_SECONDS",
            _get_int("ACK_TIMEOUT_SECONDS", 60),
        )
        self.max_delivery_attempts = _require_positive(
            "MAX_DELIVERY_ATTEMPTS",
            _get_int("MAX_DELIVERY_ATTEMPTS", 3),
        )
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        self._validate()

    def _validate(self) -> None:
        if self.secret_token in {"", "change_me", "change_me_to_a_long_random_string"}:
            raise ValueError("SECRET_TOKEN 不能使用默认值，请配置高强度密钥")
        if len(self.secret_token) < 16:
            raise ValueError("SECRET_TOKEN 长度至少为 16 个字符")
        if not self.redis_host.strip():
            raise ValueError("REDIS_HOST 不能为空")
        queue_names = {
            self.queue_pending_name,
            self.queue_processing_name,
            self.queue_payload_hash_name,
            self.queue_dead_letter_name,
        }
        if len(queue_names) != 4:
            raise ValueError("队列相关 Redis key 必须互不相同")
        if self.poll_batch_size > 100:
            raise ValueError("POLL_BATCH_SIZE 不能超过 100")
        if self.log_level not in VALID_LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL 必须是 {', '.join(sorted(VALID_LOG_LEVELS))} 之一，当前值为 {self.log_level}"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()

