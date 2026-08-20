"""Sync facade over the async core.

Calls :func:`asyncio.run` to drive the coroutines in
:mod:`hudoc_py._aio`. If you're already inside an event loop (e.g. Jupyter,
FastAPI handler), use ``hudoc_py.aio.*`` directly instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from . import _aio, config
from .main.dsl import Q
from .models import Case, CaseCollection


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "hudoc_py sync functions cannot be called from inside a running event loop. "
        "Use `hudoc_py.aio.*` (async) variants instead."
    )


def search(
    *,
    page_size: int = 100,
    limit: int | None = None,
    query: str | Q | None = None,
    sort: str = "relevance",
    complete: bool = True,
    **filters: Any,
) -> CaseCollection:
    return _run(
        _aio.search(
            page_size=page_size,
            limit=limit,
            query=query,
            sort=sort,
            complete=complete,
            **filters,
        )
    )


def count(*, query: str | Q | None = None, **filters: Any) -> int:
    """Return the number of HUDOC matches without fetching any rows."""
    return _run(_aio.count(query=query, **filters))


def smart_fetch(
    *,
    query: str | Q | None = None,
    top: int = 10,
    page_size: int = 100,
    sort: str = "relevance",
    with_text: bool = True,
    text_format: str = "text",
    segment: bool = True,
    rich_sections: bool = False,
    french_fallback: bool = True,
    concurrency: int = config.HUDOC_CONCURRENCY,
    **filters: Any,
) -> CaseCollection:
    """Search, keep the ``top`` matches, and fetch their texts concurrently."""
    return _run(
        _aio.smart_fetch(
            query=query,
            top=top,
            page_size=page_size,
            sort=sort,
            with_text=with_text,
            text_format=text_format,
            segment=segment,
            rich_sections=rich_sections,
            french_fallback=french_fallback,
            concurrency=concurrency,
            **filters,
        )
    )


def fetch_case(
    *,
    appno: str | None = None,
    itemid: str | None = None,
    language: str | None = None,
    with_text: bool = False,
    text_format: str = "text",
    segment: bool = True,
    rich_sections: bool = False,
    french_fallback: bool = True,
    rescue: bool = False,
    docx_out: str | Path | None = None,
) -> Case | None:
    return _run(
        _aio.fetch_case(
            appno=appno,
            itemid=itemid,
            language=language,
            with_text=with_text,
            text_format=text_format,
            segment=segment,
            rich_sections=rich_sections,
            french_fallback=french_fallback,
            rescue=rescue,
            docx_out=docx_out,
        )
    )


def fetch_text(itemid: str, *, format: str = "text") -> str | None:
    return _run(_aio.fetch_text(itemid, format=format))


def fetch_docx(itemid: str, *, out: str | Path | None = None) -> bytes | None:
    """Fetch a document's raw DOCX bytes; optionally write them to ``out``."""
    return _run(_aio.fetch_docx(itemid, out=out))


def list_versions(*, appno: str | None = None, ecli: str | None = None, limit: int = 500):
    return _run(_aio.list_versions(appno=appno, ecli=ecli, limit=limit))


def download_versions(output_dir: str | Path, **kwargs):
    return _run(_aio.download_versions(output_dir, **kwargs))
