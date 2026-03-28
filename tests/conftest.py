from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.update(
    {
        "SECRET_TOKEN": "test_secret_token_12345",
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "QUEUE_PENDING_NAME": "test:pending",
        "QUEUE_PROCESSING_NAME": "test:processing",
        "QUEUE_PAYLOAD_HASH_NAME": "test:payloads",
        "POLL_BATCH_SIZE": "20",
        "ACK_TIMEOUT_SECONDS": "60",
        "LOG_LEVEL": "INFO",
        "REDIS_SOCKET_CONNECT_TIMEOUT": "5",
        "REDIS_SOCKET_TIMEOUT": "5",
        "REDIS_HEALTH_CHECK_INTERVAL": "30",
    }
)


@pytest.fixture(autouse=True)
def reset_settings_cache() -> Generator[None, None, None]:
    os.environ.update(
        {
            "SECRET_TOKEN": "test_secret_token_12345",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "6379",
            "REDIS_DB": "0",
            "QUEUE_PENDING_NAME": "test:pending",
            "QUEUE_PROCESSING_NAME": "test:processing",
            "QUEUE_PAYLOAD_HASH_NAME": "test:payloads",
            "QUEUE_DEAD_LETTER_NAME": "test:dead-letter",
            "POLL_BATCH_SIZE": "20",
            "ACK_TIMEOUT_SECONDS": "60",
            "MAX_DELIVERY_ATTEMPTS": "3",
            "LOG_LEVEL": "INFO",
            "REDIS_SOCKET_CONNECT_TIMEOUT": "5",
            "REDIS_SOCKET_TIMEOUT": "5",
            "REDIS_HEALTH_CHECK_INTERVAL": "30",
        }
    )

    from app.clients.redis_client import get_redis
    from app.core.config import get_settings

    get_settings.cache_clear()
    get_redis.cache_clear()

    yield

    get_settings.cache_clear()
    get_redis.cache_clear()
