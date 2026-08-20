"""Async HTTP client for the HUDOC main search API.

The client deliberately provides:

* No domain-specific defaults: callers pass a query via :func:`build_search_query`.
* Iterator/paginated interface in addition to one-shot fetch.
* Retries with exponential backoff (shared with the downloader).
* Returns raw column dicts; mapping to :class:`hudoc_py.models.case.Case` is
  done one layer up by the sync/async facade.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from .. import config
from .dsl import Q, as_query_string
from .queries import METADATA_SELECT_FIELDS, build_search_query

logger = logging.getLogger(__name__)

HUDOC_MAX_RESULTS_PER_QUERY = 10_000


class HudocResultWindowError(RuntimeError):
    """A complete HUDOC query cannot be partitioned without losing rows."""

SEARCH_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "echr-py/0.1 (+https://github.com/niccoloridi/echr-py)",
}


class AsyncHudocClient:
    """Async context manager wrapping a single aiohttp session.

    Usage::

        async with AsyncHudocClient() as client:
            rows = await client.search(article="3", respondent="ITA", limit=100)
    """

    def __init__(
        self,
        *,
        max_retries: int = config.HUDOC_MAX_RETRIES,
        rate_limit_seconds: float = config.HUDOC_RATE_LIMIT_SECONDS,
        timeout: float = 30.0,
    ):
        self.max_retries = max_retries
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.session: aiohttp.ClientSession | None = None
        #: Total matches reported by the most recent request's envelope.
        self.last_result_count: int | None = None

    async def __aenter__(self) -> AsyncHudocClient:
        self.session = aiohttp.ClientSession(headers=SEARCH_HEADERS, timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    def _require_session(self) -> aiohttp.ClientSession:
        if self.session is None:
            raise RuntimeError(
                "AsyncHudocClient must be used as an async context manager: "
                "`async with AsyncHudocClient() as client: ...`"
            )
        return self.session

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        """GET the search endpoint with retries; return the full JSON envelope."""
        session = self._require_session()

        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.get(config.HUDOC_SEARCH_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "resultcount" in data:
                            self.last_result_count = int(data["resultcount"])
                        return data
                    if resp.status in (429, 500, 502, 503, 504):
                        wait = self.rate_limit_seconds * (2 ** (attempt - 1))
                        logger.warning(
                            "HUDOC %s on attempt %d/%d (offset %s); backing off %.1fs",
                            resp.status, attempt, self.max_retries,
                            params.get("start"), wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    body = await resp.text()
                    raise RuntimeError(
                        f"HUDOC search failed: status={resp.status} body={body[:200]!r}"
                    )
            except aiohttp.ClientError as exc:
                if attempt == self.max_retries:
                    raise
                wait = self.rate_limit_seconds * (2 ** (attempt - 1))
                logger.warning("Network error %s; retrying in %.1fs", exc, wait)
                await asyncio.sleep(wait)

        return {}

    async def _fetch_page(
        self,
        *,
        query: str,
        start: int,
        length: int,
        select: str = METADATA_SELECT_FIELDS,
        sort: str = "",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query": query,
            "select": select,
            "sort": sort,
            "length": length,
            "start": start,
            "rankingModelId": config.HUDOC_RANKING_MODEL_ID,
        }
        data = await self._request(params)
        results = data.get("results", []) or []
        return [r["columns"] for r in results if "columns" in r]

    async def count(self, *, query: str | Q | None = None, **filter_kwargs: Any) -> int:
        """Return the number of matches for a query without fetching rows."""
        query = as_query_string(query)
        if query is None:
            query = build_search_query(**filter_kwargs)

        params: dict[str, Any] = {
            "query": query,
            "select": "itemid",
            "sort": "",
            "length": 0,
            "start": 0,
            "rankingModelId": config.HUDOC_RANKING_MODEL_ID,
        }
        data = await self._request(params)
        if "resultcount" in data:
            return int(data["resultcount"])
        # length=0 is verified on the EXEC endpoint but not the main one;
        # fall back to a one-row page whose envelope carries the count.
        params["length"] = 1
        data = await self._request(params)
        return int(data.get("resultcount", 0))

    async def iter_results(
        self,
        *,
        query: str | Q | None = None,
        page_size: int = 100,
        limit: int | None = None,
        select: str = METADATA_SELECT_FIELDS,
        sort: str = "",
        complete: bool = True,
        **filter_kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield raw HUDOC ``columns`` dicts one by one.

        Either pass a fully-built ``query`` (string or :class:`Q`) or filter
        keyword arguments accepted by :func:`build_search_query`. ``sort`` is
        HUDOC's raw sort string (``""`` = relevance ranking).
        """
        date_from = _as_date(filter_kwargs.get("date_from"), default=date(1959, 1, 1))
        date_to = _as_date(filter_kwargs.get("date_to"), default=date.today())
        query = as_query_string(query)
        if query is None:
            query = build_search_query(**filter_kwargs)

        if not 1 <= page_size <= 500:
            raise ValueError("page_size must be between 1 and 500")
        if limit is not None and limit < 0:
            raise ValueError("limit cannot be negative")

        first_need = page_size if limit is None else min(page_size, limit)
        if first_need <= 0:
            return
        self.last_result_count = None
        first_page = await self._fetch_page(
            query=query, start=0, length=first_need, select=select, sort=sort
        )
        reported_total = self.last_result_count
        count_known = reported_total is not None
        total = reported_total if reported_total is not None else len(first_page)

        needs_partition = total > HUDOC_MAX_RESULTS_PER_QUERY and (
            limit is None or limit > HUDOC_MAX_RESULTS_PER_QUERY
        )
        if needs_partition:
            if not complete:
                raise HudocResultWindowError(
                    f"HUDOC reports {total} matches, above its "
                    f"{HUDOC_MAX_RESULTS_PER_QUERY}-row query window; "
                    "enable complete retrieval or narrow the query"
                )
            if sort not in {"kpdate Ascending", "kpdate Descending"}:
                logger.warning(
                    "Complete retrieval is partitioning %s HUDOC matches by date. "
                    "All rows are retained, but a single global relevance ordering "
                    "cannot be reconstructed across partitions; use a date sort when "
                    "the saved order matters.",
                    total,
                )
            windows = await self._complete_date_windows(
                query, date_from=date_from, date_to=date_to, expected_total=total
            )
            if sort == "kpdate Descending":
                windows.reverse()
            emitted = 0
            for window_query, window_count in windows:
                remaining = None if limit is None else limit - emitted
                if remaining is not None and remaining <= 0:
                    break
                window_limit = window_count if remaining is None else min(window_count, remaining)
                async for row in self._iter_bounded_pages(
                    query=window_query,
                    page_size=page_size,
                    limit=window_limit,
                    select=select,
                    sort=sort,
                ):
                    yield row
                    emitted += 1
            self.last_result_count = total
            return

        emitted = 0
        start = 0
        page = first_page
        while page:
            for row in page:
                yield row
                emitted += 1
                if limit is not None and emitted >= limit:
                    self.last_result_count = total
                    return
            start += len(page)
            if count_known and start >= min(total, HUDOC_MAX_RESULTS_PER_QUERY):
                self.last_result_count = total
                return
            need = page_size if limit is None else min(page_size, limit - emitted)
            if need <= 0:
                self.last_result_count = total
                return
            await asyncio.sleep(self.rate_limit_seconds)
            page = await self._fetch_page(
                query=query, start=start, length=need, select=select, sort=sort
            )
        self.last_result_count = total if count_known else emitted

    async def _iter_bounded_pages(
        self,
        *,
        query: str,
        page_size: int,
        limit: int,
        select: str,
        sort: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield at most one already-safe HUDOC result window."""
        emitted = 0
        while emitted < limit:
            need = min(page_size, limit - emitted)
            page = await self._fetch_page(
                query=query, start=emitted, length=need, select=select, sort=sort
            )
            if not page:
                raise HudocResultWindowError(
                    f"HUDOC returned an empty page at offset {emitted} "
                    f"inside a {limit}-row bounded window"
                )
            for row in page:
                yield row
                emitted += 1
                if emitted >= limit:
                    break
            if emitted < limit:
                await asyncio.sleep(self.rate_limit_seconds)

    async def _complete_date_windows(
        self,
        query: str,
        *,
        date_from: date,
        date_to: date,
        expected_total: int,
    ) -> list[tuple[str, int]]:
        """Bisect an oversized query into non-overlapping, checked date windows."""
        if date_from > date_to:
            raise HudocResultWindowError("Complete-search date_from is after date_to")

        async def split(start: date, end: date) -> list[tuple[str, int]]:
            window_query = _with_date_window(query, start, end)
            count = await self.count(query=window_query)
            if count <= HUDOC_MAX_RESULTS_PER_QUERY:
                return [(window_query, count)] if count else []
            if start == end:
                raise HudocResultWindowError(
                    f"{count} HUDOC rows share {start.isoformat()}, exceeding the "
                    f"{HUDOC_MAX_RESULTS_PER_QUERY}-row query window; narrow the query"
                )
            midpoint = start + timedelta(days=(end - start).days // 2)
            return await split(start, midpoint) + await split(midpoint + timedelta(days=1), end)

        windows = await split(date_from, date_to)
        dated_total = sum(count for _, count in windows)
        if dated_total != expected_total:
            raise HudocResultWindowError(
                "Date partition did not preserve the complete HUDOC result set: "
                f"initial query reported {expected_total}, partitions reported {dated_total}. "
                "Narrow the query or supply explicit date bounds; no partial result was returned."
            )
        return windows

    async def search(
        self,
        *,
        query: str | Q | None = None,
        page_size: int = 100,
        limit: int | None = None,
        select: str = METADATA_SELECT_FIELDS,
        sort: str = "",
        complete: bool = True,
        **filter_kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Eager variant: collect all matching rows into a list."""
        return [
            row
            async for row in self.iter_results(
                query=query,
                page_size=page_size,
                limit=limit,
                select=select,
                sort=sort,
                complete=complete,
                **filter_kwargs,
            )
        ]

    async def fetch_by_itemids(
        self,
        itemids: Iterable[str],
        *,
        chunk_size: int = 20,
        select: str = METADATA_SELECT_FIELDS,
    ) -> list[dict[str, Any]]:
        """Fetch metadata for known item IDs, retrying bulk omissions exactly.

        HUDOC occasionally returns fewer rows than requested for a valid OR
        query, especially for older records.  Missing IDs are therefore
        retried individually before being classified as absent.
        """
        ids = list(dict.fromkeys(str(itemid) for itemid in itemids))
        found_by_id: dict[str, dict[str, Any]] = {}
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            query = build_search_query(itemid=chunk)
            rows = await self._fetch_page(query=query, start=0, length=len(chunk), select=select)
            for row in rows:
                itemid = str(row.get("itemid") or "")
                if itemid in chunk:
                    found_by_id[itemid] = row
            missing = [itemid for itemid in chunk if itemid not in found_by_id]
            if missing and len(chunk) > 1:
                logger.warning(
                    "HUDOC bulk item-ID lookup omitted %d/%d rows; retrying individually",
                    len(missing),
                    len(chunk),
                )
                for itemid in missing:
                    exact_rows = await self._fetch_page(
                        query=build_search_query(itemid=[itemid]),
                        start=0,
                        length=1,
                        select=select,
                    )
                    exact = next(
                        (row for row in exact_rows if str(row.get("itemid") or "") == itemid),
                        None,
                    )
                    if exact is not None:
                        found_by_id[itemid] = exact
                    await asyncio.sleep(self.rate_limit_seconds)
            await asyncio.sleep(self.rate_limit_seconds)
        return [found_by_id[itemid] for itemid in ids if itemid in found_by_id]


def _as_date(value: Any, *, default: date) -> date:
    if value is None:
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().split("T", 1)[0]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Expected an ISO date, got {value!r}") from exc


def _with_date_window(query: str, start: date, end: date) -> str:
    return (
        f"({query}) AND "
        f'(kpdate>="{start.isoformat()}T00:00:00.0Z" AND '
        f'kpdate<="{end.isoformat()}T23:59:59.9Z")'
    )
