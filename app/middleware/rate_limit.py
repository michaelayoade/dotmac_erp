"""Rate limiting middleware for protecting sensitive endpoints.

Provides IP-based rate limiting with configurable limits per endpoint.
Uses in-memory storage for development/single instance, with Redis
backend option for production clusters.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock

from fastapi import HTTPException, Request, status
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.net import get_client_ip

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for a rate-limited endpoint."""

    requests: int  # Max requests allowed
    window_seconds: int  # Time window in seconds
    key_func: Callable[[Request], str] | None = None  # Custom key function


@dataclass
class RateLimitEntry:
    """Tracks request timestamps for a single key."""

    timestamps: list[float] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)


class InMemoryRateLimiter:
    """In-memory rate limiter using sliding window algorithm.

    Suitable for development and single-instance deployments.
    For production clusters, use RedisRateLimiter instead.
    """

    def __init__(self):
        self._entries: dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)
        self._cleanup_interval = 60  # Cleanup old entries every 60 seconds
        self._last_cleanup = time.time()
        self._global_lock = Lock()

    def _cleanup_old_entries(self) -> None:
        """Remove stale entries to prevent memory growth."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        with self._global_lock:
            if now - self._last_cleanup < self._cleanup_interval:
                return  # Double-check after acquiring lock

            keys_to_remove = []
            for key, entry in self._entries.items():
                with entry.lock:
                    # Keep entries with recent activity (within last hour)
                    if not entry.timestamps or now - entry.timestamps[-1] > 3600:
                        keys_to_remove.append(key)

            for key in keys_to_remove:
                del self._entries[key]

            self._last_cleanup = now

    def is_rate_limited(
        self, key: str, max_requests: int, window_seconds: int
    ) -> tuple[bool, int, int]:
        """Check if a key is rate limited.

        Args:
            key: Unique identifier (e.g., IP:path)
            max_requests: Maximum requests allowed in window
            window_seconds: Time window in seconds

        Returns:
            Tuple of (is_limited, remaining_requests, retry_after_seconds)
        """
        self._cleanup_old_entries()

        now = time.time()
        window_start = now - window_seconds
        entry = self._entries[key]

        with entry.lock:
            # Remove timestamps outside the window
            entry.timestamps = [ts for ts in entry.timestamps if ts > window_start]

            current_count = len(entry.timestamps)

            if current_count >= max_requests:
                # Calculate when the oldest request in window will expire
                if entry.timestamps:
                    retry_after = int(entry.timestamps[0] - window_start) + 1
                else:
                    retry_after = window_seconds
                return True, 0, retry_after

            # Record this request
            entry.timestamps.append(now)
            remaining = max_requests - len(entry.timestamps)
            return False, remaining, 0

    def reset(self, key: str) -> None:
        """Reset rate limit for a key (useful for testing)."""
        if key in self._entries:
            with self._entries[key].lock:
                self._entries[key].timestamps.clear()


class RedisRateLimiter:
    """Redis-backed rate limiter for production clusters.

    Uses sorted sets for sliding window implementation, ensuring
    consistent rate limiting across multiple application instances.
    """

    def __init__(self, redis_url: str | None = None):
        self._redis_url = redis_url or os.getenv("REDIS_URL")
        self._client = None
        self._available = False
        self._init_client()

    def _init_client(self) -> None:
        """Initialize Redis client if URL is configured."""
        if not self._redis_url:
            logger.debug("Redis URL not configured, rate limiter unavailable")
            return

        try:
            import redis

            self._client = redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            # Test connection
            if self._client is not None:
                self._client.ping()
            self._available = True
            logger.info("Redis rate limiter initialized successfully")
        except ImportError:
            logger.warning("redis package not installed, falling back to in-memory")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}, falling back to in-memory")
            self._client = None

    @property
    def is_available(self) -> bool:
        """Check if Redis is available."""
        return self._available and self._client is not None

    def is_rate_limited(
        self, key: str, max_requests: int, window_seconds: int
    ) -> tuple[bool, int, int]:
        """Check if a key is rate limited using Redis sorted sets."""
        if not self.is_available:
            raise RuntimeError("Redis not available")

        now = time.time()
        window_start = now - window_seconds
        redis_key = f"ratelimit:{key}"

        client = self._client
        if client is None:
            raise RuntimeError("Redis not available")
        pipe = client.pipeline()
        try:
            # Remove old entries
            pipe.zremrangebyscore(redis_key, 0, window_start)
            # Count current entries
            pipe.zcard(redis_key)
            # Add new entry with current timestamp as score
            pipe.zadd(redis_key, {str(now): now})
            # Set expiry on the key
            pipe.expire(redis_key, window_seconds + 1)
            # Get oldest entry timestamp
            pipe.zrange(redis_key, 0, 0, withscores=True)

            results = pipe.execute()
            current_count = results[1]

            if current_count >= max_requests:
                oldest = results[4]
                if oldest:
                    retry_after = int(oldest[0][1] - window_start) + 1
                else:
                    retry_after = window_seconds
                # Remove the entry we just added since we're rejecting
                client.zrem(redis_key, str(now))
                return True, 0, retry_after

            remaining = max_requests - current_count - 1
            return False, max(0, remaining), 0

        except Exception as e:
            logger.error(f"Redis rate limit check failed: {e}")
            raise

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        if self.is_available and self._client is not None:
            self._client.delete(f"ratelimit:{key}")


class RateLimiter:
    """Composite rate limiter that uses Redis if available, else in-memory."""

    def __init__(self, redis_url: str | None = None):
        self._redis = RedisRateLimiter(redis_url)
        self._memory = InMemoryRateLimiter()

    def is_rate_limited(
        self, key: str, max_requests: int, window_seconds: int
    ) -> tuple[bool, int, int]:
        """Check if rate limited, using Redis if available."""
        if self._redis.is_available:
            try:
                return self._redis.is_rate_limited(key, max_requests, window_seconds)
            except Exception:
                # Fall back to in-memory on Redis failure
                pass
        return self._memory.is_rate_limited(key, max_requests, window_seconds)

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        if self._redis.is_available:
            self._redis.reset(key)
        self._memory.reset(key)


# Default rate limiter instance
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


# Default rate limit configurations for sensitive endpoints
DEFAULT_RATE_LIMITS: dict[str, RateLimitConfig] = {
    # Login endpoint - 5 requests per minute per IP (brute force protection)
    "/auth/login": RateLimitConfig(requests=5, window_seconds=60),
    "/api/v1/auth/login": RateLimitConfig(requests=5, window_seconds=60),
    # Password reset - 3 requests per minute (email enumeration protection)
    "/auth/forgot-password": RateLimitConfig(requests=3, window_seconds=60),
    "/api/v1/auth/forgot-password": RateLimitConfig(requests=3, window_seconds=60),
    # MFA verification - 5 attempts per minute
    "/auth/mfa/verify": RateLimitConfig(requests=5, window_seconds=60),
    "/api/v1/auth/mfa/verify": RateLimitConfig(requests=5, window_seconds=60),
    # Token refresh - 30 per minute (higher limit for legitimate use)
    "/auth/refresh": RateLimitConfig(requests=30, window_seconds=60),
    "/api/v1/auth/refresh": RateLimitConfig(requests=30, window_seconds=60),
}

# Note: Careers portal rate limits are handled in-route via check_rate_limit()
# to allow dynamic org_slug path parameters. The following limits apply:
# - Job application: 3 per 5 minutes
# - Resume upload: 5 per minute
# - Status check request: 3 per minute

# Note: Onboarding portal routes (/onboarding/start/{token}/...) use token-based
# auth which inherently limits abuse (tokens are unique per employee and expire).
# The task completion endpoint is the primary action; each task can only be
# completed once per token, providing natural rate limiting.


def _make_rate_limit_key(request: Request, config: RateLimitConfig) -> str:
    """Generate a rate limit key for the request."""
    if config.key_func:
        return config.key_func(request)
    return f"{get_client_ip(request)}:{request.url.path}"


async def rate_limit_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Middleware that enforces rate limits on configured endpoints."""
    path = request.url.path

    # Check if this path has rate limiting configured
    config = DEFAULT_RATE_LIMITS.get(path)
    if not config:
        return await call_next(request)

    # Only rate limit POST requests (login attempts, etc.)
    if request.method != "POST":
        return await call_next(request)

    limiter = get_rate_limiter()
    key = _make_rate_limit_key(request, config)

    is_limited, remaining, retry_after = limiter.is_rate_limited(
        key, config.requests, config.window_seconds
    )

    if is_limited:
        logger.warning(
            f"Rate limit exceeded for {key} on {path}. "
            f"Retry after {retry_after} seconds."
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "code": "rate_limit_exceeded",
                "message": "Too many requests. Please try again later.",
                "retry_after": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(config.requests),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(time.time()) + retry_after),
            },
        )

    # Process request and add rate limit headers to response
    response = await call_next(request)

    # Add rate limit headers to successful responses
    response.headers["X-RateLimit-Limit"] = str(config.requests)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(
        int(time.time()) + config.window_seconds
    )

    return response


def check_rate_limit(
    request: Request,
    max_requests: int = 5,
    window_seconds: int = 60,
    key_suffix: str = "",
) -> None:
    """Manually check rate limit and raise HTTPException if exceeded.

    Use this for fine-grained rate limiting within route handlers.

    Args:
        request: The FastAPI request
        max_requests: Maximum requests allowed
        window_seconds: Time window in seconds
        key_suffix: Optional suffix to append to the rate limit key

    Raises:
        HTTPException: With 429 status if rate limited
    """
    limiter = get_rate_limiter()
    base_key = f"{get_client_ip(request)}:{request.url.path}"
    key = f"{base_key}:{key_suffix}" if key_suffix else base_key

    is_limited, remaining, retry_after = limiter.is_rate_limited(
        key, max_requests, window_seconds
    )

    if is_limited:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limit_exceeded",
                "message": "Too many requests. Please try again later.",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
