"""Sync facade over :mod:`hudoc_py.execution.aio`."""

from __future__ import annotations

import asyncio

from ..models import (
    ExecutionCase,
    ExecutionCaseCollection,
    ExecutionDocument,
    ExecutionDocumentCollection,
)
from . import aio


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "hudoc_py.execution sync functions cannot be called from inside a "
        "running event loop. Use hudoc_py.execution.aio.* instead."
    )


def search(
    *,
    state: str | None = None,
    supervision: str | None = None,
    is_closed: bool | None = None,
    case_type: str | None = None,
    language: str | None = "ENG",
    limit: int | None = None,
    page_size: int | None = None,
) -> ExecutionCaseCollection:
    return _run(
        aio.search(
            state=state,
            supervision=supervision,
            is_closed=is_closed,
            case_type=case_type,
            language=language,
            limit=limit,
            page_size=page_size,
        )
    )


def count(
    *,
    collection: str | None = None,
    state: str | None = None,
    appno: str | None = None,
    supervision: str | None = None,
    is_closed: bool | None = None,
    case_type: str | None = None,
    master_group_id: str | None = None,
    language: str | None = "ENG",
    extra: str | None = None,
) -> int:
    """Return the number of HUDOC-EXEC matches without fetching rows."""
    return _run(
        aio.count(
            collection=collection,
            state=state,
            appno=appno,
            supervision=supervision,
            is_closed=is_closed,
            case_type=case_type,
            master_group_id=master_group_id,
            language=language,
            extra=extra,
        )
    )


def search_documents(
    *,
    collection: str,
    state: str | None = None,
    appno: str | None = None,
    master_group_id: str | None = None,
    language: str | None = "ENG",
    limit: int | None = None,
    page_size: int | None = None,
) -> ExecutionDocumentCollection:
    return _run(
        aio.search_documents(
            collection=collection,
            state=state,
            appno=appno,
            master_group_id=master_group_id,
            language=language,
            limit=limit,
            page_size=page_size,
        )
    )


def fetch_case(
    appno: str,
    *,
    language: str | None = "ENG",
    with_documents: bool = True,
) -> ExecutionCase | None:
    return _run(aio.fetch_case(appno, language=language, with_documents=with_documents))


def fetch_document(
    content_store_id: str,
    *,
    with_text: bool = True,
    text_format: str = "text",
) -> ExecutionDocument | None:
    return _run(
        aio.fetch_document(
            content_store_id,
            with_text=with_text,
            text_format=text_format,
        )
    )


def fetch_text(content_store_id: str, *, format: str = "text") -> str | None:
    return _run(aio.fetch_text(content_store_id, format=format))
