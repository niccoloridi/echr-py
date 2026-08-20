"""French rescue: find the French sibling of an English placeholder case.

Many English HUDOC rows are "placeholders" with no downloadable body text; the
judgment text exists only in the French-language sibling document. This module
finds that sibling by application number and records the mapping.

The implementation addresses several known limitations of earlier rescue
workflows:

* **Every** application number is tried, not just the first (compound-appno
  cases were previously lost).
* ``HFADO`` (advisory opinions) is an accepted sibling doctype.
* Resume/checkpoint uses :mod:`hudoc_py.utils.jsonl` (single-event-loop appends
  cannot interleave) instead of a CSV + threading lock.

On the ``%22`` encoding pitfall: the source used ``requests`` rather than
``aiohttp`` because a *pre-encoded URL string* has its ``%22`` re-encoded to
``%2522`` by yarl. echr-py's client passes a params *dict*, which aiohttp
encodes exactly once, so :class:`AsyncHudocClient` is reused directly. If a raw
pre-encoded URL ever must be issued, wrap it as ``yarl.URL(url, encoded=True)``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .. import config
from ..main.client import AsyncHudocClient
from ..models.case import Case
from ..utils.jsonl import append_jsonl, iter_jsonl, load_processed_ids

logger = logging.getLogger(__name__)

#: Doctypes that count as a French sibling (HFADO included – source omitted it).
SIBLING_DOCTYPES = ("HFJUD", "HFDEC", "HFADO")


class RescueRecord(BaseModel):
    """One rescue attempt, appended to the checkpoint log."""

    itemid: str  # the English (placeholder) case's itemid – checkpoint id
    status: Literal["ok", "no_sibling", "error"]
    french_itemid: str | None = None
    appno_used: str | None = None
    appnos_tried: list[str] = Field(default_factory=list)
    error: str | None = None
    timestamp: str = ""


class RescueStats(BaseModel):
    """Summary of a rescue run."""

    candidates: int = 0
    skipped_resume: int = 0
    attempted: int = 0
    matched: int = 0
    no_sibling: int = 0
    errors: int = 0


def _has_text(case: Case) -> bool:
    return bool(case.text and case.text.strip())


def rescue_candidates(collection: list[Case]) -> list[Case]:
    """Cases that need a French sibling.

    A candidate has no ``french_itemid`` yet, has at least one application
    number, and looks textless – either flagged ``is_placeholder`` or with no
    loaded ``text``.
    """
    out: list[Case] = []
    for case in collection:
        if case.french_itemid:
            continue
        if not case.itemid or not case.appno:
            continue
        if case.is_placeholder or not _has_text(case):
            out.append(case)
    return out


async def find_french_sibling(
    client: AsyncHudocClient,
    case: Case,
    *,
    sibling_doctypes: tuple[str, ...] = SIBLING_DOCTYPES,
    language: str = "FRE",
    per_appno_limit: int = 25,
) -> tuple[str | None, str | None, list[str]]:
    """Search HUDOC for the French sibling of ``case``.

    Tries each application number in order; returns
    ``(french_itemid, appno_used, appnos_tried)`` for the first appno that
    yields a non-placeholder French document distinct from ``case.itemid``.
    """
    tried: list[str] = []
    for appno in case.appno:
        appno = appno.strip()
        if not appno:
            continue
        tried.append(appno)
        rows = await client.search(
            appno=appno,
            doctypes=sibling_doctypes,
            languages=(language,),
            limit=per_appno_limit,
        )
        for row in rows:
            sibling = Case.model_validate(row)
            if not sibling.itemid or sibling.itemid == case.itemid:
                continue
            if sibling.is_placeholder is True:
                continue
            return sibling.itemid, appno, tried
    return None, None, tried


async def rescue_french(
    collection: list[Case],
    *,
    checkpoint_path: str | Path,
    concurrency: int = config.HUDOC_CONCURRENCY,
    resume: bool = True,
    retry_errors: bool = False,
    limit: int | None = None,
    csv_export: str | Path | None = None,
    client: AsyncHudocClient | None = None,
) -> RescueStats:
    """Find French siblings for all rescue candidates in ``collection``.

    Appends one :class:`RescueRecord` per attempt to ``checkpoint_path`` (JSONL,
    resumable). Mutates ``collection`` in place via :func:`apply_rescue_mapping`
    when done, so callers immediately see the new ``french_itemid`` values.
    """
    stats = RescueStats()
    candidates = rescue_candidates(collection)
    stats.candidates = len(candidates)

    processed: set[str] = set()
    if resume:
        processed = load_processed_ids(checkpoint_path, id_field="itemid", ok_only=retry_errors)

    todo = [c for c in candidates if c.itemid not in processed]
    stats.skipped_resume = len(candidates) - len(todo)
    if limit is not None:
        todo = todo[:limit]
    stats.attempted = len(todo)

    sem = asyncio.Semaphore(concurrency)

    async def _run(owned_client: AsyncHudocClient) -> None:
        async def worker(case: Case) -> None:
            async with sem:
                try:
                    fr, appno_used, tried = await find_french_sibling(owned_client, case)
                    if fr:
                        record = RescueRecord(
                            itemid=case.itemid or "",
                            status="ok",
                            french_itemid=fr,
                            appno_used=appno_used,
                            appnos_tried=tried,
                        )
                        stats.matched += 1
                    else:
                        record = RescueRecord(
                            itemid=case.itemid or "",
                            status="no_sibling",
                            appnos_tried=tried,
                        )
                        stats.no_sibling += 1
                except Exception as exc:  # noqa: BLE001 – one bad case must not kill the run
                    logger.warning("Rescue failed for %s: %s", case.itemid, exc)
                    record = RescueRecord(itemid=case.itemid or "", status="error", error=str(exc))
                    stats.errors += 1
                record.timestamp = datetime.now(UTC).isoformat()
                append_jsonl(checkpoint_path, record.model_dump())

        await asyncio.gather(*(worker(c) for c in todo))

    if client is not None:
        await _run(client)
    else:
        async with AsyncHudocClient() as owned:
            await _run(owned)

    apply_rescue_mapping(collection, checkpoint_path)
    if csv_export is not None:
        export_rescue_csv(checkpoint_path, csv_export)
    logger.info(
        "Rescue done: %d matched, %d no-sibling, %d errors (of %d attempted)",
        stats.matched,
        stats.no_sibling,
        stats.errors,
        stats.attempted,
    )
    return stats


def apply_rescue_mapping(collection: list[Case], checkpoint_path: str | Path) -> int:
    """Set ``french_itemid`` on cases from ``ok`` records in the checkpoint.

    Idempotent; never overwrites an already-set ``french_itemid``. Returns the
    number of cases updated.
    """
    mapping: dict[str, str] = {}
    for rec in iter_jsonl(checkpoint_path):
        if rec.get("status") == "ok" and rec.get("french_itemid"):
            mapping[str(rec["itemid"])] = str(rec["french_itemid"])

    applied = 0
    for case in collection:
        if case.french_itemid is None and case.itemid in mapping:
            case.french_itemid = mapping[case.itemid]
            applied += 1
    return applied


def export_rescue_csv(checkpoint_path: str | Path, csv_path: str | Path) -> int:
    """Write an ``eng_itemid,french_itemid,appno`` CSV from the checkpoint.

    The output remains compatible with the historical rescue CSV format.
    Returns the number of ``ok`` rows written.
    """
    import csv

    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [rec for rec in iter_jsonl(checkpoint_path) if rec.get("status") == "ok"]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["eng_itemid", "french_itemid", "appno"])
        for rec in rows:
            writer.writerow(
                [rec.get("itemid", ""), rec.get("french_itemid", ""), rec.get("appno_used", "")]
            )
    return len(rows)
