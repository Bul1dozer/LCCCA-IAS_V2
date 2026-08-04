"""Rate limiting backends for authentication-sensitive endpoints."""
from __future__ import annotations

import os
import time
from collections import defaultdict


class RateLimitExceeded(Exception):
    pass


class MemoryRateLimiter:
    def __init__(self):
        self._attempts = defaultdict(list)

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        attempts = [ts for ts in self._attempts[key] if now - ts < window_seconds]
        if len(attempts) >= limit:
            self._attempts[key] = attempts
            raise RateLimitExceeded
        attempts.append(now)
        self._attempts[key] = attempts

    def clear(self, key: str) -> None:
        self._attempts.pop(key, None)

    def clear_all(self) -> None:
        self._attempts.clear()


class RedisRateLimiter:
    def __init__(self, url: str):
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._client.ping()

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        redis_key = f"lcca:rate:{key}"
        count = self._client.incr(redis_key)
        if count == 1:
            self._client.expire(redis_key, window_seconds)
        if count > limit:
            raise RateLimitExceeded

    def clear(self, key: str) -> None:
        self._client.delete(f"lcca:rate:{key}")

    def clear_all(self) -> None:
        raise NotImplementedError("Redis rate limiter does not support clear_all().")


def build_rate_limiter(is_production: bool):
    backend = os.environ.get("LCCA_RATE_LIMIT_BACKEND", "memory").strip().lower()
    if backend == "memory":
        if is_production:
            raise RuntimeError("LCCA_RATE_LIMIT_BACKEND=redis is required in production.")
        return MemoryRateLimiter()
    if backend == "redis":
        url = os.environ.get("LCCA_RATE_LIMIT_REDIS_URL")
        if not url:
            raise RuntimeError("LCCA_RATE_LIMIT_REDIS_URL is required for Redis rate limiting.")
        return RedisRateLimiter(url)
    raise RuntimeError(f"Unsupported LCCA_RATE_LIMIT_BACKEND={backend!r}.")
