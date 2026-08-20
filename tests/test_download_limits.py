from __future__ import annotations

import asyncio

import pytest

from hudoc_py.utils.downloads import ResponseTooLargeError, read_limited, read_text_limited


class _Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class _Response:
    def __init__(self, chunks, *, declared=None):
        self.content = _Stream(chunks)
        self.headers = {} if declared is None else {"Content-Length": str(declared)}

    def get_encoding(self):
        return "utf-8"


def test_bounded_reader_streams_under_limit():
    response = _Response([b"ab", b"cd"])
    assert asyncio.run(read_limited(response, max_bytes=4)) == b"abcd"


def test_bounded_reader_rejects_declared_or_streamed_oversize():
    with pytest.raises(ResponseTooLargeError, match="declared"):
        asyncio.run(read_limited(_Response([], declared=5), max_bytes=4))
    with pytest.raises(ResponseTooLargeError, match="exceeds"):
        asyncio.run(read_limited(_Response([b"abc", b"de"]), max_bytes=4))


def test_bounded_text_reader_uses_response_encoding():
    assert asyncio.run(read_text_limited(_Response(["café".encode()]), max_bytes=8)) == "café"
