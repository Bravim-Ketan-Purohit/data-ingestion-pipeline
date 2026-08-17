"""Tests for the rate limiter."""

import asyncio
import time

import pytest

from pipeline.limits.rate_limiter import (
    CostCeilingReached,
    RateLimiter,
    TokenBucket,
)


class TestTokenBucket:
    """Test the token bucket implementation."""

    def test_initial_capacity(self):
        bucket = TokenBucket(capacity=10.0, refill_rate=1.0)
        assert bucket.try_acquire(10.0)
        assert not bucket.try_acquire(1.0)

    def test_refill(self):
        bucket = TokenBucket(capacity=10.0, refill_rate=10.0)  # 10/sec
        bucket.try_acquire(10.0)  # Empty it
        time.sleep(0.5)  # Wait for ~5 tokens
        assert bucket.try_acquire(4.0)  # Should have ~5

    def test_time_until_available(self):
        bucket = TokenBucket(capacity=10.0, refill_rate=1.0)  # 1/sec
        bucket.try_acquire(10.0)  # Empty
        wait = bucket.time_until_available(5.0)
        assert 4.0 < wait < 6.0  # Should be about 5 seconds


class TestRateLimiter:
    """Test the centralized rate limiter."""

    @pytest.mark.asyncio
    async def test_acquire_and_release(self):
        limiter = RateLimiter()
        await limiter.acquire(estimated_tokens=100)
        limiter.release()
        assert limiter.stats["requests_made"] == 1

    @pytest.mark.asyncio
    async def test_cost_ceiling(self):
        limiter = RateLimiter()
        limiter._cost_ceiling = 1.0
        limiter.record_cost(1.5)
        with pytest.raises(CostCeilingReached):
            await limiter.acquire()

    def test_stats(self):
        limiter = RateLimiter()
        assert limiter.stats["requests_made"] == 0
        assert limiter.stats["total_cost_usd"] == 0.0
