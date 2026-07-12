import pytest

from kilnworks.core.errors import ProviderError, TransientProviderError
from kilnworks.core.retry import retry_with_backoff


def test_returns_result_after_transient_failures():
    sleeps = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientProviderError("openai", "rate limited")
        return "ok"

    result = retry_with_backoff(flaky, sleep=sleeps.append, rand=lambda: 0.0)
    assert result == "ok"
    assert sleeps == [0.5, 1.0]


def test_exhaustion_raises_plain_provider_error_naming_provider():
    def always_fails():
        raise TransientProviderError("openai", "timeout")

    with pytest.raises(ProviderError) as excinfo:
        retry_with_backoff(always_fails, sleep=lambda s: None, rand=lambda: 0.0)
    assert type(excinfo.value) is ProviderError
    assert excinfo.value.provider == "openai"
    assert "3 attempts" in str(excinfo.value)


def test_delay_caps_at_max_and_jitter_is_added():
    sleeps = []

    def always_fails():
        raise TransientProviderError("openai", "boom")

    with pytest.raises(ProviderError):
        retry_with_backoff(
            always_fails, attempts=5, max_delay=1.0, jitter=0.25,
            sleep=sleeps.append, rand=lambda: 1.0,
        )
    assert sleeps == [0.75, 1.25, 1.25, 1.25]


def test_non_transient_errors_propagate_immediately():
    def bad():
        raise ValueError("not a provider problem")

    with pytest.raises(ValueError):
        retry_with_backoff(bad, sleep=lambda s: None)
