"""Async high-level API for HUDOC-EXEC. Exposed as ``hudoc_py.execution.aio``."""

from __future__ import annotations

import aiohttp

from ..models import (
    ExecutionCase,
    ExecutionCaseCollection,
    ExecutionDocument,
    ExecutionDocumentCollection,
)
from ..text import html_to_md, html_to_text
from .client import AsyncHudocExecClient
from .collections import collection_code
from .downloader import DOWNLOAD_HEADERS, fetch_exec_document_html
from .queries import build_exec_query

# Map document_type_collection → ExecutionCase bucket attribute.
_BUCKET_BY_COLLECTION = {
    "acp": "action_plans",
    "acr": "action_reports",
    "CMDEC": "cm_decisions",
    "CMNOT": "cm_decisions",
    "CMINF": "cm_decisions",
    "HEXEC": "cm_decisions",
    "apo": "communications",
    "gvo": "communications",
    "eo": "communications",
    "ngo": "communications",
    "nhri": "communications",
    "igo": "communications",
    "nto": "communications",
    "oorg": "communications",
    "oo": "communications",
    "EXECUTION": "resolutions",
    "MERITS": "resolutions",
}


async def search(
    *,
    state: str | None = None,
    supervision: str | None = None,
    is_closed: bool | None = None,
    case_type: str | None = None,
    master_group_id: str | None = None,
    language: str | None = "ENG",
    limit: int | None = None,
    page_size: int | None = None,
) -> ExecutionCaseCollection:
    """Search HUDOC-EXEC case-level records."""
    async with AsyncHudocExecClient() as client:
        rows = await client.search_cases(
            state=state,
            supervision=supervision,
            is_closed=is_closed,
            case_type=case_type,
            master_group_id=master_group_id,
            language=language,
            limit=limit,
            page_size=page_size,
        )
    return ExecutionCaseCollection(ExecutionCase.model_validate(r) for r in rows)


async def count(
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
    query = build_exec_query(
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
    async with AsyncHudocExecClient() as client:
        return await client.count(query)


async def search_documents(
    *,
    collection: str,
    state: str | None = None,
    appno: str | None = None,
    master_group_id: str | None = None,
    language: str | None = "ENG",
    limit: int | None = None,
    page_size: int | None = None,
) -> ExecutionDocumentCollection:
    """Search non-case HUDOC-EXEC documents (action plans, CM decisions, etc.)."""
    async with AsyncHudocExecClient() as client:
        rows = await client.search_documents(
            collection=collection,
            state=state,
            appno=appno,
            master_group_id=master_group_id,
            language=language,
            limit=limit,
            page_size=page_size,
        )
    return ExecutionDocumentCollection(ExecutionDocument.model_validate(r) for r in rows)


async def fetch_case(
    appno: str,
    *,
    language: str | None = "ENG",
    with_documents: bool = True,
) -> ExecutionCase | None:
    """Fetch a single execution case by application number.

    When ``with_documents=True``, the returned ExecutionCase has its
    ``action_plans``, ``action_reports``, ``cm_decisions``, ``communications``,
    and ``resolutions`` collections populated from a single bulk query.
    """
    async with AsyncHudocExecClient() as client:
        rows = await client.fetch_for_appno(appno, language=language)

    if not rows:
        return None

    case_rows = [
        row for row in rows if collection_code(row.get("execdocumenttypecollection")) == "CEC"
    ]
    case_row = case_rows[0] if case_rows else rows[0]
    case = ExecutionCase.model_validate(case_row)

    if with_documents:
        for row in rows:
            coll = collection_code(row.get("execdocumenttypecollection"))
            if coll == "CEC":
                continue
            bucket = _BUCKET_BY_COLLECTION.get(coll or "")
            if bucket:
                getattr(case, bucket).append(ExecutionDocument.model_validate(row))

    return case


async def fetch_document(
    content_store_id: str,
    *,
    with_text: bool = True,
    text_format: str = "text",
) -> ExecutionDocument | None:
    """Fetch a HUDOC-EXEC document body by ``execcontentstoreid``.

    The EXEC conversion endpoint looks up by the internal storage UUID
    (``execcontentstoreid`` on an :class:`ExecutionDocument`), not the
    human-readable ``execidentifier``. Typical flow::

        docs = search_documents(collection="acp", state="ITA")
        for d in docs:
            full = fetch_document(d.content_store_id)

    Returns an :class:`ExecutionDocument` populated with source text, or
    ``None`` if the body could not be retrieved. The API returns official
    source content for downstream, researcher-defined coding.
    """
    async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:
        html = await fetch_exec_document_html(session, content_store_id)

    if html is None:
        return None

    doc = ExecutionDocument(content_store_id=content_store_id)
    if with_text:
        if text_format == "html":
            doc.text = html
        elif text_format == "md":
            doc.text = html_to_md(html)
        else:
            doc.text = html_to_text(html)

    return doc


async def fetch_text(content_store_id: str, *, format: str = "text") -> str | None:
    """Fetch the body of an EXEC document by ``execcontentstoreid``.

    Returns the body converted to plain text (default), Markdown, or raw HTML.
    """
    async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:
        html = await fetch_exec_document_html(session, content_store_id)
    if html is None:
        return None
    if format == "html":
        return html
    if format == "md":
        return html_to_md(html)
    return html_to_text(html)


# Re-export the bucket map so callers can extend it.
BUCKET_BY_COLLECTION: dict[str, str] = dict(_BUCKET_BY_COLLECTION)

# Aliases for users used to the "fetch_text" naming on the main namespace.
__all__ = [
    "search",
    "count",
    "search_documents",
    "fetch_case",
    "fetch_document",
    "fetch_text",
    "BUCKET_BY_COLLECTION",
]
