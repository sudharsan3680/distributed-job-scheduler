"""
Pure functions for retry-delay computation. Kept side-effect free and unit
tested directly (see tests/test_retry.py) since getting backoff math wrong
either hammers downstream systems (too fast) or stalls the queue (too slow).
"""
import random

from app.models import RetryPolicy, RetryStrategy


def compute_backoff_seconds(policy: RetryPolicy, attempt_number: int) -> float:
    """
    attempt_number is 1-indexed: this is the delay to apply *before* the
    given attempt runs, based on how many attempts have already failed.
    """
    if policy.strategy == RetryStrategy.FIXED:
        delay = policy.base_delay_seconds
    elif policy.strategy == RetryStrategy.LINEAR:
        delay = policy.base_delay_seconds * attempt_number
    elif policy.strategy == RetryStrategy.EXPONENTIAL:
        delay = policy.base_delay_seconds * (policy.multiplier ** (attempt_number - 1))
    else:
        raise ValueError(f"Unknown retry strategy: {policy.strategy}")

    delay = min(delay, policy.max_delay_seconds)

    if policy.jitter:
        # Full jitter (AWS architecture-blog recommended pattern): uniform
        # random in [0, delay]. Prevents thundering-herd retries when many
        # jobs fail at once (e.g. a downstream outage).
        delay = random.uniform(0, delay)

    return max(delay, 0.0)
