"""End-to-end bilingual corpus build: search → reconcile → rescue → texts → save.

Produces the on-disk layout the local convenience layer
(:mod:`hudoc_py.local`) reads::

    out_dir/
        raw.jsonl            # every row returned by the search (audit/resume)
        cases.parquet        # reconciled primaries, text columns dropped
        duplicates.parquet   # rows removed during reconciliation
        texts.jsonl          # {itemid, source_itemid, source_language, format, text}
        rescue.jsonl         # French-rescue checkpoint
        rescue_mapping.csv   # eng_itemid,french_itemid,appno (compat artifact)
        report.json          # CorpusReport

Every stage is idempotent: rerunning skips already-fetched texts and resumes
the rescue from its checkpoint.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp
from pydantic import BaseModel, Field, field_validator

from .. import config
from .._aio import _fetch_case_text, search
from ..main.downloader import DOWNLOAD_HEADERS
from ..main.dsl import Q
from ..models.case import Case, CaseCollection
from ..utils.jsonl import append_jsonl, append_jsonl_many, iter_jsonl, load_processed_ids
from .reconcile import ExtraSiblingPolicy, ReconcileStats, reconcile
from .rescue import RescueStats, rescue_french

logger = logging.getLogger(__name__)


class CorpusReport(BaseModel):
    """Summary of a corpus build."""

    searched: int = 0
    reconcile: ReconcileStats = Field(default_factory=ReconcileStats)
    rescue: RescueStats | None = None
    texts: dict[str, int] = Field(default_factory=dict)
    language_texts: dict[str, int] | None = None
    citations: dict[str, Any] | None = None
    rich_tables: dict[str, int] | None = None
    selection: dict[str, int] | None = None
    out_dir: str = ""


class SelectionEntry(BaseModel):
    """One immutable corpus selection address."""

    itemid: str | None = None
    primary_itemid: str | None = None
    ecli: str | None = None
    language_itemids: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("language_itemids", mode="before")
    @classmethod
    def _normalize_language_itemids(cls, value: Any) -> dict[str, list[str]]:
        if not value:
            return {}
        if not isinstance(value, dict):
            raise ValueError("language_itemids must be an object keyed by language")
        normalized: dict[str, list[str]] = {}
        for language, raw_ids in value.items():
            code = str(language).strip().upper()
            if code not in {"ENG", "FRE"}:
                continue
            values = raw_ids if isinstance(raw_ids, list) else [raw_ids]
            normalized[code] = sorted(
                {str(itemid).strip() for itemid in values if str(itemid).strip()}
            )
        return normalized

    @property
    def canonical_itemid(self) -> str | None:
        return self.primary_itemid or self.itemid

    @property
    def selected_language_itemids(self) -> list[str]:
        return sorted(
            {
                itemid
                for language in ("ENG", "FRE")
                for itemid in self.language_itemids.get(language, [])
            }
        )


def load_selection(path: str | Path) -> list[SelectionEntry]:
    """Read item IDs/ECLIs from JSON, JSONL, CSV, or one-value-per-line text."""

    source = Path(path)
    suffix = source.suffix.lower()
    records: list[Any]
    if suffix == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("selection", payload.get("items", []))
        if not isinstance(payload, list):
            raise ValueError("Selection JSON must be a list or contain a selection/items list")
        records = payload
    elif suffix == ".jsonl":
        records = [
            json.loads(line)
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    elif suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            records = list(csv.DictReader(handle))
    else:
        records = [
            line.strip()
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    entries: list[SelectionEntry] = []
    seen: set[tuple[str | None, str | None]] = set()
    for raw in records:
        if isinstance(raw, str):
            raw = {"ecli": raw} if raw.upper().startswith("ECLI:") else {"itemid": raw}
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid selection entry: {raw!r}")
        entry = SelectionEntry.model_validate(raw)
        if not entry.canonical_itemid and not entry.ecli:
            raise ValueError(f"Selection entry has neither itemid nor ecli: {raw!r}")
        key = (entry.canonical_itemid, entry.ecli)
        if key not in seen:
            entries.append(entry)
            seen.add(key)
    return entries


def _snapshot_selection(out: Path, entries: list[SelectionEntry]) -> None:
    records = [entry.model_dump(mode="json", exclude_none=True) for entry in entries]
    encoded_records = json.dumps(
        records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload = {
        "schema_version": "hudoc-selection/v1",
        "selection_sha256": hashlib.sha256(encoded_records).hexdigest(),
        "selection": records,
    }
    target = out / "selection.json"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("selection_sha256") != payload["selection_sha256"]:
            raise ValueError("Selection differs from the existing corpus selection")
        return
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _language_version_ownership(
    entries: list[SelectionEntry],
) -> dict[str, dict[str, str | None]]:
    ownership: dict[str, dict[str, str | None]] = {}
    for entry in entries:
        for language, itemids in entry.language_itemids.items():
            for itemid in itemids:
                ownership[itemid] = {
                    "primary_itemid": entry.canonical_itemid,
                    "selection_ecli": entry.ecli,
                    "selected_language": language,
                }
    return ownership


def _save_language_versions(cases: list[Case], entries: list[SelectionEntry], path: Path) -> None:
    import pandas as pd

    ownership = _language_version_ownership(entries)
    rows = [
        {**case.model_dump(mode="json", exclude={"text", "sections"}), **ownership[case.itemid]}
        for case in cases
        if case.itemid in ownership
    ]
    columns = [
        *[name for name in Case.model_fields if name not in {"text", "sections"}],
        "primary_itemid",
        "selection_ecli",
        "selected_language",
    ]
    pd.DataFrame(rows, columns=columns).to_parquet(path, index=False)


def _seed_compatibility_jsonl(source: Path, target: Path, itemids: set[str]) -> None:
    """Copy selected successful language rows into canonical compatibility logs."""

    if not source.exists() or not itemids:
        return
    done = load_processed_ids(target, id_field="itemid")
    records = [
        record
        for record in iter_jsonl(source)
        if str(record.get("itemid") or "") in itemids
        and str(record.get("itemid") or "") not in done
    ]
    append_jsonl_many(target, records)


def _selected_primaries(
    collection: list[Case], entries: list[SelectionEntry], reconciled: list[Case]
) -> CaseCollection:
    """Honor explicit primary item IDs while retaining reconciliation backfills."""

    by_itemid = {case.itemid: case for case in collection if case.itemid}
    reconciled_by_ecli = {case.ecli.casefold(): case for case in reconciled if case.ecli}
    selected = CaseCollection()
    seen: set[str] = set()
    for entry in entries:
        if not entry.language_itemids or not entry.canonical_itemid:
            continue
        source = by_itemid.get(entry.canonical_itemid)
        if source is None or not source.itemid or source.itemid in seen:
            continue
        primary = source.model_copy(deep=True)
        merged = reconciled_by_ecli.get((primary.ecli or "").casefold())
        french_ids = entry.language_itemids.get("FRE", [])
        if primary.language != "FRE" and french_ids:
            primary.french_itemid = french_ids[0]
        elif merged is not None:
            primary.french_itemid = merged.french_itemid
        if not primary.represented_by and merged is not None:
            primary.represented_by = merged.represented_by
        selected.append(primary)
        seen.add(source.itemid)
    return selected


async def _fetch_selection(
    entries: list[SelectionEntry],
) -> tuple[CaseCollection, list[dict[str, Any]]]:
    """Resolve an immutable selection while retaining typed failures."""

    from ..main.client import AsyncHudocClient

    failures: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    async with AsyncHudocClient() as client:
        expected: dict[str, list[tuple[SelectionEntry, str | None]]] = {}
        for entry in entries:
            canonical = entry.canonical_itemid
            if canonical:
                expected.setdefault(canonical, []).append((entry, None))
            for language, itemids_for_language in entry.language_itemids.items():
                for itemid in itemids_for_language:
                    expected.setdefault(itemid, []).append((entry, language))
        itemids = sorted(expected)
        if itemids:
            try:
                found = await client.fetch_by_itemids(itemids)
            except Exception as exc:
                found = []
                failures.extend(
                    {
                        "stage": "metadata",
                        "status": "error",
                        "itemid": itemid,
                        "code": "selection_metadata_error",
                        "message": str(exc),
                    }
                    for itemid in itemids
                )
            by_id = {str(row.get("itemid")): row for row in found}
            for itemid, expectations in expected.items():
                if itemid in by_id:
                    row = by_id[itemid]
                    found_ecli = str(row.get("ecli") or "")
                    found_language = str(
                        row.get("languageisocode") or row.get("language") or ""
                    ).upper()
                    mismatches = [
                        (entry, language)
                        for entry, language in expectations
                        if (entry.ecli and found_ecli.casefold() != entry.ecli.casefold())
                        or (language and found_language != language)
                    ]
                    if mismatches:
                        mismatch_entry, mismatch_language = mismatches[0]
                        failures.append(
                            {
                                "stage": "metadata",
                                "status": "invalid",
                                "itemid": itemid,
                                "ecli": mismatch_entry.ecli,
                                "found_ecli": found_ecli or None,
                                "language": mismatch_language,
                                "found_language": found_language or None,
                                "code": "selection_identity_mismatch",
                            }
                        )
                    else:
                        rows.append(row)
                elif not any(
                    failure.get("itemid") == itemid and failure.get("status") == "error"
                    for failure in failures
                ):
                    failures.append(
                        {
                            "stage": "metadata",
                            "status": "missing",
                            "itemid": itemid,
                            "code": "selection_itemid_not_found",
                        }
                    )
        for entry in entries:
            if entry.canonical_itemid or entry.language_itemids or not entry.ecli:
                continue
            escaped = entry.ecli.replace("\\", "\\\\").replace('"', '\\"')
            try:
                candidates = await client.search(query=f'ecli:"{escaped}"', limit=20)
                matches = [
                    row
                    for row in candidates
                    if str(row.get("ecli") or "").casefold() == entry.ecli.casefold()
                ]
            except Exception as exc:
                failures.append(
                    {
                        "stage": "metadata",
                        "status": "error",
                        "ecli": entry.ecli,
                        "code": "selection_metadata_error",
                        "message": str(exc),
                    }
                )
                continue
            if not matches:
                failures.append(
                    {
                        "stage": "metadata",
                        "status": "missing",
                        "ecli": entry.ecli,
                        "code": "selection_ecli_not_found",
                    }
                )
            else:
                rows.extend(matches)

    # One metadata row can be selected by both item ID and ECLI.
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("itemid") or f"ecli:{row.get('ecli')}:{len(unique)}")
        unique.setdefault(key, row)
    return CaseCollection(Case.model_validate(row) for row in unique.values()), failures


async def build_corpus(
    out_dir: str | Path,
    *,
    query: str | Q | None = None,
    limit: int | None = None,
    page_size: int = 100,
    with_texts: bool = True,
    text_format: str = "text",
    rescue: bool = True,
    save_docx: bool = False,
    resolve_case_citations: bool = False,
    citation_authority: str | Path | None = None,
    citation_overrides: str | Path | None = None,
    extra_sibling_policy: ExtraSiblingPolicy = "drop",
    selection: str | Path | None = None,
    rich_sections: bool = False,
    concurrency: int = config.HUDOC_CONCURRENCY,
    **filters: Any,
) -> CorpusReport:
    """Build a reconciled, text-hydrated corpus under ``out_dir``.

    The default search covers both languages and all six doctypes, so ENG/FRE
    siblings arrive together for reconciliation.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = CorpusReport(out_dir=str(out))
    if rich_sections and not with_texts:
        raise ValueError("rich_sections requires text acquisition")

    # 1. Search (both languages by default), or resolve a frozen selection.
    selection_failures: list[dict[str, Any]] = []
    selection_entries: list[SelectionEntry] = []
    language_version_cases: list[Case] = []
    if selection is not None:
        if query is not None or filters:
            raise ValueError("A corpus selection cannot be combined with a query or filters")
        selection_entries = load_selection(selection)
        _snapshot_selection(out, selection_entries)
        collection, selection_failures = await _fetch_selection(selection_entries)
        ownership = _language_version_ownership(selection_entries)
        language_version_cases = [case for case in collection if case.itemid in ownership]
        if ownership:
            _save_language_versions(
                language_version_cases, selection_entries, out / "language-versions.parquet"
            )
        report.selection = {
            "requested": len(selection_entries),
            "metadata_rows": len(collection),
            "language_versions": len(language_version_cases),
            "failures": len(selection_failures),
        }
    else:
        collection = await search(query=query, limit=limit, page_size=page_size, **filters)
    report.searched = len(collection)
    raw_path = out / "raw.jsonl"
    if not raw_path.exists():
        collection.to_jsonl(str(raw_path))
    failures_path = out / "acquisition-failures.jsonl"
    if selection_failures:
        append_jsonl_many(failures_path, selection_failures)

    # 2. Reconcile into one primary per ECLI.
    result = reconcile(collection, extra_sibling_policy=extra_sibling_policy)
    report.reconcile = result.stats
    if language_version_cases:
        result.cases = _selected_primaries(collection, selection_entries, result.cases)

    # 3. French rescue for placeholders still missing a sibling.
    if rescue:
        report.rescue = await rescue_french(
            result.cases,
            checkpoint_path=out / "rescue.jsonl",
            csv_export=out / "rescue_mapping.csv",
            concurrency=concurrency,
        )

    # 4. Hydrate texts (streaming to texts.jsonl, skipping already-fetched).
    if with_texts:
        if language_version_cases:
            report.language_texts = await _hydrate_to_jsonl(
                language_version_cases,
                texts_path=out / "language-texts.jsonl",
                text_format=text_format,
                concurrency=concurrency,
                rich_sections=rich_sections,
                failures_path=failures_path,
            )
            canonical_ids = {case.itemid for case in result.cases if case.itemid}
            _seed_compatibility_jsonl(
                out / "language-texts.jsonl", out / "texts.jsonl", canonical_ids
            )
            if rich_sections:
                _seed_compatibility_jsonl(
                    out / "language-sections.jsonl", out / "sections.jsonl", canonical_ids
                )
        report.texts = await _hydrate_to_jsonl(
            result.cases,
            texts_path=out / "texts.jsonl",
            text_format=text_format,
            concurrency=concurrency,
            rich_sections=rich_sections,
            failures_path=failures_path,
        )

    # 5. Optional raw DOCX download.
    if save_docx:
        from ..main.downloader import AsyncDocumentDownloader

        downloader = AsyncDocumentDownloader(
            out / "docs",
            save_html=False,
            save_md=False,
            save_txt=False,
            save_docx=True,
        )
        await downloader.download_batch(
            [c.itemid for c in (language_version_cases or result.cases) if c.itemid],
            concurrency=concurrency,
        )

    # 6. Save parquet (text/sections dropped – they live in texts.jsonl).
    _save_parquet(result.cases, out / "cases.parquet", drop_text=True)
    _save_parquet(result.duplicates, out / "duplicates.parquet", drop_text=True)

    if rich_sections:
        from .bundle import write_rich_section_tables

        rich_source = (
            out / "language-sections.jsonl" if language_version_cases else out / "sections.jsonl"
        )
        report.rich_tables = write_rich_section_tables(out, sections_path=rich_source)

    # 7. Optional measurement-grade, one-hop citation resolution. This uses
    # only metadata and the SCL fields already present on reconciled cases; it
    # never recursively parses the fetched target documents.
    if resolve_case_citations:
        from ..citations import (
            load_authority,
            load_overrides,
            resolve_citations,
            write_resolution_artifacts,
        )
        from ..main.client import AsyncHudocClient

        citation_dir = out / "citations"
        async with AsyncHudocClient() as citation_client:
            citation_result = await resolve_citations(
                result.cases,
                authority=load_authority(citation_authority),
                client=citation_client,
                overrides=load_overrides(citation_overrides),
                cache_path=citation_dir / "lookup-cache.jsonl",
            )
        write_resolution_artifacts(citation_result, citation_dir)
        report.citations = citation_result.report.model_dump(mode="json")

    (out / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    from .bundle import generate_corpus_manifest

    generate_corpus_manifest(out)
    logger.info("Corpus built at %s: %d primaries", out, len(result.cases))
    return report


async def _hydrate_to_jsonl(
    cases: list[Case],
    *,
    texts_path: str | Path,
    text_format: str,
    concurrency: int,
    rich_sections: bool = False,
    failures_path: str | Path | None = None,
) -> dict[str, int]:
    """Fetch each case's text (French fallback on) and stream to a JSONL file."""
    counts = {"fetched": 0, "fallback_used": 0, "missing": 0, "skipped": 0}
    done = load_processed_ids(texts_path, id_field="itemid")
    text_file = Path(texts_path)
    sections_path = text_file.with_name(text_file.name.replace("texts", "sections", 1))
    section_done = load_processed_ids(sections_path, id_field="itemid") if rich_sections else done
    todo = [c for c in cases if c.itemid and (c.itemid not in done or c.itemid not in section_done)]
    counts["skipped"] = len(cases) - len(todo)
    if not todo:
        return counts

    semaphore = asyncio.Semaphore(concurrency)

    async def _one(session: aiohttp.ClientSession, case: Case) -> tuple[Case, bool]:
        async with semaphore:
            ok = await _fetch_case_text(
                session,
                case,
                text_format=text_format,
                segment=rich_sections,
                rich_sections=rich_sections,
                french_fallback=True,
            )
        if not ok:
            return case, False
        return case, True

    async with aiohttp.ClientSession(headers=DOWNLOAD_HEADERS) as session:
        results = await asyncio.gather(*(_one(session, c) for c in todo))
    for case, ok in results:
        if not ok:
            counts["missing"] += 1
            if failures_path is not None:
                append_jsonl(
                    failures_path,
                    {
                        "stage": "text",
                        "status": "missing",
                        "itemid": case.itemid,
                        "code": "document_text_not_found",
                    },
                )
            continue
        counts["fetched"] += 1
        if case.text_source_itemid != case.itemid:
            counts["fallback_used"] += 1
        if case.itemid not in done:
            append_jsonl(
                texts_path,
                {
                    "itemid": case.itemid,
                    "source_itemid": case.text_source_itemid,
                    "source_language": case.text_source_language,
                    "format": text_format,
                    "text": case.text,
                },
            )
        if rich_sections and case.itemid not in section_done and case.sections is not None:
            append_jsonl(
                sections_path,
                {
                    "itemid": case.itemid,
                    "source_itemid": case.text_source_itemid,
                    "source_language": case.text_source_language,
                    "sections": case.sections.model_dump(mode="json"),
                },
            )
    return counts


def _save_parquet(cases: list[Case], path: Path, *, drop_text: bool) -> None:
    import pandas as pd

    rows = [c.model_dump(mode="json") for c in cases]
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=list(Case.model_fields))
    if drop_text:
        df = df.drop(columns=[c for c in ("text", "sections") if c in df.columns])
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def load_cases(path: str | Path) -> CaseCollection:
    """Load a cases parquet/jsonl into a :class:`CaseCollection`."""
    from ..utils.jsonl import iter_jsonl

    p = Path(path)
    if p.suffix == ".parquet":
        import pandas as pd

        return CaseCollection.from_dataframe(pd.read_parquet(p))
    return CaseCollection.from_records(iter_jsonl(p))


__all__ = ["build_corpus", "CorpusReport", "load_cases", "load_selection", "SelectionEntry"]
