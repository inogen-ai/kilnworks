import random
import time
from collections.abc import Callable
from typing import Any

from kilnworks.core.errors import ProviderError, TransientProviderError


def retry_with_backoff(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 4.0,
    jitter: float = 0.25,
    sleep: Callable[[float], None] | None = None,
    rand: Callable[[], float] | None = None,
) -> Any:
    """Call fn, retrying TransientProviderError with exponential backoff + jitter.

    After the final attempt the error is re-raised as a plain ProviderError so
    callers fail loudly with the provider named (spec section 10).
    """
    do_sleep = sleep if sleep is not None else time.sleep
    do_rand = rand if rand is not None else random.random
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except TransientProviderError as exc:
            if attempt == attempts:
                raise ProviderError(
                    exc.provider, f"gave up after {attempts} attempts: {exc}"
                ) from exc
            do_sleep(delay + jitter * do_rand())
            delay = min(delay * 2, max_delay)
