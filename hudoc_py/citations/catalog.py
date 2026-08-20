"""Reproducible compact reporter index derived from public Court sources."""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path

from ..models import Case
from .authority import load_authority
from .models import (
    CitationAuthority,
    HistoricalCatalogEntry,
    HistoricalCitationCatalog,
    ReporterLocator,
)
from .reporter import parse_reporter


def _digest(entries: list[HistoricalCatalogEntry]) -> str:
    payload = json.dumps(
        [entry.model_dump(mode="json") for entry in entries],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _metadata_reporter(case: Case) -> ReporterLocator | None:
    if reporter := parse_reporter(case.casecitation or ""):
        return reporter
    published = (case.published_by or "").strip()
    if match := re.fullmatch(r"A\s*(\d+)(?:[-‑–]?([A-Z]))?", published, re.I):
        return ReporterLocator(
            family="series_a",
            number=match.group(1),
            suffix=match.group(2),
            raw=published,
        )
    return None


def build_historical_catalog(
    cases: list[Case] | None = None,
    *,
    authority: CitationAuthority | None = None,
    metadata_coverage_from: str | None = None,
    metadata_coverage_to: str | None = None,
    metadata_retrieved_at: str | None = None,
) -> HistoricalCitationCatalog:
    """Build an exact reporter index with source provenance and no network I/O."""
    authority = authority or load_authority()
    by_identity: dict[tuple[str, str, str], HistoricalCatalogEntry] = {}
    for value in authority.entries:
        if value.reporter is None or value.reporter.family not in {
            "series_a", "dr", "commission_collection", "commission_report"
        }:
            continue
        entry = HistoricalCatalogEntry(
            reporter_key=value.reporter.key,
            title=value.title,
            normalized_title=value.normalized_title,
            appnos=list(value.appnos),
            date=value.date,
            document_kind=value.document_kind,
            target_ecli=value.target_ecli,
            target_itemid=value.target_itemid,
        )
        by_identity[(entry.reporter_key, entry.normalized_title, str(entry.date or ""))] = entry
    for case in cases or []:
        reporter = _metadata_reporter(case)
        if reporter is None or reporter.family not in {
            "series_a", "dr", "commission_collection", "commission_report"
        }:
            continue
        from ..bilingual.ecli import normalize_docname

        entry = HistoricalCatalogEntry(
            reporter_key=reporter.key,
            title=case.docname,
            normalized_title=normalize_docname(case.docname),
            appnos=list(case.appno),
            date=case.kp_date,
            document_kind=("decision" if "DEC" in (case.doctype or "") else "judgment"),
            target_ecli=case.ecli,
            target_itemid=case.itemid,
        )
        by_identity[(entry.reporter_key, entry.normalized_title, str(entry.date or ""))] = entry
    entries = sorted(
        by_identity.values(),
        key=lambda value: (value.reporter_key, value.normalized_title, str(value.date or "")),
    )
    return HistoricalCitationCatalog(
        source_url=authority.source_url,
        coverage_date=authority.updated_through,
        source_sha256=authority.source_sha256,
        metadata_coverage_from=metadata_coverage_from,
        metadata_coverage_to=metadata_coverage_to,
        metadata_retrieved_at=metadata_retrieved_at,
        content_sha256=_digest(entries),
        entries=entries,
    )


def write_historical_catalog(
    catalog: HistoricalCitationCatalog, path: str | Path
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(catalog.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return target


def load_historical_catalog(path: str | Path | None = None) -> HistoricalCitationCatalog:
    if path is None:
        resource = files("hudoc_py.data").joinpath("historical_citation_catalog.json")
        return HistoricalCitationCatalog.model_validate_json(resource.read_text(encoding="utf-8"))
    return HistoricalCitationCatalog.model_validate_json(Path(path).read_text(encoding="utf-8"))


def verify_historical_catalog(catalog: HistoricalCitationCatalog) -> bool:
    return catalog.content_sha256 == _digest(catalog.entries)
