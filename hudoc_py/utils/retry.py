"""Shared retry-with-backoff helper.

The 429/RESOURCE_EXHAUSTED exponential-backoff loop appeared in three sibling
research repos; this is the single shared implementation. Note that
:mod:`hudoc_py.llm.client` keeps its own inline loop on purpose – it returns
an error result instead of raising – while new code should use
:func:`with_backoff`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "rate limit", "rate-limit", "ratelimit")


def is_rate_limit_error(exc: BaseException) -> bool:
    """Heuristic: does this exception look like a rate-limit / quota error?"""
    msg = str(exc).lower()
    return any(marker.lower() in msg for marker in _RATE_LIMIT_MARKERS)


def with_backoff(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    initial_backoff: float = 2.0,
    retryable: Callable[[BaseException], bool] = is_rate_limit_error,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
) -> T:
    """Call ``fn``, retrying with exponential backoff on retryable errors.

    Waits ``initial_backoff * 2**attempt`` between attempts. Non-retryable
    exceptions propagate immediately; after ``max_retries`` retries the last
    exception is re-raised. ``sleep`` is injectable so tests run instantly.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except BaseException as exc:
            if attempt >= max_retries or not retryable(exc):
                raise
            wait = initial_backoff * (2**attempt)
            if on_retry is not None:
                on_retry(attempt + 1, wait, exc)
            else:
                logger.warning(
                    "Retryable error (attempt %d/%d): %s – waiting %.1fs",
                    attempt + 1, max_retries + 1, exc, wait,
                )
            sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover
