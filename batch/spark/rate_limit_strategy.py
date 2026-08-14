"""Executor-side rate limiting for Claude API calls from Spark.

THE PROBLEM: Naive mapPartitions with N executors blows through the Claude API's
global rate limit. Each executor has no knowledge of what others are doing.

CHOSEN STRATEGY: Per-executor token bucket sized to global_limit / num_executors.

This is a deliberate, solved problem. The alternative (a two-stage design where
Spark prepares batches and a separate async worker makes calls) is documented
in docs/BATCH.md with the trade-offs.

See docs/BATCH.md for the full write-up.
"""

import time
import threading
from dataclasses import dataclass, field


@dataclass
class ExecutorRateLimiter:
    """Per-executor token bucket for rate limiting Claude API calls.

    Strategy: divide the global rate limit evenly across executors.
    Each executor gets global_limit / num_executors tokens per minute.

    Trade-offs (documented in docs/BATCH.md):
    + Simple to reason about
    + No coordination between executors needed
    + Graceful degradation: fewer executors = more headroom each
    - Under-utilizes quota if some executors are idle
    - Requires knowing num_executors at job start
    - Doesn't adapt to partition skew (a 400-page PDF takes longer)
    """

    global_rpm_limit: int
    global_tpm_limit: int
    num_executors: int
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _request_tokens: float = field(init=False)
    _token_tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        # Per-executor allocation
        self._rpm_per_executor = self.global_rpm_limit / self.num_executors
        self._tpm_per_executor = self.global_tpm_limit / self.num_executors

        self._request_tokens = self._rpm_per_executor
        self._token_tokens = self._tpm_per_executor
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        """Refill token buckets based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now

        # Refill per second
        self._request_tokens = min(
            self._rpm_per_executor,
            self._request_tokens + (self._rpm_per_executor / 60.0) * elapsed,
        )
        self._token_tokens = min(
            self._tpm_per_executor,
            self._token_tokens + (self._tpm_per_executor / 60.0) * elapsed,
        )

    def acquire(self, estimated_tokens: int = 1000) -> float:
        """Acquire permission for one API call. Returns wait time in seconds.

        Blocks until both request and token budgets are available.
        Returns the time spent waiting (for metrics).
        """
        total_wait = 0.0

        while True:
            with self._lock:
                self._refill()

                if self._request_tokens >= 1.0 and self._token_tokens >= estimated_tokens:
                    self._request_tokens -= 1.0
                    self._token_tokens -= estimated_tokens
                    return total_wait

                # Calculate wait time
                request_wait = 0.0
                if self._request_tokens < 1.0:
                    request_wait = (1.0 - self._request_tokens) / (self._rpm_per_executor / 60.0)

                token_wait = 0.0
                if self._token_tokens < estimated_tokens:
                    deficit = estimated_tokens - self._token_tokens
                    token_wait = deficit / (self._tpm_per_executor / 60.0)

                wait = max(request_wait, token_wait, 0.01)

            time.sleep(wait)
            total_wait += wait

    def record_actual_tokens(self, actual_tokens: int, estimated_tokens: int) -> None:
        """Adjust token bucket after learning actual token usage."""
        with self._lock:
            diff = estimated_tokens - actual_tokens
            if diff > 0:
                # We overestimated — return tokens
                self._token_tokens = min(
                    self._tpm_per_executor,
                    self._token_tokens + diff,
                )


def create_partition_extractor(
    global_rpm: int,
    global_tpm: int,
    num_executors: int,
    schema: dict,
    model: str = "claude-sonnet-4-20250514",
):
    """Create a mapPartitions function with per-executor rate limiting.

    This is the function passed to rdd.mapPartitions(). Each partition
    gets its own rate limiter sized to global_limit / num_executors.
    """

    def extract_partition(partition_iter):
        """Process a partition of documents with rate limiting."""
        import anthropic
        import json
        import hashlib
        import os

        # Each executor gets its fair share of the rate limit
        limiter = ExecutorRateLimiter(
            global_rpm_limit=global_rpm,
            global_tpm_limit=global_tpm,
            num_executors=num_executors,
        )

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

        for doc in partition_iter:
            # Estimate tokens (rough: 4 chars per token)
            content = doc.get("content", "")
            estimated_tokens = len(content) // 4 + 500

            # Wait for rate limit budget
            wait_time = limiter.acquire(estimated_tokens)

            try:
                # Make the extraction call
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    tools=[{
                        "name": "extract_fields",
                        "description": "Extract structured fields",
                        "input_schema": schema,
                    }],
                    tool_choice={"type": "tool", "name": "extract_fields"},
                    messages=[{
                        "role": "user",
                        "content": f"Extract fields from:\n{content}",
                    }],
                )

                # Record actual token usage
                actual_tokens = response.usage.input_tokens + response.usage.output_tokens
                limiter.record_actual_tokens(actual_tokens, estimated_tokens)

                # Parse result
                fields = []
                for block in response.content:
                    if block.type == "tool_use":
                        for field_name, field_data in block.input.items():
                            fields.append({
                                "path": f"/{field_name}",
                                "value": field_data.get("value") if isinstance(field_data, dict) else field_data,
                                "confidence": field_data.get("confidence", 0.5) if isinstance(field_data, dict) else 0.5,
                            })

                yield {
                    "document_id": doc["document_id"],
                    "content_hash": doc["content_hash"],
                    "fields": fields,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "wait_time": wait_time,
                    "status": "success",
                }

            except Exception as e:
                yield {
                    "document_id": doc["document_id"],
                    "content_hash": doc["content_hash"],
                    "fields": [],
                    "error": str(e),
                    "wait_time": wait_time,
                    "status": "failed",
                }

    return extract_partition
