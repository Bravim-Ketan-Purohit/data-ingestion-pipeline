"""Token-bucket rate limiter for Claude API calls.

This is a SINGLE component that every Claude call goes through — no direct SDK calls
scattered across modules, or the limits become unenforceable.

Implements:
- Token bucket over requests/min AND tokens/min
- Honours retry-after headers
- Exponential backoff with jitter on 429 and 529
- Global concurrency cap
- Per-run cost ceiling
- Logs limiter decisions so the dashboard can show throttling as it happens
"""

import asyncio
import random
import time
from dataclasses import dataclass, field

from pipeline.config import settings
from pipeline.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TokenBucket:
    """A token bucket for rate limiting."""

    capacity: float
    refill_rate: float  # tokens per second
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Try to acquire tokens. Returns True if successful."""
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def time_until_available(self, tokens: float = 1.0) -> float:
        """Time in seconds until the requested tokens will be available."""
        self._refill()
        if self.tokens >= tokens:
            return 0.0
        deficit = tokens - self.tokens
        return deficit / self.refill_rate


class RateLimiter:
    """Central rate limiter for all Claude API calls.

    Every extraction call goes through this. No exceptions.
    """

    def __init__(self) -> None:
        # Request-per-minute bucket
        self._rpm_bucket = TokenBucket(
            capacity=float(settings.claude_rpm_limit),
            refill_rate=settings.claude_rpm_limit / 60.0,
        )
        # Tokens-per-minute bucket
        self._tpm_bucket = TokenBucket(
            capacity=float(settings.claude_tpm_limit),
            refill_rate=settings.claude_tpm_limit / 60.0,
        )
        # Concurrency semaphore
        self._semaphore = asyncio.Semaphore(settings.claude_concurrency_limit)
        # Cost tracking
        self._total_cost_usd: float = 0.0
        self._cost_ceiling = settings.claude_cost_ceiling_usd
        # Retry-after tracking
        self._retry_after_until: float = 0.0
        # Stats
        self._requests_made: int = 0
        self._requests_throttled: int = 0
        self._total_wait_seconds: float = 0.0

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost_usd

    @property
    def stats(self) -> dict:
        return {
            "requests_made": self._requests_made,
            "requests_throttled": self._requests_throttled,
            "total_wait_seconds": round(self._total_wait_seconds, 2),
            "total_cost_usd": round(self._total_cost_usd, 4),
            "cost_ceiling_usd": self._cost_ceiling,
        }

    def record_cost(self, cost_usd: float) -> None:
        """Record the cost of an API call."""
        self._total_cost_usd += cost_usd

    def check_cost_ceiling(self) -> None:
        """Raise if the cost ceiling has been reached."""
        if self._total_cost_usd >= self._cost_ceiling:
            raise CostCeilingReached(
                f"Cost ceiling reached: ${self._total_cost_usd:.4f} >= ${self._cost_ceiling:.4f}"
            )

    def set_retry_after(self, seconds: float) -> None:
        """Set a global retry-after from a 429/529 response."""
        self._retry_after_until = time.monotonic() + seconds
        logger.warning(
            "rate_limit_retry_after",
            retry_after_seconds=seconds,
        )

    async def acquire(self, estimated_tokens: int = 1000) -> None:
        """Acquire permission to make an API call. Blocks until available.

        This is the single gateway for all Claude calls.
        """
        self.check_cost_ceiling()

        # Respect retry-after
        now = time.monotonic()
        if now < self._retry_after_until:
            wait = self._retry_after_until - now
            logger.info("rate_limit_waiting_retry_after", wait_seconds=round(wait, 2))
            self._total_wait_seconds += wait
            self._requests_throttled += 1
            await asyncio.sleep(wait)

        # Wait for RPM bucket
        while not self._rpm_bucket.try_acquire(1.0):
            wait = self._rpm_bucket.time_until_available(1.0)
            logger.info("rate_limit_waiting_rpm", wait_seconds=round(wait, 2))
            self._total_wait_seconds += wait
            self._requests_throttled += 1
            await asyncio.sleep(wait)

        # Wait for TPM bucket
        while not self._tpm_bucket.try_acquire(float(estimated_tokens)):
            wait = self._tpm_bucket.time_until_available(float(estimated_tokens))
            logger.info("rate_limit_waiting_tpm", wait_seconds=round(wait, 2))
            self._total_wait_seconds += wait
            self._requests_throttled += 1
            await asyncio.sleep(wait)

        # Acquire concurrency slot
        await self._semaphore.acquire()
        self._requests_made += 1

    def release(self) -> None:
        """Release the concurrency slot after a call completes."""
        self._semaphore.release()

    async def execute_with_backoff(self, coro_factory, max_retries: int = 5):
        """Execute a coroutine with exponential backoff on 429/529.

        coro_factory is a callable that returns a new coroutine each time,
        since a coroutine can only be awaited once.
        """
        for attempt in range(max_retries + 1):
            try:
                await self.acquire()
                try:
                    result = await coro_factory()
                    return result
                finally:
                    self.release()
            except RateLimitError as e:
                if attempt == max_retries:
                    raise
                # Exponential backoff with jitter
                base_delay = min(2**attempt, 60)
                jitter = random.uniform(0, base_delay * 0.5)
                delay = base_delay + jitter

                if e.retry_after:
                    self.set_retry_after(e.retry_after)
                    delay = max(delay, e.retry_after)

                logger.warning(
                    "rate_limit_backoff",
                    attempt=attempt + 1,
                    delay_seconds=round(delay, 2),
                    status_code=e.status_code,
                )
                self._requests_throttled += 1
                self._total_wait_seconds += delay
                await asyncio.sleep(delay)

        raise RuntimeError("Unreachable")


class RateLimitError(Exception):
    """Raised when the API returns 429 or 529."""

    def __init__(self, status_code: int, retry_after: float | None = None, message: str = ""):
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message or f"Rate limited: HTTP {status_code}")


class CostCeilingReached(Exception):
    """Raised when the per-run cost ceiling is exceeded."""

    pass


# Singleton instance — all modules use this
rate_limiter = RateLimiter()
