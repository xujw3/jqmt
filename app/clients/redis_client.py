from __future__ import annotations

from functools import lru_cache

import redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    settings = get_settings()
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        decode_responses=True,
        socket_connect_timeout=settings.redis_socket_connect_timeout,
        socket_timeout=settings.redis_socket_timeout,
        health_check_interval=settings.redis_health_check_interval,
    )

