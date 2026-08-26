from app.models import RetryPolicy, RetryStrategy
from app.services.retry import compute_backoff_seconds


def _policy(**kw):
    defaults = dict(name="p", strategy=RetryStrategy.FIXED, base_delay_seconds=5, max_delay_seconds=3600, multiplier=2, max_attempts=5, jitter=False)
    defaults.update(kw)
    return RetryPolicy(**defaults)


def test_fixed_backoff_is_constant():
    p = _policy(strategy=RetryStrategy.FIXED, base_delay_seconds=10)
    assert compute_backoff_seconds(p, 1) == 10
    assert compute_backoff_seconds(p, 5) == 10


def test_linear_backoff_scales_with_attempt():
    p = _policy(strategy=RetryStrategy.LINEAR, base_delay_seconds=10)
    assert compute_backoff_seconds(p, 1) == 10
    assert compute_backoff_seconds(p, 3) == 30


def test_exponential_backoff_doubles():
    p = _policy(strategy=RetryStrategy.EXPONENTIAL, base_delay_seconds=2, multiplier=2)
    assert compute_backoff_seconds(p, 1) == 2
    assert compute_backoff_seconds(p, 2) == 4
    assert compute_backoff_seconds(p, 3) == 8


def test_exponential_backoff_caps_at_max_delay():
    p = _policy(strategy=RetryStrategy.EXPONENTIAL, base_delay_seconds=100, multiplier=10, max_delay_seconds=500)
    assert compute_backoff_seconds(p, 5) == 500  # would be 100 * 10^4 uncapped


def test_jitter_stays_within_bounds():
    p = _policy(strategy=RetryStrategy.FIXED, base_delay_seconds=100, jitter=True)
    for _ in range(50):
        delay = compute_backoff_seconds(p, 1)
        assert 0 <= delay <= 100
