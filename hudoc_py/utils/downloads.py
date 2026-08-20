"""Bounded HTTP response readers shared by document downloaders."""

from __future__ import annotations

from typing import Any


class ResponseTooLargeError(ValueError):
    """Raised when a remote response exceeds its configured byte limit."""


def _declared_length(response: Any) -> int | None:
    value = getattr(response, "headers", {}).get("Content-Length")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def read_limited(response: Any, *, max_bytes: int) -> bytes:
    """Read an aiohttp-like response without allowing unbounded buffering."""
    declared = _declared_length(response)
    if declared is not None and declared > max_bytes:
        raise ResponseTooLargeError(
            f"declared response size {declared} exceeds {max_bytes} bytes"
        )

    stream = getattr(response, "content", None)
    iterator = getattr(stream, "iter_chunked", None)
    if callable(iterator):
        chunks: list[bytes] = []
        total = 0
        async for chunk in iterator(min(1024 * 1024, max_bytes + 1)):
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLargeError(f"response exceeds {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    payload = await response.read()
    if len(payload) > max_bytes:
        raise ResponseTooLargeError(f"response exceeds {max_bytes} bytes")
    return payload


async def read_text_limited(response: Any, *, max_bytes: int) -> str:
    """Read bounded response bytes and decode with the declared encoding."""
    payload = await read_limited(response, max_bytes=max_bytes)
    get_encoding = getattr(response, "get_encoding", None)
    encoding = get_encoding() if callable(get_encoding) else "utf-8"
    return payload.decode(encoding or "utf-8", errors="replace")
