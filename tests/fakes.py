from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.operations: list[tuple[str, tuple]] = []

    def __enter__(self) -> "FakePipeline":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def hset(self, name: str, key: str, value: str) -> "FakePipeline":
        self.operations.append(("hset", (name, key, value)))
        return self

    def lpush(self, name: str, value: str) -> "FakePipeline":
        self.operations.append(("lpush", (name, value)))
        return self

    def rpush(self, name: str, value: str) -> "FakePipeline":
        self.operations.append(("rpush", (name, value)))
        return self

    def hexists(self, name: str, key: str) -> "FakePipeline":
        self.operations.append(("hexists", (name, key)))
        return self

    def lrem(self, name: str, count: int, value: str) -> "FakePipeline":
        self.operations.append(("lrem", (name, count, value)))
        return self

    def execute(self) -> list[object]:
        results: list[object] = []
        for operation, args in self.operations:
            method = getattr(self.redis, operation)
            results.append(method(*args))
        self.operations.clear()
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.ping_calls = 0

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def ping(self) -> bool:
        self.ping_calls += 1
        return True

    def hset(self, name: str, key: str, value: str) -> int:
        self.hashes.setdefault(name, {})[key] = value
        return 1

    def hget(self, name: str, key: str) -> str | None:
        return self.hashes.get(name, {}).get(key)

    def hdel(self, name: str, key: str) -> int:
        if key in self.hashes.get(name, {}):
            del self.hashes[name][key]
            return 1
        return 0

    def hexists(self, name: str, key: str) -> bool:
        return key in self.hashes.get(name, {})

    def hlen(self, name: str) -> int:
        return len(self.hashes.get(name, {}))

    def lpush(self, name: str, value: str) -> int:
        self.lists.setdefault(name, []).insert(0, value)
        return len(self.lists[name])

    def rpush(self, name: str, value: str) -> int:
        self.lists.setdefault(name, []).append(value)
        return len(self.lists[name])

    def rpoplpush(self, source: str, destination: str) -> str | None:
        source_list = self.lists.setdefault(source, [])
        if not source_list:
            return None
        value = source_list.pop()
        self.lists.setdefault(destination, []).insert(0, value)
        return value

    def lrem(self, name: str, count: int, value: str) -> int:
        items = self.lists.setdefault(name, [])
        removed = 0
        remaining: list[str] = []
        for item in items:
            if item == value and (count == 0 or removed < count):
                removed += 1
                continue
            remaining.append(item)
        self.lists[name] = remaining
        return removed

    def lrange(self, name: str, start: int, end: int) -> list[str]:
        items = self.lists.get(name, [])
        if end == -1:
            end = len(items) - 1
        return deepcopy(items[start : end + 1])

    def llen(self, name: str) -> int:
        return len(self.lists.get(name, []))


def list_values(redis: FakeRedis, name: str) -> list[str]:
    return list(redis.lists.get(name, []))


def hash_keys(redis: FakeRedis, name: str) -> Iterable[str]:
    return redis.hashes.get(name, {}).keys()
