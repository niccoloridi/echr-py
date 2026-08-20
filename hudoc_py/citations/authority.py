"""Import, load, and query the Court's exact case-law citation authority."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Literal

from ..bilingual.ecli import normalize_docname
from .models import CitationAuthority, CitationAuthorityEntry, CitationAuthoritySource
from .reporter import (
    APPNO_REGEX,
    extract_reference_name,
    infer_document_kind,
    infer_procedural_phase,
    normalize_reference,
    normalize_reference_key,
    parse_reference_date,
    parse_reporter,
)

OFFICIAL_AUTHORITY_URL = "https://www.echr.coe.int/documents/d/echr/case_law_references_eng"
OFFICIAL_AUTHORITY_URLS = {
    "eng": OFFICIAL_AUTHORITY_URL,
    "fra": "https://www.echr.coe.int/documents/d/echr/Case_law_references_FRA",
}
_PAGE_MARKER_RE = re.compile(
    r"^\s*\d+\s*/\s*\d+(?:\s+(?:European Court of Human Rights|Cour européenne des droits de l’homme))?\s*$",
    re.I,
)
_START_RE = re.compile(r"^[A-ZÀ-ÖØ-Þ0-9][^,]{0,180}\s+(?:v\.|c\.)\s+", re.I)
_UPDATED_RE = re.compile(
    r"(?:Updated\s+until|Mis\s+à\s+jour\s+jusqu[’']au)\s*:?\s*"
    r"(\d{1,2}(?:er)?\s+[A-Za-zÀ-ÖØ-öø-ÿ]+\s+\d{4})",
    re.I,
)
_PARAGRAPH_SLOT_RE = re.compile(r"§{1,2}\s*\.{2,}")


def _looks_like_entry_start(line: str) -> bool:
    return bool(
        _START_RE.match(line)
        or re.match(
            r"^(?:Case\s+[‘\"“]|Affaire\s+[«‘\"“]|Advisory opinion|Avis consultatif|"
            r"Decision on|Décision sur|Inter-State|Interétatique)",
            line,
            re.I,
        )
    )


def _citation_complete(value: str) -> bool:
    match = _PARAGRAPH_SLOT_RE.search(value)
    if not match:
        return False
    tail = value[match.end() :]
    reporter_hint = re.search(
        r"(?:Reports(?:\s+of\s+Judgments(?:\s+and\s+Decisions?)?)?|"
        r"Recueil(?:\s+des\s+arrêts(?:\s+et\s+décisions)?)?|"
        r"Series\s+A|Série\s+A|ECHR|CEDH|D\.?\s*R\.?)",
        tail,
        re.I,
    )
    return bool(
        (parse_reference_date(tail) or parse_reporter(tail))
        and not (reporter_hint and not parse_reporter(tail))
        and not value.rstrip().endswith(",")
    )


def _entry_id(citation: str, language: str = "eng") -> str:
    prefix = "" if language == "eng" else f"{language}:"
    return hashlib.sha256((prefix + normalize_reference(citation).casefold()).encode()).hexdigest()


def authority_entry_from_citation(
    citation: str, *, language: Literal["eng", "fra"] = "eng"
) -> CitationAuthorityEntry:
    """Parse one exact citation from the official list."""
    citation = normalize_reference(citation).strip(" •▪")
    name = extract_reference_name(citation)
    reporter = parse_reporter(citation)
    document_kind = infer_document_kind(citation)
    procedural_phase = infer_procedural_phase(citation)
    # The Court's master list supplies the official reporter form for judgments
    # without repeating the word "judgment" in every row. Explicit decision,
    # advisory-opinion, Article 50, and other qualifiers have already won above.
    if document_kind == "unknown" and reporter and reporter.family in {
        "series_a",
        "reports",
        "echr",
    }:
        document_kind = "judgment"
        procedural_phase = "merits"
    return CitationAuthorityEntry(
        entry_id=_entry_id(citation, language),
        language=language,
        citation=citation,
        normalized_citation=normalize_reference_key(citation),
        title=name,
        normalized_title=normalize_docname(name),
        appnos=list(dict.fromkeys(APPNO_REGEX.findall(citation))),
        # Official forms place the decision date either before or after the
        # ``§ ...`` insertion slot.  parse_reference_date deliberately returns
        # the last complete day-month-year, avoiding reporter years.
        date=parse_reference_date(citation),
        document_kind=document_kind,
        procedural_phase=procedural_phase,
        grand_chamber="[GC]" in citation.upper(),
        reporter=reporter,
    )


def extract_authority_citations(text: str, *, language: str = "eng") -> list[str]:
    """Recover logical citation rows from text extracted from the official PDF."""
    entries: list[str] = []
    seen: set[str] = set()
    current = ""
    for raw_line in text.replace("\f", "\n").splitlines():
        line = normalize_reference(raw_line).strip(" •▪")
        if not line or _PAGE_MARKER_RE.match(line):
            continue
        lowered = line.casefold()
        if lowered.startswith(
            (
                "european court of human rights",
                "cour européenne des droits de l’homme",
                "case-law references",
                "références de jurisprudence",
                "updated until",
                "mis à jour jusqu",
                "table of contents",
                "table des matières",
            )
        ):
            continue
        if re.match(r"^-\s*[A-Z]\s*-$", line, re.I):
            continue
        if not current:
            if not _looks_like_entry_start(line):
                continue
            current = line
        else:
            current = f"{current} {line}"
        if _citation_complete(current):
            key = normalize_reference_key(current)
            if key not in seen:
                entries.append(current)
                seen.add(key)
            current = ""
    return entries


def _pdf_text(path: str | Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError(
            "pypdf is required to import the official citation authority. "
            'Install it with: pip install "echr-py[citations]"'
        ) from exc
    return "\n\f\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _packaged_supplement_entries() -> list[CitationAuthorityEntry]:
    resource = files("hudoc_py.data").joinpath("citation_authority_supplements.json")
    supplements = CitationAuthority.model_validate_json(resource.read_text(encoding="utf-8"))
    return [
        entry.model_copy(update={"entry_source": "curated_supplement"})
        for entry in supplements.entries
    ]


def import_authority_pdf(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    updated_through: str | None = None,
    source_url: str | None = None,
    language: Literal["eng", "fra"] = "eng",
    retrieved_at: str | None = None,
) -> CitationAuthority:
    """Convert a locally downloaded official PDF into a compact JSON authority."""
    pdf = Path(pdf_path)
    payload = pdf.read_bytes()
    text = _pdf_text(pdf)
    if language not in OFFICIAL_AUTHORITY_URLS:
        raise ValueError("authority language must be 'eng' or 'fra'")
    source_url = source_url or OFFICIAL_AUTHORITY_URLS[language]
    retrieved_at = retrieved_at or dt.datetime.now(dt.UTC).isoformat()
    citations = extract_authority_citations(text, language=language)
    if updated_through is None and (match := _UPDATED_RE.search(text)):
        parsed = parse_reference_date(match.group(1))
        updated_through = parsed.isoformat() if parsed else match.group(1)
    entries = [
        authority_entry_from_citation(citation, language=language) for citation in citations
    ]
    by_citation = {entry.normalized_citation: index for index, entry in enumerate(entries)}
    for supplement in _packaged_supplement_entries():
        existing_index = by_citation.get(supplement.normalized_citation)
        if existing_index is None:
            by_citation[supplement.normalized_citation] = len(entries)
            entries.append(supplement)
            continue
        # A supplement may enrich an official row with a reviewed HUDOC
        # identifier or a documented coverage limitation. The official text
        # and its source provenance remain authoritative.
        existing = entries[existing_index]
        entries[existing_index] = existing.model_copy(
            update={
                "target_ecli": supplement.target_ecli or existing.target_ecli,
                "target_itemid": supplement.target_itemid or existing.target_itemid,
                "target_docname": supplement.target_docname or existing.target_docname,
                "target_unavailable": supplement.target_unavailable,
                "coverage_note": supplement.coverage_note or existing.coverage_note,
            }
        )
    authority = CitationAuthority(
        schema_version="citation-authority/v2",
        source_url=source_url,
        updated_through=updated_through,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        imported_at=retrieved_at,
        parser_version="7",
        coverage="full",
        sources=[CitationAuthoritySource(
            language=language,
            url=source_url,
            retrieved_at=retrieved_at,
            updated_through=updated_through,
            sha256=hashlib.sha256(payload).hexdigest(),
            entry_count=sum(entry.entry_source == "official_master" for entry in entries),
        )],
        entries=entries,
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "citation-authority.json").write_text(
        authority.model_dump_json(indent=2), encoding="utf-8"
    )
    write_authority_csv(authority, out / "citation-authority.csv")
    return authority


def _equivalence_key(entry: CitationAuthorityEntry) -> tuple[object, ...] | None:
    reporter_key = entry.reporter.key if entry.reporter else None
    common = (
        entry.date,
        entry.document_kind,
        entry.procedural_phase,
        entry.grand_chamber,
    )
    if entry.appnos:
        return ("appnos", tuple(sorted(entry.appnos)), *common, reporter_key)
    # Reports/ECHR volumes contain many documents and are never identity keys.
    # A Series A number (including its suffix) identifies one historical
    # document, subject to the cross-language one-to-one gate below.
    if entry.reporter and entry.reporter.family == "series_a":
        return ("series_a", reporter_key, *common)
    return None


def _authority_language(authority: CitationAuthority) -> Literal["eng", "fra"]:
    """Infer the language of a legacy single-edition authority."""
    languages = {entry.language for entry in authority.entries if entry.language}
    if len(languages) == 1:
        return next(iter(languages))
    return "fra" if "references_fra" in authority.source_url.casefold() else "eng"


def _authority_sources(authority: CitationAuthority) -> list[CitationAuthoritySource]:
    """Return v2 provenance, synthesising it for a checksummed v1 authority."""
    if authority.sources:
        return authority.sources
    if not authority.source_sha256:
        return []
    language = _authority_language(authority)
    return [CitationAuthoritySource(
        language=language,
        url=authority.source_url,
        retrieved_at=authority.imported_at,
        updated_through=authority.updated_through,
        sha256=authority.source_sha256,
        entry_count=sum(entry.entry_source == "official_master" for entry in authority.entries),
    )]


def merge_authorities(
    authorities: list[CitationAuthority], out_dir: str | Path | None = None
) -> CitationAuthority:
    """Combine official language editions without title-based identity guesses."""
    if not authorities:
        raise ValueError("at least one authority is required")
    sources = [source for authority in authorities for source in _authority_sources(authority)]
    entries_by_id: dict[str, CitationAuthorityEntry] = {}
    supplements: dict[str, CitationAuthorityEntry] = {}
    for authority in authorities:
        legacy_language = _authority_language(authority)
        for entry in authority.entries:
            updates: dict[str, object] = {"equivalent_entry_ids": []}
            if entry.entry_source == "curated_supplement":
                entry = entry.model_copy(update=updates)
                supplements.setdefault(entry.entry_id, entry)
                continue
            if entry.language is None:
                updates["language"] = legacy_language
            entry = entry.model_copy(update=updates)
            entries_by_id.setdefault(entry.entry_id, entry)
    entries = list(entries_by_id.values())

    # Supplements are language-neutral reviewed additions. If a newer official
    # edition now contains the exact normalized citation, enrich the official
    # row and omit the obsolete duplicate supplement.
    official_by_citation: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        official_by_citation.setdefault(entry.normalized_citation, []).append(index)
    retained_supplements: list[CitationAuthorityEntry] = []
    for supplement in supplements.values():
        official_indexes = official_by_citation.get(supplement.normalized_citation, [])
        if not official_indexes:
            retained_supplements.append(supplement)
            continue
        for index in official_indexes:
            official = entries[index]
            entries[index] = official.model_copy(
                update={
                    "target_ecli": supplement.target_ecli or official.target_ecli,
                    "target_itemid": supplement.target_itemid or official.target_itemid,
                    "target_docname": supplement.target_docname or official.target_docname,
                    "target_unavailable": supplement.target_unavailable,
                    "coverage_note": supplement.coverage_note or official.coverage_note,
                }
            )
    groups: dict[tuple[object, ...], list[int]] = {}
    for index, entry in enumerate(entries):
        if (key := _equivalence_key(entry)) is not None:
            groups.setdefault(key, []).append(index)
    for indexes in groups.values():
        by_language: dict[str | None, list[int]] = {}
        for index in indexes:
            by_language.setdefault(entries[index].language, []).append(index)
        # Equivalence is a conservative bilingual link, not a transitive
        # cluster. Any duplicate within either edition makes the group
        # ambiguous and therefore unlinked.
        if len(by_language.get("eng", [])) != 1 or len(by_language.get("fra", [])) != 1:
            continue
        eng_index = by_language["eng"][0]
        fra_index = by_language["fra"][0]
        entries[eng_index] = entries[eng_index].model_copy(
            update={"equivalent_entry_ids": [entries[fra_index].entry_id]}
        )
        entries[fra_index] = entries[fra_index].model_copy(
            update={"equivalent_entry_ids": [entries[eng_index].entry_id]}
        )
    entries.extend(retained_supplements)
    primary = authorities[0]
    merged = CitationAuthority(
        schema_version="citation-authority/v2",
        source_url=primary.source_url,
        imported_at=max(authority.imported_at for authority in authorities),
        updated_through=max(
            (value for value in (source.updated_through for source in sources) if value),
            default=None,
        ),
        parser_version="7",
        source_sha256=hashlib.sha256(
            "".join(sorted(source.sha256 for source in sources)).encode()
        ).hexdigest(),
        coverage="full",
        sources=sources,
        entries=entries,
    )
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        save_authority(merged, out / "citation-authority.json")
        write_authority_csv(merged, out / "citation-authority.csv")
    return merged


def load_authority(path: str | Path | None = None) -> CitationAuthority:
    """Load an authority JSON, defaulting to the packaged bootstrap concordance."""
    if path is None:
        data = files("hudoc_py.data")
        resource = data.joinpath("citation_authority.json")
        if not resource.is_file():
            resource = data.joinpath("citation_authority_eng.json")
        return CitationAuthority.model_validate_json(resource.read_text(encoding="utf-8"))
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / "citation-authority.json"
    return CitationAuthority.model_validate_json(candidate.read_text(encoding="utf-8"))


def save_authority(authority: CitationAuthority, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(authority.model_dump_json(indent=2), encoding="utf-8")
    return out


def write_authority_csv(authority: CitationAuthority, path: str | Path) -> Path:
    """Write a flat, human-readable mirror of an authority JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "authority_schema_version",
        "authority_updated_through",
        "authority_coverage",
        "authority_parser_version",
        "authority_source_url",
        "authority_source_sha256",
        "entry_id",
        "entry_source",
        "language",
        "equivalent_entry_ids",
        "citation",
        "title",
        "appnos",
        "date",
        "document_kind",
        "procedural_phase",
        "grand_chamber",
        "reporter_family",
        "reporter_key",
        "target_ecli",
        "target_itemid",
        "target_unavailable",
        "coverage_note",
    ]
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for entry in authority.entries:
            writer.writerow(
                {
                    "authority_schema_version": authority.schema_version,
                    "authority_updated_through": authority.updated_through,
                    "authority_coverage": authority.coverage,
                    "authority_parser_version": authority.parser_version,
                    "authority_source_url": authority.source_url,
                    "authority_source_sha256": authority.source_sha256,
                    "entry_id": entry.entry_id,
                    "entry_source": entry.entry_source,
                    "language": entry.language,
                    "equivalent_entry_ids": ";".join(entry.equivalent_entry_ids),
                    "citation": entry.citation,
                    "title": entry.title,
                    "appnos": ";".join(entry.appnos),
                    "date": entry.date.isoformat() if entry.date else None,
                    "document_kind": entry.document_kind,
                    "procedural_phase": entry.procedural_phase,
                    "grand_chamber": entry.grand_chamber,
                    "reporter_family": entry.reporter.family if entry.reporter else None,
                    "reporter_key": entry.reporter.key if entry.reporter else None,
                    "target_ecli": entry.target_ecli,
                    "target_itemid": entry.target_itemid,
                    "target_unavailable": entry.target_unavailable,
                    "coverage_note": entry.coverage_note,
                }
            )
    return out


def authority_to_dict(authority: CitationAuthority) -> dict[str, object]:
    """Return a plain JSON-compatible authority payload for external tooling."""
    return json.loads(authority.model_dump_json())
