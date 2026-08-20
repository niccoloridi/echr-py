"""Tests for the shared retry/backoff helper."""

from __future__ import annotations

import pytest

from hudoc_py.utils import is_rate_limit_error, with_backoff


def test_is_rate_limit_error_cases():
    assert is_rate_limit_error(RuntimeError("429 Too Many Requests"))
    assert is_rate_limit_error(RuntimeError("RESOURCE_EXHAUSTED: quota"))
    assert is_rate_limit_error(RuntimeError("Rate limit exceeded"))
    assert not is_rate_limit_error(RuntimeError("connection refused"))


def test_with_backoff_success_after_retries_records_waits():
    waits: list[float] = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("429")
        return "done"

    result = with_backoff(flaky, max_retries=3, initial_backoff=2.0, sleep=waits.append)
    assert result == "done"
    assert waits == [2.0, 4.0, 8.0]


def test_with_backoff_non_retryable_raises_immediately():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        with_backoff(boom, sleep=lambda _: None)
    assert calls["n"] == 1


def test_with_backoff_exhaustion_reraises():
    def always_429():
        raise RuntimeError("429")

    with pytest.raises(RuntimeError):
        with_backoff(always_429, max_retries=2, sleep=lambda _: None)


def test_with_backoff_custom_retryable():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("transient")
        return 42

    result = with_backoff(
        flaky, retryable=lambda exc: isinstance(exc, ValueError), sleep=lambda _: None
    )
    assert result == 42
