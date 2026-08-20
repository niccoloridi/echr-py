"""Async high-level API. Exposed as ``hudoc_py.aio``.

These coroutines are the canonical implementation; the sync facade in
:mod:`hudoc_py._sync` simply wraps them with :func:`asyncio.run`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp

from . import config
from .main.client import AsyncHudocClient
from .main.downloader import DOWNLOAD_HEADERS, fetch_document_docx, fetch_document_html
from .main.dsl import Q
from .main.queries import resolve_sort
from .models import Case, CaseCollection
from .text import html_to_md, html_to_text, segment_html, segment_main_sections


def _populate_text(
    case: Case,
    html: str,
    *,
    text_format: str = "text",
    segment: bool = True,
    rich_sections: bool = False,
    source_itemid: str | None = None,
    source_language: str | None = None,
) -> None:
    """Set ``case.text`` (and optionally ``case.sections``) from raw HTML.

    Stamps text provenance: ``source_itemid``/``source_language`` default to the
    case's own itemid/language, but differ when the text came from a French
    sibling (see :func:`_fetch_case_text`).
    """
    plain = html_to_text(html)
    if segment:
        if rich_sections:
            case.sections = segment_html(
                html,
                doctype=case.doctype,
                doctype_branch=case.doctype_branch,
                document_id=source_itemid or case.itemid,
            )
            plain = case.sections.full or plain
        else:
            case.sections = segment_main_sections(plain)
    if text_format == "html":
        case.text = html
    elif text_format == "md":
        case.text = html_to_md(html)
    else:
        case.text = plain
    case.text_source_itemid = source_itemid if source_itemid is not None else case.itemid
    case.text_source_language = source_language if source_language is not None else case.language


async def _fetch_case_text(
    session: aiohttp.ClientSession,
    case: Case,
    *,
    text_format: str = "text",
    segment: bool = True,
    rich_sections: bool = False,
    french_fallback: bool = True,
) -> bool:
    """Fetch and populate one case's text, falling back to its French sibling.

    Returns ``True`` if text was loaded (from either the case itself or, when
    ``french_fallback`` and ``case.french_itemid`` is set, the French sibling).
    """
    if not case.itemid:
        return False
    html = await fetch_document_html(session, case.itemid)
    source_itemid: str | None = case.itemid
    source_language = case.language

    if html is None and french_fallback and case.french_itemid:
        html = await fetch_document_html(session, case.french_itemid)
        if html is not None:
            source_itemid = case.french_itemid
            source_language = "FRE"

    if html is None:
        return False
    _populate_text(
        case,
        html,
        text_format=text_format,
        segment=segment,
        rich_sections=rich_sections,
        source_itemid=source_itemid,
        source_language=source_language,
    )
    return True


async def hydrate_texts(
    collection: list[Case],
    *,
    text_format: str = "text",
    segment: bool = False,
    rich_sections: bool = False,
    french_fallback: bool = True,
    concurrency: int = config.HUDOC_CONCURRENCY,
) -> dict[str, int]:
    """Load text into every case in ``collection`` concurrently, in place.

    Returns ``{"fetched", "fallback_used", "missing"}`` counts.
    """
    counts = {"fetched": 0, "fallback_used": 0, "missing": 0}
    if not collection:
        return counts
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(session: aiohttp.ClientSession, case: Case) -> None:
        async with semaphore:
            ok = await _fetch_case_text(
                session,
                case,
                text_format=text_format,
                segment=segment,
                rich_sections=rich_sections,
                french_fallback=french_fallback,
            )
        if not ok:
            counts["missing"] += 1
            return
        counts["fetched"] += 1
        if case.text_source_itemid != case.itemid:
            counts["fallback_used"] += 1

    async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:
        await asyncio.gather(*(_one(session, c) for c in collection))
    return counts


async def search(
    *,
    page_size: int = 100,
    limit: int | None = None,
    query: str | Q | None = None,
    hudoc_url: str | None = None,
    sort: str = "relevance",
    complete: bool = True,
    **filters: Any,
) -> CaseCollection:
    """Search HUDOC main and return parsed :class:`Case` objects.

    ``filters`` are forwarded to :func:`hudoc_py.main.queries.build_search_query`
    (e.g. ``article="3"``, ``respondent="ITA"``, ``date_from="2020-01-01"``).
    ``sort`` is ``"relevance"`` (HUDOC's ranking model), ``"date-desc"``,
    ``"date-asc"``, or a raw ``"column Direction"`` string.
    """
    if hudoc_url:
        if query is not None:
            raise ValueError("Pass either query or hudoc_url, not both")
        from .main.queries import build_search_query
        from .main.url import query_from_hudoc_url

        query = query_from_hudoc_url(hudoc_url)
        if filters:
            filters.setdefault("doctypes", ())
            filters.setdefault("languages", ())
            query = f"({query}) AND ({build_search_query(**filters)})"
            filters = {}

    async with AsyncHudocClient() as client:
        rows = await client.search(
            query=query,
            page_size=page_size,
            limit=limit,
            sort=resolve_sort(sort),
            complete=complete,
            **filters,
        )
        total = client.last_result_count
    collection = CaseCollection(Case.model_validate(r) for r in rows)
    collection.result_count = total
    return collection


async def count(
    *, query: str | Q | None = None, hudoc_url: str | None = None, **filters: Any
) -> int:
    """Return the number of HUDOC matches without fetching any rows."""
    if hudoc_url:
        if query is not None:
            raise ValueError("Pass either query or hudoc_url, not both")
        from .main.queries import build_search_query
        from .main.url import query_from_hudoc_url

        query = query_from_hudoc_url(hudoc_url)
        if filters:
            filters.setdefault("doctypes", ())
            filters.setdefault("languages", ())
            query = f"({query}) AND ({build_search_query(**filters)})"
            filters = {}
    async with AsyncHudocClient() as client:
        return await client.count(query=query, **filters)


async def smart_fetch(
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
    """Search, keep the ``top`` matches, and fetch their texts concurrently.

    The one-call "only the relevant cases" workflow: with the default
    ``sort="relevance"`` and a full-text ``query``/``text`` filter, this
    returns the ``top`` most relevant cases with text (and sections) already
    populated. Note that HUDOC's ``rank`` score is only discriminating when
    the query contains a full-text clause. ``french_fallback`` only fires for
    cases that already carry a ``french_itemid`` (i.e. reconciled corpora);
    raw search rows never do, so it is a no-op there.
    """
    collection = await search(
        query=query,
        limit=top,
        page_size=page_size,
        sort=sort,
        **filters,
    )

    if with_text and collection:
        await hydrate_texts(
            collection,
            text_format=text_format,
            segment=segment,
            rich_sections=rich_sections,
            french_fallback=french_fallback,
            concurrency=concurrency,
        )

    return collection


async def fetch_case(
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
    """Fetch metadata (and optionally full text) for a single case.

    Provide either ``appno`` or ``itemid`` (exact). When selecting by
    application number, ``language`` restricts HUDOC's language flag; without
    it HUDOC's English and French rows are both eligible. When
    ``with_text=True`` the case's ``text`` field is
    populated; ``text_format`` is one of ``"text"``, ``"md"``, ``"html"``.
    When ``segment=True`` and text was fetched, ``case.sections`` is set.
    When ``rich_sections=True``, segmentation uses the seven-section split
    (procedure / facts / complaints / the_law / operative /
    separate_opinion / appendix); otherwise the fast two-section split is
    used.

    When the case has no downloadable text and ``rescue=True``, a live search
    finds its French sibling by application number and text is loaded from
    there (stamping ``FRE`` provenance). ``french_fallback`` uses an
    already-known ``french_itemid`` for the same purpose without a search.
    """
    if not (appno or itemid):
        raise ValueError("Provide either appno or itemid")

    async with AsyncHudocClient() as client:
        if itemid:
            rows = await client.fetch_by_itemids([itemid])
        else:
            language_filter = (language.upper(),) if language else ("ENG", "FRE")
            rows = await client.search(appno=appno, languages=language_filter, limit=1)

        if not rows:
            return None
        case = Case.model_validate(rows[0])

        if with_text and case.itemid:
            async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:
                loaded = await _fetch_case_text(
                    session,
                    case,
                    text_format=text_format,
                    segment=segment,
                    rich_sections=rich_sections,
                    french_fallback=french_fallback,
                )
                # No text and rescue enabled: find the French sibling live.
                if not loaded and rescue and not case.french_itemid:
                    from .bilingual.rescue import find_french_sibling

                    fr, _appno, _tried = await find_french_sibling(client, case)
                    if fr:
                        case.french_itemid = fr
                        await _fetch_case_text(
                            session,
                            case,
                            text_format=text_format,
                            segment=segment,
                            rich_sections=rich_sections,
                            french_fallback=True,
                        )

    if docx_out is not None and case.itemid:
        await fetch_docx(case.itemid, out=docx_out)

    return case


async def fetch_docx(itemid: str, *, out: str | Path | None = None) -> bytes | None:
    """Fetch a document's raw DOCX bytes; optionally write them to ``out``.

    Returns the bytes (or ``None`` if the endpoint has no DOCX for the item).
    DOCX is a separate binary payload from the HTML body and is deliberately
    not stored on ``Case.text``.
    """
    async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:
        data = await fetch_document_docx(session, itemid)
    if data is not None and out is not None:
        Path(out).write_bytes(data)
    return data


async def fetch_text(itemid: str, *, format: str = "text") -> str | None:
    """Fetch and convert a single document's body."""
    async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:
        html = await fetch_document_html(session, itemid)
    if html is None:
        return None
    if format == "html":
        return html
    if format == "md":
        return html_to_md(html)
    return html_to_text(html)


async def list_versions(*, appno: str | None = None, ecli: str | None = None, limit: int = 500):
    """List every HUDOC language record for an application or ECLI."""
    from .versions import list_versions as _list_versions

    return await _list_versions(appno=appno, ecli=ecli, limit=limit)


async def download_versions(output_dir, **kwargs):
    """Download multilingual records and return an acquisition manifest."""
    from .versions import download_versions as _download_versions

    return await _download_versions(output_dir, **kwargs)
