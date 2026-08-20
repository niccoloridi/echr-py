"""Async HTTP client for the public HUDOC-EXEC search service.

The state-partitioning workaround for the service's 10k result limit is
preserved.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .. import config
from .collections import CASE_SELECT_FIELDS, DOC_SELECT_FIELDS, EXEC_RANKING_MODEL_ID
from .queries import build_exec_query

logger = logging.getLogger(__name__)

SEARCH_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "echr-py/0.1 (+https://github.com/niccoloridi/echr-py)",
}


class AsyncHudocExecClient:
    """Async context manager for HUDOC-EXEC.

    Usage::

        async with AsyncHudocExecClient() as client:
            cases = await client.search_cases(state="ITA", limit=200)
            plans = await client.search_documents(collection="acp", state="ITA", limit=50)
    """

    def __init__(
        self,
        *,
        batch_size: int = 500,
        max_retries: int = config.HUDOC_MAX_RETRIES,
        rate_limit_seconds: float = config.HUDOC_RATE_LIMIT_SECONDS,
        timeout: float = 60.0,
    ):
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> AsyncHudocExecClient:
        self.session = aiohttp.ClientSession(headers=SEARCH_HEADERS, timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    def _require_session(self) -> aiohttp.ClientSession:
        if self.session is None:
            raise RuntimeError("AsyncHudocExecClient must be used as an async context manager.")
        return self.session

    async def _fetch_page(
        self,
        *,
        query: str,
        start: int,
        length: int,
        select: str,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query": query,
            "select": select,
            "sort": "",
            "start": start,
            "length": length,
            "rankingModelId": EXEC_RANKING_MODEL_ID,
        }
        session = self._require_session()
        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.get(config.HUDOC_EXEC_SEARCH_URL, params=params) as resp:
                    if resp.status == 200 and "json" in (resp.content_type or ""):
                        data = await resp.json()
                        results = data.get("results", []) or []
                        return [r["columns"] for r in results if "columns" in r]
                    if resp.status in (429, 500, 502, 503, 504):
                        wait = self.rate_limit_seconds * (2 ** (attempt - 1))
                        logger.warning(
                            "HUDOC-EXEC %s on attempt %d/%d (offset %d); waiting %.1fs",
                            resp.status,
                            attempt,
                            self.max_retries,
                            start,
                            wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    body = await resp.text()
                    raise RuntimeError(
                        f"HUDOC-EXEC search failed: status={resp.status} body={body[:200]!r}"
                    )
            except aiohttp.ClientError as exc:
                if attempt == self.max_retries:
                    raise
                wait = self.rate_limit_seconds * (2 ** (attempt - 1))
                logger.warning("Network error %s; retrying in %.1fs", exc, wait)
                await asyncio.sleep(wait)
        return []

    async def count(self, query: str) -> int:
        """Return the result count for a query without fetching rows."""
        params: dict[str, Any] = {
            "query": query,
            "select": "execidentifier",
            "sort": "",
            "start": 0,
            "length": 0,
            "rankingModelId": EXEC_RANKING_MODEL_ID,
        }
        session = self._require_session()
        async with session.get(config.HUDOC_EXEC_SEARCH_URL, params=params) as resp:
            if resp.status == 200 and "json" in (resp.content_type or ""):
                data = await resp.json()
                return int(data.get("resultcount", 0))
        return 0

    async def iter_results(
        self,
        *,
        query: str,
        select: str,
        page_size: int | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        page = page_size or self.batch_size
        emitted = 0
        start = 0
        while True:
            need = page if limit is None else min(page, limit - emitted)
            if need <= 0:
                return
            rows = await self._fetch_page(query=query, start=start, length=need, select=select)
            if not rows:
                return
            for row in rows:
                yield row
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
            start += len(rows)
            await asyncio.sleep(self.rate_limit_seconds)

    async def search_cases(
        self,
        *,
        state: str | None = None,
        supervision: str | None = None,
        is_closed: bool | None = None,
        case_type: str | None = None,
        master_group_id: str | None = None,
        language: str | None = "ENG",
        limit: int | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search case-level records (``execdocumenttypecollection=CEC``)."""
        query = build_exec_query(
            collection="CEC",
            state=state,
            supervision=supervision,
            is_closed=is_closed,
            case_type=case_type,
            master_group_id=master_group_id,
            language=language,
        )
        return [
            row
            async for row in self.iter_results(
                query=query,
                select=CASE_SELECT_FIELDS,
                page_size=page_size,
                limit=limit,
            )
        ]

    async def search_documents(
        self,
        *,
        collection: str,
        state: str | None = None,
        appno: str | None = None,
        master_group_id: str | None = None,
        language: str | None = "ENG",
        limit: int | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search non-case documents (action plans, CM decisions, communications)."""
        query = build_exec_query(
            collection=collection,
            state=state,
            appno=appno,
            master_group_id=master_group_id,
            language=language,
        )
        return [
            row
            async for row in self.iter_results(
                query=query,
                select=DOC_SELECT_FIELDS,
                page_size=page_size,
                limit=limit,
            )
        ]

    async def fetch_for_appno(
        self,
        appno: str,
        *,
        language: str | None = "ENG",
        limit: int | None = 10000,
    ) -> list[dict[str, Any]]:
        """Fetch every document of any collection for a given application number."""
        all_fields = set(CASE_SELECT_FIELDS.split(",")) | set(DOC_SELECT_FIELDS.split(","))
        select = ",".join(sorted(all_fields))
        query = build_exec_query(appno=appno, language=language)
        return [
            row
            async for row in self.iter_results(
                query=query,
                select=select,
                limit=limit,
            )
        ]
