"""Deterministic, evidence-gated SCL citation resolution."""

from __future__ import annotations

import asyncio
import csv
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..bilingual.ecli import normalize_docname, normalize_ecli
from ..models import Case
from ..utils.jsonl import append_jsonl, iter_jsonl
from .authority import load_authority
from .catalog import load_historical_catalog
from .models import (
    CitationAuthority,
    CitationAuthorityEntry,
    CitationCandidate,
    CitationMention,
    CitationOverride,
    CitationResolution,
    CitationResolutionReport,
    CitationResolutionResult,
    HistoricalCatalogEntry,
    HistoricalCitationCatalog,
)
from .reporter import (
    extract_reference_name,
    infer_document_kind,
    infer_procedural_phase,
    locate_source_context,
    parse_published_reporter,
    parse_reporter,
    parse_scl_mentions,
    publication_reporter_key,
)


def canonical_node_id(case: Case) -> str:
    ecli = normalize_ecli(case.ecli)
    if ecli:
        return f"ecli:{ecli}"
    if case.itemid:
        return f"itemid:{case.itemid}"
    raise ValueError("citation target must have an ECLI or HUDOC itemid")


def _candidate_phase(case: Case) -> str:
    description = " ".join(
        value
        for value in (
            case.docname,
            case.casecitation,
            case.typedescription,
            case.doctype,
        )
        if value
    )
    phase = infer_procedural_phase(description)
    if phase != "unknown":
        return phase
    collection = (case.document_collection_id or "").upper()
    if "DECCOMMISSION" in collection:
        return "commission_decision"
    if ";REPORTS" in collection:
        return "commission_report"
    doctype = (case.doctype or "").upper()
    if "DEC" in doctype:
        return "admissibility"
    if "JUD" in doctype:
        # The Court's citation convention treats an unqualified judgment as
        # the merits document.  Explicit phase suffixes were handled above.
        return "merits"
    if "ADO" in doctype:
        return "advisory_opinion"
    return "unknown"


def _candidate_kind(case: Case) -> str:
    collection = (case.document_collection_id or "").upper()
    if "DECCOMMISSION" in collection or ";REPORTS" in collection:
        return "commission"
    text = " ".join(
        value for value in (case.docname, case.casecitation, case.typedescription) if value
    )
    inferred = infer_document_kind(text)
    if inferred != "unknown":
        return inferred
    doctype = (case.doctype or "").upper()
    if "DEC" in doctype:
        return "decision"
    if "JUD" in doctype:
        return "judgment"
    if "ADO" in doctype:
        return "advisory"
    if "COMMISSION" in collection:
        return "commission"
    return "unknown"


def _pick_display_case(group: list[Case]) -> Case:
    return sorted(
        group,
        key=lambda case: (
            case.is_placeholder is True,
            (case.language or "").upper() != "ENG",
            case.itemid or "",
        ),
    )[0]


def _is_eligible_target(case: Case) -> bool:
    """Return whether a HUDOC row represents a citable case-law document.

    HUDOC app-number searches also return legal-information notes (CLIN),
    communicated cases, press releases, and execution resolutions.  Those are
    metadata about a decision, not alternative document identities.  Historical
    Commission decisions and reports remain eligible through the DECISIONS and
    REPORTS collections.
    """
    title = (case.docname or "").strip().casefold()
    if title.startswith(("legal summary -", "résumé juridique -")):
        return False
    collection = (case.document_collection_id or "").upper()
    if any(
        marker in collection
        for marker in (";JUDGMENTS", ";DECISIONS", ";REPORTS", ";ADVISORYOPINIONS")
    ):
        return True
    doctype = (case.doctype or "").upper()
    return any(marker in doctype for marker in ("JUD", "DEC", "ADO", "ADV", "REP"))


class TargetCatalog:
    """Canonical ECLI/itemid index over local and fetched HUDOC metadata."""

    def __init__(self, cases: Iterable[Case], *, source_node_ids: set[str] | None = None):
        self._source_node_ids = source_node_ids or set()
        self._groups: dict[str, list[Case]] = defaultdict(list)
        self.candidates: dict[str, CitationCandidate] = {}
        self.by_appno: dict[str, set[str]] = defaultdict(set)
        # Keep every node associated with an identifier.  HUDOC metadata is
        # normally unique, but silently overwriting a duplicate would turn a
        # catalog conflict into an apparently exact match.
        self.by_ecli: dict[str, set[str]] = defaultdict(set)
        self.by_itemid: dict[str, set[str]] = defaultdict(set)
        self.by_advisory_request_id: dict[str, set[str]] = defaultdict(set)
        self.by_title: dict[str, set[str]] = defaultdict(set)
        self.add_cases(cases)

    def add_cases(self, cases: Iterable[Case]) -> None:
        changed: set[str] = set()
        for case in cases:
            if (not case.ecli and not case.itemid) or not _is_eligible_target(case):
                continue
            node_id = canonical_node_id(case)
            if all(existing.itemid != case.itemid for existing in self._groups[node_id]):
                self._groups[node_id].append(case)
                changed.add(node_id)
        for node_id in changed:
            self._refresh(node_id)

    def _refresh(self, node_id: str) -> None:
        group = self._groups[node_id]
        chosen = _pick_display_case(group)
        appnos = list(dict.fromkeys(appno for case in group for appno in case.appno if appno))
        casecitations = list(
            dict.fromkeys(case.casecitation for case in group if case.casecitation)
        )
        reporter_keys = list(
            dict.fromkeys(
                reporter.key
                for citation in casecitations
                if (reporter := parse_reporter(citation)) is not None
            )
        )
        published_reporter_keys = list(
            dict.fromkeys(
                publication_reporter_key(reporter)
                for case in group
                if case.published_by
                if (reporter := parse_published_reporter(case.published_by)) is not None
            )
        )
        ecli = normalize_ecli(chosen.ecli)
        candidate = CitationCandidate(
            node_id=node_id,
            itemid=chosen.itemid,
            ecli=ecli,
            advisory_request_id=chosen.advop_identifier,
            docname=chosen.docname,
            title_aliases=list(dict.fromkeys(case.docname for case in group if case.docname)),
            reporter_keys=reporter_keys,
            published_reporter_keys=published_reporter_keys,
            casecitations=casecitations,
            appnos=appnos,
            date=chosen.kp_date,
            language=chosen.language,
            doctype=chosen.doctype,
            document_kind=_candidate_kind(chosen),
            procedural_phase=_candidate_phase(chosen),  # type: ignore[arg-type]
            grand_chamber=(chosen.doctype_branch or "").upper() == "GRANDCHAMBER"
            or "ADVISORYOPINIONS;PROTOCOL16;OPINIONS"
            in (chosen.document_collection_id or "").upper(),
            respondent=list(chosen.respondent),
            is_placeholder=chosen.is_placeholder,
            in_source_corpus=node_id in self._source_node_ids,
            hudoc_url=f"https://hudoc.echr.coe.int/eng?i={chosen.itemid}"
            if chosen.itemid
            else None,
            positive_evidence=(
                ["HUDOC formation Grand Chamber"]
                if (chosen.doctype_branch or "").upper() == "GRANDCHAMBER"
                or ";GRANDCHAMBER;" in (chosen.document_collection_id or "").upper()
                else ["HUDOC formation Chamber"]
                if (chosen.doctype_branch or "").upper() == "CHAMBER"
                or ";CHAMBER;" in (chosen.document_collection_id or "").upper()
                else []
            ),
        )
        self.candidates[node_id] = candidate
        for case in group:
            if case.itemid:
                self.by_itemid[case.itemid].add(node_id)
            normalized_ecli = normalize_ecli(case.ecli)
            if normalized_ecli:
                self.by_ecli[normalized_ecli].add(node_id)
            if case.advop_identifier:
                self.by_advisory_request_id[case.advop_identifier.upper()].add(node_id)
            for appno in case.appno:
                if appno:
                    self.by_appno[appno].add(node_id)
            if case.docname:
                self.by_title[normalize_docname(case.docname)].add(node_id)
                # HUDOC often appends a procedural suffix to an otherwise
                # generic title, while SCL prints the phase separately.
                # This relaxed alias generates candidates only; evidence
                # gating still chooses the document.
                base_title = extract_reference_name(case.docname)
                if base_title:
                    self.by_title[normalize_docname(base_title)].add(node_id)

    def for_appnos(self, appnos: Iterable[str]) -> list[CitationCandidate]:
        ids: set[str] = set()
        for appno in appnos:
            ids.update(self.by_appno.get(appno, set()))
        return [self.candidates[node_id].model_copy(deep=True) for node_id in sorted(ids)]

    def for_titles(self, titles: Iterable[str | None]) -> list[CitationCandidate]:
        ids: set[str] = set()
        for title in titles:
            normalized = normalize_docname(title)
            if normalized:
                ids.update(self.by_title.get(normalized, set()))
        return [self.candidates[node_id].model_copy(deep=True) for node_id in sorted(ids)]

    def for_advisory_request_ids(self, identifiers: Iterable[str]) -> list[CitationCandidate]:
        selected: dict[str, CitationCandidate] = {}
        for identifier in identifiers:
            ids = self.by_advisory_request_id.get(identifier.upper(), set())
            for node_id in sorted(ids):
                selected[node_id] = self.candidates[node_id].model_copy(deep=True)
        return list(selected.values())

    def exact_candidates(
        self, *, ecli: str | None = None, itemid: str | None = None
    ) -> list[CitationCandidate]:
        """Return only candidates compatible with every supplied identifier.

        Supplying both identifiers is a conjunction.  A missing or conflicting
        member must not be softened into a match on whichever identifier was
        checked first.
        """
        selections: list[set[str]] = []
        if ecli:
            selections.append(set(self.by_ecli.get(normalize_ecli(ecli) or "", set())))
        if itemid:
            selections.append(set(self.by_itemid.get(itemid, set())))
        if not selections:
            return []
        node_ids = set.intersection(*selections)
        return [self.candidates[node_id].model_copy(deep=True) for node_id in sorted(node_ids)]

    def exact(
        self, *, ecli: str | None = None, itemid: str | None = None
    ) -> CitationCandidate | None:
        candidates = self.exact_candidates(ecli=ecli, itemid=itemid)
        return candidates[0] if len(candidates) == 1 else None


def load_overrides(path: str | Path | None) -> list[CitationOverride]:
    if path is None:
        return []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [CitationOverride.model_validate(row) for row in csv.DictReader(handle)]


def _override_for(
    mention: CitationMention, overrides: list[CitationOverride]
) -> CitationOverride | None:
    applicable = [
        override for override in overrides if override.reference_hash == mention.reference_hash
    ]
    specific = [
        override
        for override in applicable
        if (override.source_ecli and override.source_ecli == mention.source_ecli)
        or (override.source_itemid and override.source_itemid == mention.source_itemid)
    ]
    reusable = [o for o in applicable if not o.source_ecli and not o.source_itemid]
    return specific[0] if specific else (reusable[0] if reusable else None)


def _authority_matches(
    mention: CitationMention,
    authority: CitationAuthority,
    *,
    exact_index: dict[str, list[CitationAuthorityEntry]] | None = None,
    reporter_index: dict[str, list[CitationAuthorityEntry]] | None = None,
    appno_index: dict[str, list[CitationAuthorityEntry]] | None = None,
) -> list[CitationAuthorityEntry]:
    exact = (
        exact_index.get(mention.normalized_ref, [])
        if exact_index is not None
        else [
            entry
            for entry in authority.entries
            if entry.normalized_citation == mention.normalized_ref
        ]
    )
    if exact:
        return exact
    if mention.explicit_appnos:
        indexed = appno_index or {
            appno: [entry for entry in authority.entries if appno in entry.appnos]
            for appno in mention.explicit_appnos
        }
        appnos = set(mention.explicit_appnos)
        candidates = {
            entry.entry_id: entry
            for appno in appnos
            for entry in indexed.get(appno, [])
            if appnos.issubset(entry.appnos)
        }
        compatible: list[CitationAuthorityEntry] = []
        for entry in candidates.values():
            if mention.cited_name and not _authority_title_matches(mention, entry):
                continue
            if mention.target_date and entry.date != mention.target_date:
                continue
            if (
                mention.document_kind != "unknown"
                and _authority_document_kind(entry) != mention.document_kind
            ):
                continue
            if (
                mention.procedural_phase != "unknown"
                and _authority_phase(entry) != mention.procedural_phase
            ):
                continue
            if mention.reporter and (
                entry.reporter is None or entry.reporter.key != mention.reporter.key
            ):
                continue
            if mention.grand_chamber and not entry.grand_chamber:
                continue
            compatible.append(entry)
        target_identities = {
            normalize_ecli(entry.target_ecli) or entry.target_itemid for entry in compatible
        } - {None}
        if len(target_identities) == 1:
            return compatible
    if not mention.reporter:
        return []
    reporter_matches = (
        reporter_index.get(mention.reporter.key, [])
        if reporter_index is not None
        else [
            entry
            for entry in authority.entries
            if entry.reporter and entry.reporter.key == mention.reporter.key
        ]
    )
    if not reporter_matches:
        reporter_matches = [
            entry
            for entry in authority.entries
            if entry.reporter and _reporter_authority_compatible(mention.reporter, entry.reporter)
        ]
    if len(reporter_matches) <= 1:
        if (
            reporter_matches
            and mention.cited_name
            and not _authority_title_matches(mention, reporter_matches[0])
        ):
            return []
        return reporter_matches
    narrowed = reporter_matches
    if mention.explicit_appnos:
        appnos = set(mention.explicit_appnos)
        by_appno = [entry for entry in narrowed if appnos & set(entry.appnos)]
        if by_appno:
            narrowed = by_appno
    if mention.target_date:
        dated = [entry for entry in narrowed if entry.date == mention.target_date]
        if dated:
            narrowed = dated
    title = normalize_docname(mention.cited_name)
    if title:
        titled = [entry for entry in narrowed if _authority_title_matches(mention, entry)]
        # Printed title evidence is a precision gate, not an optional
        # tie-breaker.  A unique date/reporter bucket can still contain a
        # completely different authority.
        if not titled:
            return []
        narrowed = titled
    # A reporter volume is not a document locator. If corroboration has not
    # selected one exact authority row, retain ambiguity instead of iterating
    # through unrelated cases in the same ECHR/Reports volume.
    return narrowed if len(narrowed) == 1 else []


def _reporter_authority_compatible(printed: Any, official: Any) -> bool:
    """Allow an official locator to complete, but never contradict, print."""
    if printed.family != official.family:
        return False
    for field in ("year", "volume", "number", "suffix", "page"):
        printed_value = getattr(printed, field)
        if printed_value is not None and printed_value != getattr(official, field):
            return False
    return not (printed.extracts and not official.extracts)


_PAREN_TITLE_QUALIFIER_RE = re.compile(r"\(([^)]*)\)")
_TITLE_NUMBER_MARKER_RE = re.compile(r"\bn(?:os?|°|º)\.?\s*\d+", re.I)
_UNPAREN_CASE_NUMBER_QUALIFIER_RE = re.compile(r"\bn(?:o|°|º)\.?\s*(\d+)(?![\d/])\s*(?=,|$)", re.I)
_TITLE_PHASE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "preliminary_objections",
        re.compile(r"preliminary objections|exceptions? pr[ée]liminaires?", re.I),
    ),
    ("article_50", re.compile(r"article\s*50", re.I)),
    (
        "just_satisfaction",
        re.compile(r"article\s*41|just satisfaction|satisfaction [ée]quitable", re.I),
    ),
    ("revision", re.compile(r"r[ée]vision", re.I)),
    ("interpretation", re.compile(r"interpr[ée]tation", re.I)),
    (
        "friendly_settlement",
        re.compile(r"friendly settlement|r[èe]glement amiable", re.I),
    ),
    ("striking_out", re.compile(r"striking out|radiation", re.I)),
    ("admissibility", re.compile(r"\bdec\.?\b|decision|d[ée]cision|recevabil", re.I)),
    ("merits", re.compile(r"\bmerits\b|\bfond\b", re.I)),
)

_REFERENCE_TITLE_BOUNDARY_RE = re.compile(
    r",\s*(?=(?:n(?:o|os|°|º)\.?\s*\d+\s*/\s*\d+|"
    r"\d{1,2}(?:er)?\s+[A-Za-zÀ-ÖØ-öø-ÿ]+\s+\d{4}|"
    r"judgment\b|decision\b|arr[êe]t\b|d[ée]cision\b|"
    r"Series\s+A|Reports\b|ECHR\b|CEDH\b|D\.?\s*R\.?\b|"
    r"Decisions and Reports|Recueil\b|§))|"
    r"\s+(?=(?:judgment|decision|report)\s+of\b|arr[êe]t\s+du\b)",
    re.I,
)


def _reference_title_segment(value: str | None) -> str:
    text = (value or "").strip()
    match = _REFERENCE_TITLE_BOUNDARY_RE.search(text)
    return text[: match.start()].rstrip(" ,") if match else text


def _explicit_title_phase(value: str | None) -> str | None:
    phases = _explicit_title_phases(value)
    return next((phase for phase, _pattern in _TITLE_PHASE_PATTERNS if phase in phases), None)


def _explicit_title_phases(value: str | None) -> set[str]:
    """Return every printed procedural qualifier, retaining compound suffixes."""
    text = value or ""
    return {
        _canonical_title_phase(phase)
        for phase, pattern in _TITLE_PHASE_PATTERNS
        if pattern.search(text)
    }


def _canonical_title_phase(phase: str) -> str:
    if phase in {"article_50", "just_satisfaction"}:
        return "just_satisfaction"
    if phase in {"admissibility", "commission_decision"}:
        return "admissibility"
    return phase


def _title_case_numbers(value: str | None) -> set[str]:
    """Extract every case-series ordinal, including joined plural forms."""
    text = value or ""
    numbers = set(_UNPAREN_CASE_NUMBER_QUALIFIER_RE.findall(text))
    for content in _PAREN_TITLE_QUALIFIER_RE.findall(text):
        # Application numbers are not case-title ordinals.
        if "/" in content or not _TITLE_NUMBER_MARKER_RE.search(content):
            continue
        numbers.update(re.findall(r"\d+", content))
    return numbers


def _title_qualifiers_compatible(
    source: str | None,
    target: str | None,
    *,
    source_phase: str = "unknown",
    target_phase: str = "unknown",
) -> bool:
    source_numbers = _title_case_numbers(source)
    target_numbers = _title_case_numbers(target)
    if (source_numbers or target_numbers) and source_numbers != target_numbers:
        return False
    source_explicit_phases = _explicit_title_phases(source)
    target_explicit_phases = _explicit_title_phases(target)
    if source_explicit_phases or target_explicit_phases:
        effective_source = source_explicit_phases or (
            {_canonical_title_phase(source_phase)} if source_phase != "unknown" else set()
        )
        effective_target = target_explicit_phases or (
            {_canonical_title_phase(target_phase)} if target_phase != "unknown" else set()
        )
        return bool(effective_source) and effective_source == effective_target
    return not (
        source_phase != "unknown" and target_phase != "unknown" and source_phase != target_phase
    )


def _title_similarity(mention: CitationMention, candidate: CitationCandidate) -> float:
    source = normalize_docname(mention.cited_name)
    aliases = candidate.title_aliases
    if not aliases and candidate.docname:
        aliases = [candidate.docname]
    compatible_aliases = [
        title
        for title in aliases
        if title
        and _title_qualifiers_compatible(
            _reference_title_segment(mention.raw_ref),
            title,
            source_phase=mention.procedural_phase,
            target_phase=candidate.procedural_phase,
        )
    ]
    targets = [normalize_docname(title) for title in compatible_aliases]
    targets.extend(
        normalize_docname(base)
        for title in compatible_aliases
        if title and (base := extract_reference_name(title))
    )
    return max(
        (SequenceMatcher(None, source, target).ratio() for target in targets if source and target),
        default=0.0,
    )


def _authority_title_matches(mention: CitationMention, entry: CitationAuthorityEntry) -> bool:
    source = normalize_docname(mention.cited_name)
    target = entry.normalized_title or normalize_docname(entry.title)
    if not source or not target:
        return not source
    if not _title_qualifiers_compatible(
        _reference_title_segment(mention.raw_ref),
        _reference_title_segment(entry.citation),
        source_phase=mention.procedural_phase,
        target_phase=_authority_phase(entry),
    ):
        return False
    return source == target or SequenceMatcher(None, source, target).ratio() >= 0.82


def _entry_candidate_title_matches(
    entry: CitationAuthorityEntry, candidate: CitationCandidate
) -> bool:
    source = entry.normalized_title or normalize_docname(entry.title)
    aliases = list(candidate.title_aliases)
    if not aliases and candidate.docname:
        aliases = [candidate.docname]
    targets = [
        normalize_docname(value)
        for value in aliases
        if value
        and _title_qualifiers_compatible(
            _reference_title_segment(entry.citation),
            value,
            source_phase=_authority_phase(entry),
            target_phase=candidate.procedural_phase,
        )
    ]
    return bool(
        source
        and any(
            source == target or SequenceMatcher(None, source, target).ratio() >= 0.82
            for target in targets
            if target
        )
    )


def _document_kind_matches(mention: CitationMention, candidate: CitationCandidate) -> bool:
    if candidate.document_kind == mention.document_kind:
        return True
    # Historical SCL commonly prints only “(dec.)”. HUDOC correctly classifies
    # the target as a Commission document; these are compatible descriptions of
    # the same decision, not contradictory procedural identities.
    return (
        mention.document_kind == "decision"
        and candidate.document_kind == "commission"
        and candidate.procedural_phase == "commission_decision"
    )


def _procedural_phase_matches(mention: CitationMention, candidate: CitationCandidate) -> bool:
    if candidate.procedural_phase == mention.procedural_phase:
        return True
    return (
        mention.procedural_phase == "admissibility"
        and candidate.procedural_phase == "commission_decision"
    )


def _authority_document_kind(entry: CitationAuthorityEntry) -> str:
    if entry.document_kind != "unknown":
        return entry.document_kind
    if entry.reporter and entry.reporter.family in {"series_a", "reports", "echr"}:
        return "judgment"
    return "unknown"


def _authority_phase(entry: CitationAuthorityEntry) -> str:
    if entry.procedural_phase != "unknown":
        return entry.procedural_phase
    return "merits" if _authority_document_kind(entry) == "judgment" else "unknown"


def _candidate_from_authority(entry: CitationAuthorityEntry) -> CitationCandidate:
    """Materialize a checked official target when HUDOC metadata is unavailable."""
    ecli = normalize_ecli(entry.target_ecli)
    itemid = entry.target_itemid
    node_id = f"ecli:{ecli}" if ecli else f"itemid:{itemid}"
    ecli_metadata = _authority_ecli_metadata(entry)
    encoded_date = ecli_metadata[0] if ecli_metadata else None
    encoded_kind = ecli_metadata[1] if ecli_metadata else None
    authority_kind = _authority_document_kind(entry)
    document_kind = (
        authority_kind
        if authority_kind != "unknown"
        else {
            "JUD": "judgment",
            "DEC": "decision",
            "ADV": "advisory",
            "REP": "commission",
        }.get(encoded_kind or "", "unknown")
    )
    authority_phase = _authority_phase(entry)
    procedural_phase = (
        authority_phase
        if authority_phase != "unknown"
        else {
            "JUD": "merits",
            "DEC": "admissibility",
            "ADV": "advisory_opinion",
            "REP": "commission_report",
        }.get(encoded_kind or "", "unknown")
    )
    return CitationCandidate(
        node_id=node_id,
        itemid=itemid,
        ecli=ecli,
        docname=entry.target_docname or entry.title,
        # When the derived authority records a HUDOC target title, evaluate
        # against that title.  Keeping the source citation title as a second
        # alias would mask a corrupt or mistranscribed target_docname.
        title_aliases=[value for value in [entry.target_docname or entry.title] if value],
        reporter_keys=[entry.reporter.key] if entry.reporter else [],
        casecitations=[entry.citation],
        appnos=list(entry.appnos),
        date=entry.date or encoded_date,
        language=entry.language,
        document_kind=document_kind,
        procedural_phase=procedural_phase,  # type: ignore[arg-type]
        grand_chamber=entry.grand_chamber,
        is_placeholder=False,
        hudoc_url=(f"https://hudoc.echr.coe.int/?i={itemid}" if itemid else None),
    )


_ECLI_TARGET_RE = re.compile(
    r"^ECLI:CE:ECHR:(?P<year>\d{4}):(?P<month>\d{2})(?P<day>\d{2})"
    r"(?P<kind>JUD|DEC|ADV|REP)(?P<application>\d+)$"
)


def _target_ecli_metadata(value: str | None) -> tuple[date, str, str] | None:
    ecli = normalize_ecli(value)
    match = _ECLI_TARGET_RE.fullmatch(ecli or "")
    if match is None:
        return None
    try:
        encoded_date = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None
    return encoded_date, match.group("kind"), match.group("application")


def _authority_ecli_metadata(
    entry: CitationAuthorityEntry,
) -> tuple[date, str, str] | None:
    return _target_ecli_metadata(entry.target_ecli)


def _hydrate_candidate_from_ecli(candidate: CitationCandidate) -> CitationCandidate:
    metadata = _target_ecli_metadata(candidate.ecli)
    if metadata is None:
        return candidate
    encoded_date, encoded_kind, payload = metadata
    decoded_appno = f"{int(payload[:-2] or '0')}/{payload[-2:]}"
    return candidate.model_copy(
        deep=True,
        update={
            "date": candidate.date or encoded_date,
            "appnos": candidate.appnos or [decoded_appno],
            "document_kind": (
                candidate.document_kind
                if candidate.document_kind != "unknown"
                else {
                    "JUD": "judgment",
                    "DEC": "decision",
                    "ADV": "advisory",
                    "REP": "commission",
                }[encoded_kind]
            ),
            "procedural_phase": (
                candidate.procedural_phase
                if candidate.procedural_phase != "unknown"
                else {
                    "JUD": "merits",
                    "DEC": "admissibility",
                    "ADV": "advisory_opinion",
                    "REP": "commission_report",
                }[encoded_kind]
            ),
        },
    )


def _candidate_ecli_conflicts(candidate: CitationCandidate) -> list[str]:
    metadata = _target_ecli_metadata(candidate.ecli)
    if metadata is None:
        return ["candidate ECLI has invalid target metadata"]
    encoded_date, encoded_kind, payload = metadata
    conflicts: list[str] = []
    if candidate.date and candidate.date != encoded_date:
        conflicts.append("candidate date conflicts with ECLI")
    if candidate.appnos:
        padded = _ecli_appno_payloads(candidate.appnos, width=len(payload))
        if payload not in padded:
            conflicts.append("candidate application number conflicts with ECLI")
    compatible_kinds = {
        "JUD": {"judgment", "unknown"},
        "DEC": {"decision", "commission", "unknown"},
        "ADV": {"advisory", "unknown"},
        "REP": {"commission", "unknown"},
    }
    if candidate.document_kind not in compatible_kinds[encoded_kind]:
        conflicts.append("candidate document kind conflicts with ECLI")
    compatible_phases = {
        "JUD": {
            "merits",
            "article_50",
            "just_satisfaction",
            "revision",
            "interpretation",
            "friendly_settlement",
            "striking_out",
            "unknown",
        },
        "DEC": {"admissibility", "commission_decision", "unknown"},
        "ADV": {"advisory_opinion", "unknown"},
        "REP": {"commission_report", "unknown"},
    }
    if candidate.procedural_phase not in compatible_phases[encoded_kind]:
        conflicts.append("candidate procedural phase conflicts with ECLI")
    return conflicts


def _ecli_appno_payloads(values: list[str], *, width: int) -> set[str]:
    """Decode appnos even when a Parquet reader stringifies a list-valued cell."""
    payloads: set[str] = set()
    for value in values:
        for left, right in re.findall(r"(?<!\d)(\d{1,8})\s*/\s*(\d{2,4})(?!\d)", value):
            payloads.add(f"{left}{right}".zfill(width))
    return payloads


def _authority_identifier_conflicts(
    entry: CitationAuthorityEntry, *, source_date: date | None = None
) -> list[str]:
    """Validate semantic metadata encoded by a derived authority target ECLI."""
    ecli = normalize_ecli(entry.target_ecli)
    if not ecli:
        return []
    metadata = _authority_ecli_metadata(entry)
    if metadata is None:
        return ["invalid authority target ECLI"]
    encoded_date, encoded_kind, encoded_application = metadata
    conflicts: list[str] = []
    if entry.date and entry.date != encoded_date and entry.entry_id != "temeltasch-dr-31-130":
        conflicts.append("authority target ECLI date conflicts with authority date")
    if source_date and encoded_date > source_date:
        conflicts.append("authority target ECLI date is after source document")
    if entry.appnos:
        padded_appnos = _ecli_appno_payloads(entry.appnos, width=len(encoded_application))
        if encoded_application not in padded_appnos:
            conflicts.append("authority target ECLI application conflicts with authority appno")
    authority_kind = _authority_document_kind(entry)
    kind_compatible = (
        authority_kind == "unknown"
        or (encoded_kind == "JUD" and authority_kind == "judgment")
        or (encoded_kind == "DEC" and authority_kind in {"decision", "commission"})
        or (encoded_kind == "ADV" and authority_kind == "advisory")
        or (encoded_kind == "REP" and authority_kind == "commission")
    )
    if not kind_compatible:
        conflicts.append("authority target ECLI kind conflicts with authority kind")
    authority_phase = _authority_phase(entry)
    phase_compatible = (
        authority_phase == "unknown"
        or (
            encoded_kind == "JUD"
            and authority_phase
            in {
                "merits",
                "article_50",
                "just_satisfaction",
                "revision",
                "interpretation",
                "friendly_settlement",
                "striking_out",
            }
        )
        or (encoded_kind == "DEC" and authority_phase in {"admissibility", "commission_decision"})
        or (encoded_kind == "ADV" and authority_phase == "advisory_opinion")
        or (encoded_kind == "REP" and authority_phase == "commission_report")
    )
    if not phase_compatible:
        conflicts.append("authority target ECLI kind conflicts with authority phase")
    return conflicts


def _historical_identifier_conflicts(
    entry: HistoricalCatalogEntry, *, source_date: date | None = None
) -> list[str]:
    """Validate a historical row against metadata encoded by its target ECLI."""
    ecli = normalize_ecli(entry.target_ecli)
    if not ecli:
        return []
    metadata = _target_ecli_metadata(ecli)
    if metadata is None:
        return ["invalid historical target ECLI"]
    encoded_date, encoded_kind, encoded_application = metadata
    conflicts: list[str] = []
    reviewed_temeltasch_date = (
        entry.reporter_key == "DR::31:::130"
        and entry.normalized_title == "TEMELTASCH SWITZERLAND"
        and entry.appnos == ["9116/80"]
        and ecli == "ECLI:CE:ECHR:1983:0305REP000911680"
    )
    if entry.date and entry.date != encoded_date and not reviewed_temeltasch_date:
        conflicts.append("historical target ECLI date conflicts with catalog date")
    if source_date and encoded_date > source_date:
        conflicts.append("historical target ECLI date is after source document")
    if entry.appnos:
        padded_appnos = _ecli_appno_payloads(entry.appnos, width=len(encoded_application))
        if encoded_application not in padded_appnos:
            conflicts.append("historical target ECLI application conflicts with catalog appno")
    kind_compatible = (
        entry.document_kind == "unknown"
        or (encoded_kind == "JUD" and entry.document_kind == "judgment")
        or (encoded_kind == "DEC" and entry.document_kind in {"decision", "commission"})
        or (encoded_kind == "ADV" and entry.document_kind == "advisory")
        or (encoded_kind == "REP" and entry.document_kind == "commission")
    )
    if not kind_compatible:
        conflicts.append("historical target ECLI kind conflicts with catalog kind")
    return conflicts


def _has_printed_document_selector(mention: CitationMention) -> bool:
    """Return whether the source itself selects a procedural document."""
    reporter_selector = bool(
        mention.reporter
        and (
            (
                mention.reporter.family in {"echr", "reports"}
                and mention.reporter.year
                and mention.reporter.volume
            )
            or (mention.reporter.family == "series_a" and mention.reporter.number)
            or (
                mention.reporter.family in {"dr", "commission_collection", "commission_report"}
                and mention.reporter.volume
                and mention.reporter.page
            )
        )
    )
    return bool(
        mention.target_date
        or mention.target_year
        or reporter_selector
        or mention.document_kind != "unknown"
        or mention.procedural_phase != "unknown"
        or mention.grand_chamber
    )


def _evaluate_candidate(
    mention: CitationMention,
    candidate: CitationCandidate,
    *,
    authority_entry: CitationAuthorityEntry | None = None,
) -> CitationCandidate | None:
    candidate = candidate.model_copy(deep=True)
    if candidate.ecli and (identifier_conflicts := _candidate_ecli_conflicts(candidate)):
        candidate.conflicting_evidence.extend(identifier_conflicts)
        candidate.conflicting_evidence.append("candidate identifier metadata conflict")
    if mention.source_date and candidate.date and candidate.date > mention.source_date:
        candidate.conflicting_evidence.append("target date is after source document")
        return candidate
    if candidate.is_placeholder:
        candidate.conflicting_evidence.append("placeholder HUDOC record")
        return candidate
    if (mention.source_ecli and candidate.ecli == normalize_ecli(mention.source_ecli)) or (
        mention.source_itemid and candidate.itemid == mention.source_itemid
    ):
        candidate.conflicting_evidence.append("exact source document self-edge")
        return candidate

    candidate.title_similarity = _title_similarity(mention, candidate)
    if (
        not mention.source_ecli
        and mention.source_itemid
        and mention.source_appnos
        and bool(set(mention.source_appnos).intersection(candidate.appnos))
        and mention.source_date
        and candidate.date == mention.source_date
        and candidate.title_similarity >= 0.97
        and (mention.document_kind == "unknown" or candidate.document_kind == mention.document_kind)
        and (
            mention.procedural_phase == "unknown"
            or candidate.procedural_phase == mention.procedural_phase
        )
    ):
        candidate.conflicting_evidence.append("probable source document language sibling")
    if mention.explicit_appnos and set(mention.explicit_appnos).issubset(candidate.appnos):
        candidate.positive_evidence.append("explicit application number")
    elif mention.explicit_appnos:
        candidate.conflicting_evidence.append("different application number")
    if mention.advisory_request_id:
        if (
            candidate.advisory_request_id
            and candidate.advisory_request_id.upper() == mention.advisory_request_id.upper()
        ):
            candidate.positive_evidence.append("advisory request identifier")
        elif candidate.advisory_request_id:
            candidate.conflicting_evidence.append("different advisory request identifier")
    if mention.target_date:
        if candidate.date == mention.target_date:
            candidate.positive_evidence.append("exact date")
        else:
            candidate.conflicting_evidence.append("different date")
    if mention.reporter:
        if (
            mention.reporter.key in candidate.reporter_keys
            or publication_reporter_key(mention.reporter) in candidate.published_reporter_keys
        ):
            candidate.positive_evidence.append("exact reporter locator")
        else:
            candidate.conflicting_evidence.append("different reporter locator")
        if (
            mention.reporter.year
            and candidate.date
            and mention.reporter.year == candidate.date.year
        ):
            candidate.positive_evidence.append("reporter publication year")
    elif mention.target_year:
        if candidate.date:
            partial_matches = candidate.date.year == mention.target_year
            if mention.target_month is not None:
                partial_matches = partial_matches and candidate.date.month == mention.target_month
            if partial_matches:
                candidate.positive_evidence.append("partial date")
            else:
                candidate.conflicting_evidence.append("different date")
        else:
            candidate.conflicting_evidence.append("different date")
    if mention.document_kind != "unknown":
        if _document_kind_matches(mention, candidate):
            candidate.positive_evidence.append("document kind")
        else:
            candidate.conflicting_evidence.append("different document kind")
    if mention.procedural_phase != "unknown":
        if _procedural_phase_matches(mention, candidate):
            candidate.positive_evidence.append("procedural phase")
        else:
            candidate.conflicting_evidence.append("different procedural phase")
    if mention.grand_chamber:
        if candidate.grand_chamber:
            candidate.positive_evidence.append("Grand Chamber")
        elif "HUDOC formation Chamber" in candidate.positive_evidence:
            candidate.conflicting_evidence.append("not Grand Chamber")
        else:
            candidate.conflicting_evidence.append("missing Grand Chamber metadata")
    if candidate.title_similarity >= 0.97:
        candidate.positive_evidence.append("exact normalized title")
    elif candidate.title_similarity >= 0.82:
        candidate.positive_evidence.append("similar title")
    elif mention.cited_name:
        candidate.conflicting_evidence.append("different title")
    if authority_entry:
        if authority_entry.target_ecli:
            if normalize_ecli(authority_entry.target_ecli) == candidate.ecli:
                candidate.positive_evidence.append("authority ECLI")
            else:
                candidate.conflicting_evidence.append("different authority ECLI")
        if authority_entry.target_itemid:
            if authority_entry.target_itemid == candidate.itemid:
                candidate.positive_evidence.append("authority itemid")
            elif (
                authority_entry.target_ecli
                and normalize_ecli(authority_entry.target_ecli) == candidate.ecli
            ):
                # Item IDs are language-version IDs.  A canonical ECLI node
                # may legitimately display its other official language row.
                candidate.positive_evidence.append("authority itemid language sibling")
            else:
                candidate.conflicting_evidence.append("different authority itemid")
        if authority_entry.appnos:
            if set(authority_entry.appnos).issubset(candidate.appnos):
                candidate.positive_evidence.append("authority application number")
            else:
                candidate.conflicting_evidence.append("different authority application number")
        if authority_entry.date:
            if authority_entry.date == candidate.date:
                candidate.positive_evidence.append("authority date")
            else:
                candidate.conflicting_evidence.append("different authority date")
        authority_kind = _authority_document_kind(authority_entry)
        if authority_kind != "unknown":
            if candidate.document_kind == authority_kind:
                candidate.positive_evidence.append("authority document kind")
            else:
                candidate.conflicting_evidence.append("different authority document kind")
        authority_phase = _authority_phase(authority_entry)
        if authority_phase != "unknown":
            if candidate.procedural_phase == authority_phase:
                candidate.positive_evidence.append("authority procedural phase")
            else:
                candidate.conflicting_evidence.append("different authority procedural phase")
        if authority_entry.reporter:
            if (
                authority_entry.reporter.key in candidate.reporter_keys
                or publication_reporter_key(authority_entry.reporter)
                in candidate.published_reporter_keys
            ):
                candidate.positive_evidence.append("authority reporter locator")
            elif candidate.reporter_keys:
                candidate.conflicting_evidence.append("different authority reporter locator")
        if (
            authority_entry.reporter
            and authority_entry.reporter.year
            and candidate.date
            and authority_entry.reporter.year == candidate.date.year
        ):
            candidate.positive_evidence.append("authority reporter publication year")
        if _entry_candidate_title_matches(authority_entry, candidate):
            candidate.positive_evidence.append("authority title")
        else:
            candidate.conflicting_evidence.append("different authority title")
        if authority_entry.grand_chamber:
            candidate.positive_evidence.append("authority Grand Chamber")
            if "HUDOC formation Chamber" in candidate.positive_evidence:
                candidate.conflicting_evidence.append("different authority Grand Chamber")
    return candidate


def _metadata_accepts(mention: CitationMention, candidate: CitationCandidate) -> bool:
    evidence = set(candidate.positive_evidence)
    conflicts = set(candidate.conflicting_evidence)
    fatal = {
        "target date is after source document",
        "exact source document self-edge",
        "placeholder HUDOC record",
        "different date",
        "different document kind",
        "different procedural phase",
        "different reporter locator",
        "not Grand Chamber",
        "missing Grand Chamber metadata",
        "probable source document language sibling",
        "candidate identifier metadata conflict",
    }
    if fatal & conflicts or (mention.cited_name and "different title" in conflicts):
        return False
    if mention.explicit_appnos and "explicit application number" not in evidence:
        return False
    if mention.advisory_request_id:
        return (
            "advisory request identifier" in evidence
            and bool(
                {"exact date", "document kind", "procedural phase", "Grand Chamber"} & evidence
            )
            and "different advisory request identifier" not in conflicts
        )
    corroborated = bool(
        {
            "exact date",
            "partial date",
            "document kind",
            "procedural phase",
            "Grand Chamber",
            "exact reporter locator",
            "reporter publication year",
        }
        & evidence
    )
    if "explicit application number" in evidence:
        return corroborated
    return "exact date" in evidence and (
        "exact normalized title" in evidence or "similar title" in evidence
    )


def _historical_checked_candidate(
    mention: CitationMention,
    entry: HistoricalCatalogEntry,
    candidate: CitationCandidate,
) -> CitationCandidate | None:
    checked = candidate.model_copy(deep=True)
    if (
        entry.reporter_key == "DR::31:::130"
        and entry.normalized_title == "TEMELTASCH SWITZERLAND"
        and entry.appnos == ["9116/80"]
        and normalize_ecli(entry.target_ecli) == "ECLI:CE:ECHR:1983:0305REP000911680"
    ):
        checked.conflicting_evidence = [
            value for value in checked.conflicting_evidence if value != "different date"
        ]
    if (
        mention.reporter
        and entry.reporter_key == mention.reporter.key
        and not checked.reporter_keys
    ):
        checked.conflicting_evidence = [
            value for value in checked.conflicting_evidence if value != "different reporter locator"
        ]
        checked.positive_evidence.append("exact reporter locator")
    historical_phase = _explicit_title_phase(entry.title)
    if historical_phase and historical_phase == mention.procedural_phase:
        checked.conflicting_evidence = [
            value for value in checked.conflicting_evidence if value != "different procedural phase"
        ]
        checked.positive_evidence.append("procedural phase")
        checked.procedural_phase = historical_phase  # type: ignore[assignment]
    return checked if _metadata_accepts(mention, checked) else None


def _authority_accepts(
    mention: CitationMention,
    entry: CitationAuthorityEntry,
    candidate: CitationCandidate,
) -> bool:
    evidence = set(candidate.positive_evidence)
    conflicts = set(candidate.conflicting_evidence)
    if not _has_printed_document_selector(mention):
        # An authority row can identify the application behind a printed name
        # or application number.  Its unprinted date/kind/phase cannot select
        # one procedural document on the source's behalf.
        return False
    if mention.explicit_appnos and "explicit application number" not in evidence:
        return False
    fatal = {
        "different date",
        "different document kind",
        "different procedural phase",
        "different reporter locator",
        "not Grand Chamber",
        "target date is after source document",
        "exact source document self-edge",
        "placeholder HUDOC record",
        "different authority ECLI",
        "different authority itemid",
        "different authority application number",
        "different authority date",
        "different authority document kind",
        "different authority procedural phase",
        "different authority reporter locator",
        "different authority Grand Chamber",
        "different authority title",
        "probable source document language sibling",
        "candidate identifier metadata conflict",
    }
    if (
        mention.reporter
        and entry.reporter
        and _reporter_authority_compatible(mention.reporter, entry.reporter)
        and not candidate.reporter_keys
        and (
            (entry.target_ecli and normalize_ecli(entry.target_ecli) == candidate.ecli)
            or (entry.target_itemid and entry.target_itemid == candidate.itemid)
            or {
                "authority title",
                "authority date",
                "authority document kind",
                "authority procedural phase",
            }.issubset(evidence)
            or {
                "explicit application number",
                "authority application number",
                "authority title",
                "authority document kind",
                "authority procedural phase",
                "reporter publication year",
                "authority reporter publication year",
            }.issubset(evidence)
        )
    ):
        # HUDOC metadata often omits casecitation even though the official
        # citation authority supplies the exact reporter locator and direct
        # target identity. Missing candidate metadata is not agreement, but
        # the official row can corroborate it; a different nonempty candidate
        # reporter remains fatal.
        fatal.discard("different reporter locator")
    if (
        entry.entry_id == "temeltasch-dr-31-130"
        and normalize_ecli(entry.target_ecli) == candidate.ecli
        and set(entry.appnos).issubset(candidate.appnos)
        and entry.reporter
        and mention.reporter
        and entry.reporter.key == mention.reporter.key
        and _entry_candidate_title_matches(entry, candidate)
        and candidate.document_kind == "commission"
        and candidate.procedural_phase == "commission_report"
    ):
        # Reviewed exception: the Commission report itself is dated 5 May
        # 1982, while HUDOC indexes its PDF as 5 March 1983.  The official DR
        # 31 p. 130 concordance, appno, title, phase and exact ECLI all agree.
        fatal -= {"different date", "different authority date"}
    if fatal & conflicts:
        return False
    if "missing Grand Chamber metadata" in conflicts and "authority Grand Chamber" not in evidence:
        return False
    if mention.cited_name and "different title" in conflicts:
        return False
    if entry.target_ecli or entry.target_itemid:
        if not ({"authority ECLI", "authority itemid"} & evidence):
            return False
        # A direct identifier in an authority row identifies a candidate, but
        # never bypasses the row's or printed mention's compatibility gates.
        return bool(
            {"authority title", "authority application number", "authority date"} & evidence
        )
    identity = {
        "authority date",
        "authority reporter locator",
        "authority reporter publication year",
    } & evidence
    if "authority reporter publication year" in identity:
        identity.update({"authority document kind", "authority procedural phase"} & evidence)
        # A reporter year/volume can contain several procedural documents. It
        # becomes document-level evidence only with the authority's document
        # kind or phase, not merely because both candidates were published that year.
        identity.discard("authority reporter publication year")
    return bool({"authority title", "authority similar title"} & evidence) and bool(identity)


async def _fetch_online_candidates(
    mentions: list[CitationMention],
    client: Any,
    *,
    authority: CitationAuthority,
    cache_path: str | Path | None,
    source_cases: Iterable[Case] = (),
) -> tuple[list[Case], int]:
    authority_exact_index: dict[str, list[CitationAuthorityEntry]] = defaultdict(list)
    authority_reporter_index: dict[str, list[CitationAuthorityEntry]] = defaultdict(list)
    authority_appno_index: dict[str, list[CitationAuthorityEntry]] = defaultdict(list)
    for entry in authority.entries:
        authority_exact_index[entry.normalized_citation].append(entry)
        for appno in entry.appnos:
            authority_appno_index[appno].append(entry)
        if entry.reporter:
            authority_reporter_index[entry.reporter.key].append(entry)

    def authority_matches(mention: CitationMention) -> list[CitationAuthorityEntry]:
        return _authority_matches(
            mention,
            authority,
            exact_index=authority_exact_index,
            reporter_index=authority_reporter_index,
            appno_index=authority_appno_index,
        )

    cached_cases = _load_cached_cases(cache_path)
    cached_appnos = {appno for case in cached_cases for appno in case.appno}
    cached_eclis = {
        normalize_ecli(case.ecli) for case in cached_cases if case.ecli and not case.is_placeholder
    }
    cached_itemids = {case.itemid for case in cached_cases if case.itemid}
    cached_advisory_ids = {
        case.advop_identifier.upper() for case in cached_cases if case.advop_identifier
    }
    wanted = sorted(
        {
            appno
            for mention in mentions
            for appno in (*mention.explicit_appnos, *mention.scl_appno_candidates)
            if appno not in cached_appnos
        }
    )
    fetched: list[Case] = []
    lookup_errors = 0
    matched_authority = [entry for mention in mentions for entry in authority_matches(mention)]
    identifier_queries = (
        (
            "ecli",
            sorted(
                {
                    mention.explicit_ecli
                    for mention in mentions
                    if mention.explicit_ecli and mention.explicit_ecli not in cached_eclis
                }
                | {
                    normalized
                    for entry in matched_authority
                    if entry.target_ecli
                    and (normalized := normalize_ecli(entry.target_ecli))
                    and normalized not in cached_eclis
                }
                | {
                    normalized
                    for case in source_cases
                    if case.is_placeholder
                    and (normalized := normalize_ecli(case.ecli))
                    and normalized not in cached_eclis
                }
            ),
        ),
        (
            "itemid",
            sorted(
                {
                    mention.explicit_itemid
                    for mention in mentions
                    if mention.explicit_itemid and mention.explicit_itemid not in cached_itemids
                }
                | {
                    entry.target_itemid
                    for entry in matched_authority
                    if entry.target_itemid and entry.target_itemid not in cached_itemids
                }
            ),
        ),
        (
            "advop_identifier",
            sorted(
                {
                    mention.advisory_request_id
                    for mention in mentions
                    if mention.advisory_request_id
                    and mention.advisory_request_id.upper() not in cached_advisory_ids
                }
            ),
        ),
    )
    for kind, values in identifier_queries:
        for index in range(0, len(values), 20):
            chunk = values[index : index + 20]
            try:
                rows = await client.search(
                    **{kind: chunk}, doctypes=(), languages=("ENG", "FRE"), limit=None
                )
            except Exception as exc:  # noqa: BLE001 - network failure becomes audit data
                lookup_errors += 1
                if cache_path:
                    append_jsonl(
                        cache_path,
                        {
                            "lookup": {"kind": kind, "value": chunk},
                            "status": "error",
                            "error": str(exc),
                        },
                    )
                continue
            for row in rows:
                case = Case.model_validate(row)
                fetched.append(case)
                if cache_path:
                    append_jsonl(cache_path, {"case": case.model_dump(mode="json")})
    for index in range(0, len(wanted), 20):
        chunk = wanted[index : index + 20]
        try:
            rows = await client.search(
                appno=chunk,
                doctypes=(),
                languages=("ENG", "FRE"),
                limit=None,
            )
        except Exception as exc:  # noqa: BLE001 - network failure becomes audit data
            lookup_errors += 1
            if cache_path:
                append_jsonl(
                    cache_path,
                    {
                        "lookup": {"kind": "appno", "value": chunk},
                        "status": "error",
                        "error": str(exc),
                    },
                )
            continue
        for row in rows:
            case = Case.model_validate(row)
            fetched.append(case)
            if cache_path:
                append_jsonl(cache_path, {"case": case.model_dump(mode="json")})

    # Title discovery includes titles supplied by a unique exact-authority
    # match. This is what makes a reporter-only mention such as "Series A
    # no. 58" discoverable without pretending the reporter volume is an
    # application number. Discovery never independently accepts a target.
    known_cases = [case for case in (*cached_cases, *fetched) if _is_eligible_target(case)]
    known_titles: set[str] = set()
    known_title_dates: set[tuple[str, Any]] = set()
    known_title_years: set[tuple[str, int]] = set()

    def remember_title(case: Case) -> None:
        normalized_title = normalize_docname(case.docname)
        if not normalized_title:
            return
        known_titles.add(normalized_title)
        if case.kp_date is not None:
            known_title_dates.add((normalized_title, case.kp_date))
            known_title_years.add((normalized_title, case.kp_date.year))

    for known_case in known_cases:
        remember_title(known_case)
    search_requests: list[tuple[str, Any, int | None]] = []
    for mention in mentions:
        if mention.cited_name:
            search_requests.append((mention.cited_name, mention.target_date, mention.target_year))
        matched = authority_matches(mention)
        if len(matched) == 1 and matched[0].title:
            search_requests.append(
                (
                    matched[0].title,
                    matched[0].date or mention.target_date,
                    mention.target_year,
                )
            )
    pending_title_requests: list[tuple[str, Any, int | None]] = []
    for search_name, expected_date, expected_year in dict.fromkeys(search_requests):
        normalized = normalize_docname(search_name)
        if not normalized:
            continue
        already_known = (
            (normalized, expected_date) in known_title_dates
            if expected_date is not None
            else (normalized, expected_year) in known_title_years
            if expected_year is not None
            else normalized in known_titles
        )
        if already_known:
            continue
        pending_title_requests.append((search_name, expected_date, expected_year))

    semaphore = asyncio.Semaphore(8)

    async def fetch_title(request: tuple[str, Any, int | None]) -> Any:
        search_name, expected_date, expected_year = request
        try:
            if expected_date is not None:
                date_filters = {
                    "date_from": expected_date.isoformat(),
                    "date_to": expected_date.isoformat(),
                }
            elif expected_year is not None:
                date_filters = {
                    "date_from": f"{expected_year:04d}-01-01",
                    "date_to": f"{expected_year:04d}-12-31",
                }
            else:
                date_filters = {}
            async with semaphore:
                rows = await client.search(
                    docname=search_name,
                    doctypes=(),
                    languages=("ENG", "FRE"),
                    limit=50,
                    **date_filters,
                )
            return rows
        except Exception as exc:  # noqa: BLE001 - network failure becomes audit data
            return exc

    title_results = await asyncio.gather(
        *(fetch_title(request) for request in pending_title_requests)
    )
    for (search_name, expected_date, expected_year), result in zip(
        pending_title_requests, title_results, strict=True
    ):
        if isinstance(result, Exception):
            lookup_errors += 1
            if cache_path:
                append_jsonl(
                    cache_path,
                    {
                        "lookup": {
                            "kind": "title",
                            "value": search_name,
                            "date": str(expected_date) if expected_date else None,
                            "year": expected_year,
                        },
                        "status": "error",
                        "error": str(result),
                    },
                )
            continue
        for row in result:
            case = Case.model_validate(row)
            fetched.append(case)
            if _is_eligible_target(case):
                known_cases.append(case)
                remember_title(case)
            if cache_path:
                append_jsonl(cache_path, {"case": case.model_dump(mode="json")})

    # App-number and title searches can initially return only an English or
    # French placeholder. Resolve the shared ECLI once more after discovery so
    # the canonical node can use the substantive language sibling. This keeps
    # placeholders out of measurement-grade graphs without discarding an exact
    # target merely because one language record has no document body.
    discovered = [*cached_cases, *fetched]
    substantive_eclis = {
        normalized
        for case in discovered
        if not case.is_placeholder and (normalized := normalize_ecli(case.ecli))
    }
    placeholder_eclis = sorted(
        {
            normalized
            for case in discovered
            if case.is_placeholder
            and (normalized := normalize_ecli(case.ecli))
            and normalized not in substantive_eclis
        }
    )
    for index in range(0, len(placeholder_eclis), 20):
        chunk = placeholder_eclis[index : index + 20]
        try:
            rows = await client.search(
                ecli=chunk, doctypes=(), languages=("ENG", "FRE"), limit=None
            )
        except Exception as exc:  # noqa: BLE001 - network failure becomes audit data
            lookup_errors += 1
            if cache_path:
                append_jsonl(
                    cache_path,
                    {
                        "lookup": {"kind": "placeholder-ecli", "value": chunk},
                        "status": "error",
                        "error": str(exc),
                    },
                )
            continue
        for row in rows:
            case = Case.model_validate(row)
            fetched.append(case)
            if cache_path:
                append_jsonl(cache_path, {"case": case.model_dump(mode="json")})
    return [*cached_cases, *fetched], lookup_errors


def _load_cached_cases(cache_path: str | Path | None) -> list[Case]:
    """Load persisted HUDOC lookup rows for both online and offline resolution."""
    if cache_path is None or not Path(cache_path).exists():
        return []
    return [
        Case.model_validate(row["case"])
        for row in iter_jsonl(cache_path)
        if isinstance(row.get("case"), dict)
    ]


def _plausible_candidate(mention: CitationMention, candidate: CitationCandidate) -> bool:
    evidence = set(candidate.positive_evidence)
    conflicts = set(candidate.conflicting_evidence)
    if {
        "target date is after source document",
        "exact source document self-edge",
        "placeholder HUDOC record",
    } & conflicts:
        return "placeholder HUDOC record" in conflicts
    if mention.explicit_appnos and "different application number" in conflicts:
        return bool(
            "exact normalized title" in evidence
            and "exact date" in evidence
            and not {"different document kind", "different procedural phase"} & conflicts
        )
    if {
        "explicit application number",
        "advisory request identifier",
        "exact reporter locator",
        "authority ECLI",
        "authority itemid",
    } & evidence:
        return True
    title = {"exact normalized title", "similar title"} & evidence
    corroboration = {
        "exact date",
        "partial date",
        "document kind",
        "procedural phase",
        "Grand Chamber",
        "reporter publication year",
        "authority date",
        "authority reporter locator",
        "authority document kind",
    } & evidence
    return bool(title and corroboration)


def _conflict_method(mention: CitationMention, candidates: list[CitationCandidate]) -> str:
    relevant = [candidate for candidate in candidates if _plausible_candidate(mention, candidate)]
    if not relevant and len(candidates) == 1:
        relevant = candidates
    evidence = {value for candidate in relevant for value in candidate.positive_evidence}
    conflicts = {value for candidate in relevant for value in candidate.conflicting_evidence}
    if relevant and all("placeholder HUDOC record" in c.conflicting_evidence for c in relevant):
        return "only placeholder HUDOC target found; substantive language sibling required"
    if (
        "different application number" in conflicts
        and "exact normalized title" in evidence
        and "exact date" in evidence
    ):
        return "printed application number conflicts with HUDOC metadata"
    if (
        "different date" in conflicts
        and "explicit application number" in evidence
        and {"exact normalized title", "similar title"} & evidence
    ):
        return "printed date conflicts with HUDOC metadata"
    if "different document kind" in conflicts and {
        "explicit application number",
        "exact date",
    }.issubset(evidence):
        return "printed document kind conflicts with HUDOC metadata"
    if "different procedural phase" in conflicts and {
        "explicit application number",
        "exact date",
    }.issubset(evidence):
        return "printed procedural phase conflicts with HUDOC metadata"
    if mention.advisory_request_id:
        return "advisory request identifier target not discovered in HUDOC"
    if mention.reporter:
        return "reporter citation not linked to a unique HUDOC document"
    if mention.cited_name and mention.target_year:
        return "no corroborated HUDOC target found for title, year, and document type"
    if mention.cited_name:
        return "no corroborated HUDOC target found for the parsed reference"
    return "insufficient bibliographic evidence"


def _ambiguous_method(mention: CitationMention, candidates: list[CitationCandidate]) -> str:
    evidence = {value for candidate in candidates for value in candidate.positive_evidence}
    conflicts = {value for candidate in candidates for value in candidate.conflicting_evidence}
    phases = {candidate.procedural_phase for candidate in candidates}
    if "different date" in conflicts and "explicit application number" in evidence:
        return "printed date conflicts across multiple candidate documents"
    if mention.reporter and len(phases) > 1:
        return "reporter citation has multiple procedural HUDOC candidates"
    if mention.explicit_appnos:
        return "multiple plausible documents share the application number"
    return "multiple plausible candidate documents"


def _resolve_one(
    mention: CitationMention,
    catalog: TargetCatalog,
    authority: CitationAuthority,
    historical_catalog: HistoricalCitationCatalog,
    overrides: list[CitationOverride],
    *,
    lookup_failed: bool = False,
    authority_exact_index: dict[str, list[CitationAuthorityEntry]] | None = None,
    authority_reporter_index: dict[str, list[CitationAuthorityEntry]] | None = None,
    authority_appno_index: dict[str, list[CitationAuthorityEntry]] | None = None,
    historical_reporter_index: dict[str, list[HistoricalCatalogEntry]] | None = None,
) -> CitationResolution:
    if (
        mention.source_date
        and mention.reporter
        and mention.reporter.year
        and mention.reporter.year > mention.source_date.year
    ):
        return CitationResolution(
            mention=mention,
            status="unresolved_reference",
            method="printed reporter year is after source document",
        )
    if mention.explicit_ecli:
        explicit_ecli_metadata = _target_ecli_metadata(mention.explicit_ecli)
        if explicit_ecli_metadata is None:
            return CitationResolution(
                mention=mention,
                status="unresolved_reference",
                method="printed ECLI has invalid target metadata",
            )
        if mention.source_date and explicit_ecli_metadata[0] > mention.source_date:
            return CitationResolution(
                mention=mention,
                status="unresolved_reference",
                method="printed ECLI date is after source document",
            )
    override = _override_for(mention, overrides)
    if override:
        target = catalog.exact(ecli=override.target_ecli, itemid=override.target_itemid)
        if target and not target.is_placeholder:
            return CitationResolution(
                mention=mention,
                status="resolved_override",
                method="reviewed override",
                target=target,
                candidates=[target],
                override_note=override.reviewer_note,
                override_reviewed_at=override.reviewed_at,
            )
        raise ValueError(
            "reviewed citation override target is absent or a placeholder in the HUDOC target catalog: "
            f"{override.target_ecli or override.target_itemid or '<missing identifier>'}"
        )

    discovery_method = str(mention.discovery_evidence.get("method", ""))
    if (
        mention.discovery_evidence.get("namespace") == "echr_commission"
        and mention.discovery_evidence.get("resolution_policy") == "classified_unresolved"
    ):
        return CitationResolution(
            mention=mention,
            status="unresolved_reference",
            method="classified European Commission reference; no Court document promotion",
        )
    if discovery_method.startswith(("authority_unique_", "authority_ambiguous_")):
        # The official list establishes the application behind a unique
        # applicant short form, but its unprinted date/kind/phase must not be
        # mistaken for evidence selecting a procedural HUDOC document.
        candidates = (
            list(
                {
                    candidate.node_id: candidate
                    for candidate in catalog.for_appnos(mention.explicit_appnos)
                }.values()
            )
            if discovery_method.startswith("authority_unique_")
            else []
        )
        return CitationResolution(
            mention=mention,
            status="unresolved_reference",
            method=(
                "official unique short form identifies the application; "
                "printed evidence does not select a procedural document"
            ),
            candidates=candidates,
        )

    if mention.explicit_ecli or mention.explicit_itemid:
        exact_candidates = catalog.exact_candidates(
            ecli=mention.explicit_ecli, itemid=mention.explicit_itemid
        )
        if len(exact_candidates) > 1:
            return CitationResolution(
                mention=mention,
                status="ambiguous_document",
                method="explicit identifier is duplicated in target catalog",
                candidates=exact_candidates,
            )
        target = exact_candidates[0] if exact_candidates else None
        if target and target.is_placeholder:
            return CitationResolution(
                mention=mention,
                status="target_not_in_hudoc",
                method="explicit identifier resolves only to a placeholder target",
                candidates=[target],
            )
        if target:
            candidate_ecli_conflicts = _candidate_ecli_conflicts(target)
            if candidate_ecli_conflicts:
                conflicted = target.model_copy(deep=True)
                conflicted.conflicting_evidence.extend(candidate_ecli_conflicts)
                return CitationResolution(
                    mention=mention,
                    status="unresolved_reference",
                    method="; ".join(candidate_ecli_conflicts),
                    candidates=[conflicted],
                )
            target = _hydrate_candidate_from_ecli(target)
            evaluated = _evaluate_candidate(mention, target)
            assert evaluated is not None
            exact_conflicts = {
                "target date is after source document",
                "exact source document self-edge",
                "probable source document language sibling",
            } & set(evaluated.conflicting_evidence)
            if exact_conflicts:
                return CitationResolution(
                    mention=mention,
                    status="unresolved_reference",
                    method="explicit identifier conflicts with source identity or chronology",
                    candidates=[evaluated],
                )
            return CitationResolution(
                mention=mention,
                status="resolved_identifier",
                method="explicit identifier",
                target=evaluated,
                candidates=[evaluated],
            )
        if mention.explicit_ecli and mention.explicit_itemid:
            ecli_matches = catalog.exact_candidates(ecli=mention.explicit_ecli)
            itemid_matches = catalog.exact_candidates(itemid=mention.explicit_itemid)
            if ecli_matches or itemid_matches:
                return CitationResolution(
                    mention=mention,
                    status="unresolved_reference",
                    method="printed ECLI and HUDOC item ID identify different documents",
                    candidates=list(
                        {
                            candidate.node_id: candidate
                            for candidate in (*ecli_matches, *itemid_matches)
                        }.values()
                    ),
                )
        return CitationResolution(
            mention=mention,
            status="target_not_in_hudoc",
            method="explicit identifier absent from target catalog",
        )

    if mention.reporter:
        historical = (
            historical_reporter_index.get(mention.reporter.key, [])
            if historical_reporter_index is not None
            else [
                entry
                for entry in historical_catalog.entries
                if entry.reporter_key == mention.reporter.key
            ]
        )
        historical = [
            entry
            for entry in historical
            if not _historical_identifier_conflicts(entry, source_date=mention.source_date)
        ]
        if mention.target_date:
            historical = [entry for entry in historical if entry.date == mention.target_date]
        if mention.explicit_appnos:
            historical = [
                entry for entry in historical if set(mention.explicit_appnos).issubset(entry.appnos)
            ]
        if mention.document_kind != "unknown":
            historical = [
                entry for entry in historical if entry.document_kind == mention.document_kind
            ]
        identities = {
            normalize_ecli(entry.target_ecli) or entry.target_itemid
            for entry in historical
            if entry.target_ecli or entry.target_itemid
        }
        identities.discard(None)
        if len(identities) == 1:
            source_title = normalize_docname(mention.cited_name)
            compatible = [
                entry
                for entry in historical
                if (normalize_ecli(entry.target_ecli) or entry.target_itemid) in identities
                and (
                    not source_title
                    or not (target_title := normalize_docname(entry.title))
                    or source_title == target_title
                    or SequenceMatcher(None, source_title, target_title).ratio() >= 0.82
                )
            ]
            if compatible:
                selected = compatible[0]
                exact_targets = catalog.exact_candidates(
                    ecli=selected.target_ecli,
                    itemid=None if selected.target_ecli else selected.target_itemid,
                )
                historical_evaluated = [
                    result
                    for target in exact_targets
                    if (result := _evaluate_candidate(mention, target)) is not None
                ]
                historical_accepted = [
                    checked
                    for candidate in historical_evaluated
                    if (checked := _historical_checked_candidate(mention, selected, candidate))
                    is not None
                ]
                if len(historical_accepted) != 1:
                    historical = []
                else:
                    historical_target = historical_accepted[0]
                    return CitationResolution(
                        mention=mention,
                        status="resolved_authority",
                        method="packaged historical reporter catalog",
                        target=historical_target,
                        candidates=historical_evaluated,
                    )

        # HUDOC exposes the official publication locator on the target row as
        # ``publishedby``.  When that per-document locator exactly matches the
        # printed reporter (including volume and extracts status), it may act
        # as corroboration before the broader authority lookup.  All ordinary
        # metadata gates remain conjunctive, and more than one accepted
        # canonical document is deliberately left unresolved.
        publication_key = publication_reporter_key(mention.reporter)
        publication_pool = [
            *catalog.for_appnos(mention.explicit_appnos or mention.scl_appno_candidates),
            *catalog.for_titles((mention.cited_name,)),
        ]
        publication_evaluated = [
            result
            for candidate in {
                candidate.node_id: candidate for candidate in publication_pool
            }.values()
            if publication_key in candidate.published_reporter_keys
            if (result := _evaluate_candidate(mention, candidate)) is not None
        ]
        publication_accepted = [
            candidate
            for candidate in publication_evaluated
            if _metadata_accepts(mention, candidate)
        ]
        if len(publication_accepted) == 1:
            return CitationResolution(
                mention=mention,
                status="resolved_metadata",
                method="exact HUDOC publication metadata plus printed selectors",
                target=publication_accepted[0],
                candidates=publication_evaluated,
            )

    authority_entries = _authority_matches(
        mention,
        authority,
        exact_index=authority_exact_index,
        reporter_index=authority_reporter_index,
        appno_index=authority_appno_index,
    )
    unavailable_entries = [entry for entry in authority_entries if entry.target_unavailable]
    active_authority_entries = [
        entry for entry in authority_entries if not entry.target_unavailable
    ]
    if unavailable_entries and not active_authority_entries:
        unavailable_entry = unavailable_entries[0]
        return CitationResolution(
            mention=mention,
            status="target_not_in_hudoc",
            method=unavailable_entry.coverage_note
            or "reviewed authority records target as unavailable in HUDOC",
            authority_entry_id=unavailable_entry.entry_id,
            documented_exclusion=True,
        )
    authority_evaluated: list[CitationCandidate] = []
    authority_accepted: list[tuple[CitationAuthorityEntry, CitationCandidate]] = []
    authority_preflight_conflicts: list[str] = []
    for entry in active_authority_entries:
        identifier_conflicts = _authority_identifier_conflicts(
            entry, source_date=mention.source_date
        )
        if identifier_conflicts:
            authority_preflight_conflicts.extend(identifier_conflicts)
            continue
        pool: list[CitationCandidate] = []
        ecli_targets = catalog.exact_candidates(ecli=entry.target_ecli) if entry.target_ecli else []
        itemid_targets = (
            catalog.exact_candidates(itemid=entry.target_itemid) if entry.target_itemid else []
        )
        if entry.target_ecli and entry.target_itemid:
            if ecli_targets or itemid_targets:
                ecli_nodes = {candidate.node_id for candidate in ecli_targets}
                itemid_nodes = {candidate.node_id for candidate in itemid_targets}
                common_nodes = ecli_nodes & itemid_nodes
                if common_nodes:
                    pool = [
                        candidate for candidate in ecli_targets if candidate.node_id in common_nodes
                    ]
                else:
                    # Both identifiers exist but point to disjoint canonical
                    # nodes. Never manufacture or select one side of a hybrid.
                    if ecli_targets and itemid_targets:
                        authority_preflight_conflicts.append(
                            "authority ECLI and item ID identify different documents"
                        )
                        authority_evaluated.extend(
                            list(
                                {
                                    candidate.node_id: candidate
                                    for candidate in (*ecli_targets, *itemid_targets)
                                }.values()
                            )
                        )
                        continue
                    pool = [*ecli_targets, *itemid_targets]
            else:
                pool = [_candidate_from_authority(entry)]
        elif ecli_targets or itemid_targets:
            pool = [*ecli_targets, *itemid_targets]
        elif entry.target_ecli or entry.target_itemid:
            pool = [_candidate_from_authority(entry)]
        else:
            appnos = entry.appnos or mention.explicit_appnos or mention.scl_appno_candidates
            candidates = [
                *catalog.for_appnos(appnos),
                *catalog.for_advisory_request_ids((mention.advisory_request_id or "",)),
                *catalog.for_titles((entry.title, mention.cited_name)),
            ]
            pool = list({candidate.node_id: candidate for candidate in candidates}.values())
            if not pool:
                pool = [catalog.candidates[node_id] for node_id in sorted(catalog.candidates)]
        authority_candidates_evaluated = [
            result
            for candidate in pool
            if (result := _evaluate_candidate(mention, candidate, authority_entry=entry))
            is not None
        ]
        authority_evaluated.extend(authority_candidates_evaluated)
        authority_accepted.extend(
            (entry, candidate)
            for candidate in authority_candidates_evaluated
            if _authority_accepts(mention, entry, candidate)
        )

    accepted_by_node: dict[str, list[tuple[CitationAuthorityEntry, CitationCandidate]]] = (
        defaultdict(list)
    )
    for entry, candidate in authority_accepted:
        accepted_by_node[candidate.node_id].append((entry, candidate))
    if len(accepted_by_node) == 1:
        selected_pair = min(
            next(iter(accepted_by_node.values())), key=lambda value: value[0].entry_id
        )
        return CitationResolution(
            mention=mention,
            status="resolved_authority",
            method="official citation authority",
            target=selected_pair[1],
            candidates=list(
                {candidate.node_id: candidate for candidate in authority_evaluated}.values()
            ),
            authority_entry_id=selected_pair[0].entry_id,
        )
    if len(accepted_by_node) > 1:
        return CitationResolution(
            mention=mention,
            status="ambiguous_document",
            method="official citation authority identifies multiple compatible documents",
            candidates=[values[0][1] for _, values in sorted(accepted_by_node.items())],
        )

    if authority_entries:
        if authority_evaluated:
            return CitationResolution(
                mention=mention,
                status="unresolved_reference",
                method=_conflict_method(mention, authority_evaluated),
                candidates=authority_evaluated,
                authority_entry_id=authority_entries[0].entry_id,
            )
        if authority_preflight_conflicts:
            return CitationResolution(
                mention=mention,
                status="unresolved_reference",
                method="; ".join(sorted(set(authority_preflight_conflicts))),
                authority_entry_id=authority_entries[0].entry_id,
            )
        if lookup_failed:
            return CitationResolution(
                mention=mention,
                status="unresolved_reference",
                method="HUDOC target lookup failed",
                authority_entry_id=authority_entries[0].entry_id,
            )
        return CitationResolution(
            mention=mention,
            status="target_not_in_hudoc",
            method="authority target absent from catalog",
            authority_entry_id=authority_entries[0].entry_id,
        )

    appnos = mention.explicit_appnos or mention.scl_appno_candidates
    metadata_pool = [
        *catalog.for_appnos(appnos),
        *catalog.for_advisory_request_ids((mention.advisory_request_id or "",)),
        *catalog.for_titles((mention.cited_name,)),
    ]
    metadata_pool = list({candidate.node_id: candidate for candidate in metadata_pool}.values())
    metadata_evaluated = [
        result
        for candidate in metadata_pool
        if (result := _evaluate_candidate(mention, candidate)) is not None
    ]
    metadata_accepted = [
        candidate for candidate in metadata_evaluated if _metadata_accepts(mention, candidate)
    ]
    if len(metadata_accepted) == 1:
        if mention.advisory_request_id:
            method = "advisory request identifier plus corroborating metadata"
        elif mention.explicit_appnos:
            method = "appno plus corroborating metadata"
        else:
            method = "title/reporter plus corroborating metadata"
        return CitationResolution(
            mention=mention,
            status="resolved_metadata",
            method=method,
            target=metadata_accepted[0],
            candidates=metadata_evaluated,
        )
    plausible = [
        candidate for candidate in metadata_evaluated if _plausible_candidate(mention, candidate)
    ]
    if len(metadata_accepted) > 1 or len(plausible) > 1:
        return CitationResolution(
            mention=mention,
            status="ambiguous_document",
            method=_ambiguous_method(mention, plausible or metadata_accepted),
            candidates=metadata_evaluated,
        )
    return CitationResolution(
        mention=mention,
        status="unresolved_reference",
        method=_conflict_method(mention, metadata_evaluated),
        candidates=metadata_evaluated,
    )


def _graph_artifacts(
    source_cases: list[Case],
    resolutions: list[CitationResolution],
    catalog: TargetCatalog,
) -> tuple[list[CitationCandidate], list[dict[str, object]], list[dict[str, object]]]:
    source_nodes: dict[str, CitationCandidate] = {}
    for case in source_cases:
        if not case.ecli and not case.itemid:
            continue
        node_id = canonical_node_id(case)
        canonical_source = catalog.candidates.get(node_id)
        if canonical_source is not None:
            source_nodes[node_id] = canonical_source.model_copy(
                deep=True, update={"in_source_corpus": True}
            )
            continue
        source_nodes.setdefault(
            node_id,
            CitationCandidate(
                node_id=node_id,
                itemid=case.itemid,
                ecli=normalize_ecli(case.ecli),
                advisory_request_id=case.advop_identifier,
                docname=case.docname,
                appnos=list(case.appno),
                date=case.kp_date,
                language=case.language,
                doctype=case.doctype,
                document_kind=_candidate_kind(case),
                procedural_phase=_candidate_phase(case),  # type: ignore[arg-type]
                grand_chamber=(case.doctype_branch or "").upper() == "GRANDCHAMBER"
                or "ADVISORYOPINIONS;PROTOCOL16;OPINIONS"
                in (case.document_collection_id or "").upper(),
                respondent=list(case.respondent),
                is_placeholder=case.is_placeholder,
                in_source_corpus=True,
                hudoc_url=f"https://hudoc.echr.coe.int/eng?i={case.itemid}"
                if case.itemid
                else None,
            ),
        )
    targets = {
        resolution.target.node_id: resolution.target
        for resolution in resolutions
        if resolution.resolved and resolution.target
    }
    all_nodes = {**source_nodes, **targets}
    edge_mentions: dict[tuple[str, str], list[str]] = defaultdict(list)
    for resolution in resolutions:
        if not resolution.resolved or not resolution.target:
            continue
        if resolution.mention.source_ecli:
            source_id = f"ecli:{resolution.mention.source_ecli}"
        elif resolution.mention.source_itemid:
            source_id = f"itemid:{resolution.mention.source_itemid}"
        else:
            continue
        edge_mentions[(source_id, resolution.target.node_id)].append(resolution.mention.mention_id)
    edges = [
        {
            "source": source,
            "target": target,
            "citation_count": len(mention_ids),
            "mention_ids": mention_ids,
        }
        for (source, target), mention_ids in sorted(edge_mentions.items())
    ]
    nodes = [candidate.model_dump(mode="json") for candidate in all_nodes.values()]
    return list(targets.values()), nodes, edges


async def resolve_citations(
    source_cases: Iterable[Case],
    *,
    mentions: Iterable[CitationMention] | None = None,
    authority: CitationAuthority | None = None,
    historical_catalog: HistoricalCitationCatalog | None = None,
    catalog: Iterable[Case] | None = None,
    client: Any | None = None,
    overrides: Iterable[CitationOverride] | None = None,
    cache_path: str | Path | None = None,
    max_mentions: int | None = None,
) -> CitationResolutionResult:
    """Resolve SCL mentions using authority, local metadata, and optional HUDOC lookup."""
    sources = list(source_cases)
    authority = authority or load_authority()
    historical_catalog = historical_catalog or load_historical_catalog()
    mention_list = (
        list(mentions)
        if mentions is not None
        else [mention for case in sources for mention in parse_scl_mentions(case)]
    )
    if max_mentions is not None:
        mention_list = mention_list[:max_mentions]
    source_by_id = {case.itemid: case for case in sources if case.itemid}
    for mention in mention_list:
        source = source_by_id.get(mention.source_itemid) if mention.source_itemid else None
        if source and source.text:
            mention.source_context = locate_source_context(source.text, mention)

    catalog_cases = [*sources, *(catalog or [])]
    lookup_errors = 0
    if client is not None:
        online_cases, lookup_errors = await _fetch_online_candidates(
            mention_list,
            client,
            authority=authority,
            cache_path=cache_path,
            source_cases=sources,
        )
        catalog_cases.extend(online_cases)
    else:
        # The lookup cache is a portable metadata catalog, not merely an
        # online request optimisation. Offline reruns must use the same cached
        # candidate rows so enabling downstream paragraph hydration cannot
        # silently demote already resolved document targets.
        catalog_cases.extend(_load_cached_cases(cache_path))
    source_ids = {canonical_node_id(case) for case in sources if case.ecli or case.itemid}
    target_catalog = TargetCatalog(catalog_cases, source_node_ids=source_ids)
    override_list = list(overrides or [])
    authority_exact_index: dict[str, list[CitationAuthorityEntry]] = defaultdict(list)
    authority_reporter_index: dict[str, list[CitationAuthorityEntry]] = defaultdict(list)
    authority_appno_index: dict[str, list[CitationAuthorityEntry]] = defaultdict(list)
    for entry in authority.entries:
        authority_exact_index[entry.normalized_citation].append(entry)
        for appno in entry.appnos:
            authority_appno_index[appno].append(entry)
        if entry.reporter:
            authority_reporter_index[entry.reporter.key].append(entry)
    historical_reporter_index: dict[str, list[HistoricalCatalogEntry]] = defaultdict(list)
    for historical_entry in historical_catalog.entries:
        historical_reporter_index[historical_entry.reporter_key].append(historical_entry)
    resolutions = [
        _resolve_one(
            mention,
            target_catalog,
            authority,
            historical_catalog,
            override_list,
            lookup_failed=lookup_errors > 0,
            authority_exact_index=authority_exact_index,
            authority_reporter_index=authority_reporter_index,
            authority_appno_index=authority_appno_index,
            historical_reporter_index=historical_reporter_index,
        )
        for mention in mention_list
    ]
    targets, nodes, edges = _graph_artifacts(sources, resolutions, target_catalog)
    report = CitationResolutionReport.from_resolutions(
        resolutions,
        source_documents=len(sources),
        authority=authority,
        target_documents=len(targets),
        edge_count=len(edges),
        placeholder_nodes=sum(bool(node.get("is_placeholder")) for node in nodes),
        lookup_errors=lookup_errors,
        unidentified_source_documents=sum(not bool(case.ecli or case.itemid) for case in sources),
    )
    return CitationResolutionResult(
        resolutions=resolutions, targets=targets, nodes=nodes, edges=edges, report=report
    )
