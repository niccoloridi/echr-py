"""Deterministic, evidence-gated SCL citation resolution."""

from __future__ import annotations

import asyncio
import csv
from collections import defaultdict
from collections.abc import Iterable
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
    parse_reporter,
    parse_scl_mentions,
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
        self.by_ecli: dict[str, str] = {}
        self.by_itemid: dict[str, str] = {}
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
        ecli = normalize_ecli(chosen.ecli)
        candidate = CitationCandidate(
            node_id=node_id,
            itemid=chosen.itemid,
            ecli=ecli,
            advisory_request_id=chosen.advop_identifier,
            docname=chosen.docname,
            title_aliases=list(dict.fromkeys(case.docname for case in group if case.docname)),
            reporter_keys=reporter_keys,
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
        )
        self.candidates[node_id] = candidate
        for case in group:
            if case.itemid:
                self.by_itemid[case.itemid] = node_id
            normalized_ecli = normalize_ecli(case.ecli)
            if normalized_ecli:
                self.by_ecli[normalized_ecli] = node_id
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
        selected: list[CitationCandidate] = []
        for identifier in identifiers:
            ids = self.by_advisory_request_id.get(identifier.upper(), set())
            candidates = [self.candidates[node_id] for node_id in ids]
            if candidates:
                chosen = min(
                    candidates,
                    key=lambda candidate: (
                        (candidate.language or "").upper() != "ENG",
                        candidate.itemid or "",
                    ),
                )
                selected.append(chosen.model_copy(deep=True))
        return selected

    def exact(
        self, *, ecli: str | None = None, itemid: str | None = None
    ) -> CitationCandidate | None:
        node_id = self.by_ecli.get(normalize_ecli(ecli) or "") if ecli else None
        if node_id is None and itemid:
            node_id = self.by_itemid.get(itemid)
        return self.candidates[node_id].model_copy(deep=True) if node_id else None


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


def _title_similarity(mention: CitationMention, candidate: CitationCandidate) -> float:
    source = normalize_docname(mention.cited_name)
    aliases = candidate.title_aliases
    if not aliases and candidate.docname:
        aliases = [candidate.docname]
    targets = [normalize_docname(title) for title in aliases if title]
    targets.extend(
        normalize_docname(base)
        for title in aliases
        if title and (base := extract_reference_name(title))
    )
    return max(
        (SequenceMatcher(None, source, target).ratio() for target in targets if source and target),
        default=0.0,
    )


def _authority_title_matches(
    mention: CitationMention, entry: CitationAuthorityEntry
) -> bool:
    source = normalize_docname(mention.cited_name)
    target = entry.normalized_title or normalize_docname(entry.title)
    if not source or not target:
        return not source
    return source == target or SequenceMatcher(None, source, target).ratio() >= 0.82


def _entry_candidate_title_matches(
    entry: CitationAuthorityEntry, candidate: CitationCandidate
) -> bool:
    source = entry.normalized_title or normalize_docname(entry.title)
    aliases = list(candidate.title_aliases)
    if not aliases and candidate.docname:
        aliases = [candidate.docname]
    targets = [normalize_docname(value) for value in aliases if value]
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


def _evaluate_candidate(
    mention: CitationMention,
    candidate: CitationCandidate,
    *,
    authority_entry: CitationAuthorityEntry | None = None,
) -> CitationCandidate | None:
    candidate = candidate.model_copy(deep=True)
    if mention.source_date and candidate.date and candidate.date > mention.source_date:
        candidate.conflicting_evidence.append("target date is after source document")
        return candidate
    if candidate.is_placeholder:
        candidate.conflicting_evidence.append("placeholder HUDOC record")
        return candidate
    if candidate.node_id in {
        f"ecli:{mention.source_ecli}" if mention.source_ecli else "",
        f"itemid:{mention.source_itemid}" if mention.source_itemid else "",
    }:
        candidate.conflicting_evidence.append("exact source document self-edge")
        return candidate

    candidate.title_similarity = _title_similarity(mention, candidate)
    if set(mention.explicit_appnos) & set(candidate.appnos):
        candidate.positive_evidence.append("explicit application number")
    elif mention.explicit_appnos and candidate.appnos:
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
        elif candidate.date:
            candidate.conflicting_evidence.append("different date")
    if mention.reporter:
        if mention.reporter.key in candidate.reporter_keys:
            candidate.positive_evidence.append("exact reporter locator")
        elif candidate.reporter_keys:
            candidate.conflicting_evidence.append("different reporter locator")
        if (
            mention.reporter.year
            and candidate.date
            and mention.reporter.year == candidate.date.year
        ):
            candidate.positive_evidence.append("reporter publication year")
    elif mention.target_year and candidate.date:
        partial_matches = candidate.date.year == mention.target_year
        if mention.target_month is not None:
            partial_matches = partial_matches and candidate.date.month == mention.target_month
        if partial_matches:
            candidate.positive_evidence.append("partial date")
        else:
            candidate.conflicting_evidence.append("different date")
    if mention.document_kind != "unknown":
        if _document_kind_matches(mention, candidate):
            candidate.positive_evidence.append("document kind")
        elif candidate.document_kind != "unknown":
            candidate.conflicting_evidence.append("different document kind")
    if mention.procedural_phase != "unknown":
        if _procedural_phase_matches(mention, candidate):
            candidate.positive_evidence.append("procedural phase")
        elif candidate.procedural_phase != "unknown":
            candidate.conflicting_evidence.append("different procedural phase")
    if mention.grand_chamber:
        if candidate.grand_chamber:
            candidate.positive_evidence.append("Grand Chamber")
        else:
            candidate.conflicting_evidence.append("not Grand Chamber")
    if candidate.title_similarity >= 0.97:
        candidate.positive_evidence.append("exact normalized title")
    elif candidate.title_similarity >= 0.82:
        candidate.positive_evidence.append("similar title")
    elif mention.cited_name:
        candidate.conflicting_evidence.append("different title")
    if authority_entry:
        if authority_entry.target_ecli and authority_entry.target_ecli == candidate.ecli:
            candidate.positive_evidence.append("authority ECLI")
        if authority_entry.target_itemid and authority_entry.target_itemid == candidate.itemid:
            candidate.positive_evidence.append("authority itemid")
        if authority_entry.date and authority_entry.date == candidate.date:
            candidate.positive_evidence.append("authority date")
        authority_kind = _authority_document_kind(authority_entry)
        if authority_kind != "unknown":
            if candidate.document_kind == authority_kind:
                candidate.positive_evidence.append("authority document kind")
            elif candidate.document_kind != "unknown":
                candidate.conflicting_evidence.append("different authority document kind")
        authority_phase = _authority_phase(authority_entry)
        if authority_phase != "unknown" and candidate.procedural_phase != "unknown":
            if candidate.procedural_phase == authority_phase:
                candidate.positive_evidence.append("authority procedural phase")
            else:
                candidate.conflicting_evidence.append("different authority procedural phase")
        if authority_entry.reporter and authority_entry.reporter.key in candidate.reporter_keys:
            candidate.positive_evidence.append("authority reporter locator")
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
    return candidate


def _metadata_accepts(mention: CitationMention, candidate: CitationCandidate) -> bool:
    evidence = set(candidate.positive_evidence)
    conflicts = set(candidate.conflicting_evidence)
    if (
        "target date is after source document" in conflicts
        or "exact source document self-edge" in conflicts
        or "placeholder HUDOC record" in conflicts
        or (mention.cited_name and "different title" in conflicts)
    ):
        return False
    if mention.explicit_appnos and "explicit application number" not in evidence:
        return False
    if mention.advisory_request_id:
        return "advisory request identifier" in evidence and bool(
            {"exact date", "document kind", "procedural phase", "Grand Chamber"} & evidence
        ) and not (
            {
                "different date",
                "different document kind",
                "different procedural phase",
                "different advisory request identifier",
            }
            & conflicts
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
        return corroborated and not (
            {"different date", "different document kind", "different procedural phase"} & conflicts
        )
    return (
        "exact date" in evidence
        and ("exact normalized title" in evidence or "similar title" in evidence)
        and not ({"different document kind", "different procedural phase"} & conflicts)
    )


def _authority_accepts(
    mention: CitationMention,
    entry: CitationAuthorityEntry,
    candidate: CitationCandidate,
) -> bool:
    conflicts = set(candidate.conflicting_evidence)
    if entry.target_ecli or entry.target_itemid:
        return (
            "authority ECLI" in candidate.positive_evidence
            or "authority itemid" in candidate.positive_evidence
        ) and not {
            "target date is after source document",
            "exact source document self-edge",
            "placeholder HUDOC record",
        } & conflicts
    evidence = set(candidate.positive_evidence)
    if mention.explicit_appnos and "explicit application number" not in evidence:
        return False
    if {
        "different date",
        "different document kind",
        "different procedural phase",
        "not Grand Chamber",
        "target date is after source document",
        "exact source document self-edge",
        "placeholder HUDOC record",
        "different authority document kind",
        "different authority procedural phase",
        "different authority title",
    } & conflicts:
        return False
    if mention.cited_name and "different title" in conflicts:
        return False
    identity = {
        "authority date",
        "authority reporter locator",
        "authority reporter publication year",
    } & evidence
    if "authority reporter publication year" in identity:
        identity.update(
            {"authority document kind", "authority procedural phase"} & evidence
        )
        # A reporter year/volume can contain several procedural documents. It
        # becomes document-level evidence only with the authority's document
        # kind or phase, not merely because both candidates were published that year.
        identity.discard("authority reporter publication year")
    return bool({"authority title", "authority similar title"} & evidence) and bool(
        identity
    )


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
    for entry in authority.entries:
        authority_exact_index[entry.normalized_citation].append(entry)
        if entry.reporter:
            authority_reporter_index[entry.reporter.key].append(entry)

    def authority_matches(mention: CitationMention) -> list[CitationAuthorityEntry]:
        return _authority_matches(
            mention,
            authority,
            exact_index=authority_exact_index,
            reporter_index=authority_reporter_index,
        )

    cached_rows: list[dict[str, Any]] = []
    if cache_path and Path(cache_path).exists():
        cached_rows = [
            dict(row["case"]) for row in iter_jsonl(cache_path) if isinstance(row.get("case"), dict)
        ]
    cached_cases = [Case.model_validate(row) for row in cached_rows]
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


def _plausible_candidate(
    mention: CitationMention, candidate: CitationCandidate
) -> bool:
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


def _conflict_method(
    mention: CitationMention, candidates: list[CitationCandidate]
) -> str:
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


def _ambiguous_method(
    mention: CitationMention, candidates: list[CitationCandidate]
) -> str:
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
    historical_reporter_index: dict[str, list[HistoricalCatalogEntry]] | None = None,
) -> CitationResolution:
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

    if mention.explicit_ecli or mention.explicit_itemid:
        target = catalog.exact(ecli=mention.explicit_ecli, itemid=mention.explicit_itemid)
        if target and not target.is_placeholder:
            return CitationResolution(
                mention=mention,
                status="resolved_identifier",
                method="explicit identifier",
                target=target,
                candidates=[target],
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
        if mention.target_date:
            dated = [entry for entry in historical if entry.date == mention.target_date]
            if dated:
                historical = dated
        if mention.document_kind != "unknown":
            typed = [
                entry for entry in historical
                if entry.document_kind == mention.document_kind
            ]
            if typed:
                historical = typed
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
                ecli = normalize_ecli(selected.target_ecli)
                target = CitationCandidate(
                    node_id=f"ecli:{ecli}" if ecli else f"itemid:{selected.target_itemid}",
                    itemid=selected.target_itemid,
                    ecli=ecli,
                    docname=selected.title,
                    title_aliases=[selected.title] if selected.title else [],
                    reporter_keys=[selected.reporter_key],
                    appnos=list(selected.appnos),
                    date=selected.date,
                    document_kind=selected.document_kind,
                )
                return CitationResolution(
                    mention=mention,
                    status="resolved_authority",
                    method="packaged historical reporter catalog",
                    target=target,
                    candidates=[target],
                )

    authority_entries = _authority_matches(
        mention,
        authority,
        exact_index=authority_exact_index,
        reporter_index=authority_reporter_index,
    )
    unavailable_entry = next(
        (entry for entry in authority_entries if entry.target_unavailable), None
    )
    if unavailable_entry:
        return CitationResolution(
            mention=mention,
            status="target_not_in_hudoc",
            method=unavailable_entry.coverage_note
            or "reviewed authority records target as unavailable in HUDOC",
            authority_entry_id=unavailable_entry.entry_id,
            documented_exclusion=True,
        )
    for entry in authority_entries:
        pool: list[CitationCandidate] = []
        exact = catalog.exact(ecli=entry.target_ecli, itemid=entry.target_itemid)
        if exact:
            pool = [exact]
        else:
            appnos = entry.appnos or mention.explicit_appnos or mention.scl_appno_candidates
            candidates = [
                *catalog.for_appnos(appnos),
                *catalog.for_advisory_request_ids((mention.advisory_request_id or "",)),
                *catalog.for_titles((entry.title, mention.cited_name)),
            ]
            pool = list({candidate.node_id: candidate for candidate in candidates}.values())
            if not pool:
                pool = list(catalog.candidates.values())
        evaluated = [
            result
            for candidate in pool
            if (result := _evaluate_candidate(mention, candidate, authority_entry=entry))
            is not None
        ]
        accepted = [
            candidate for candidate in evaluated if _authority_accepts(mention, entry, candidate)
        ]
        if len(accepted) == 1:
            return CitationResolution(
                mention=mention,
                status="resolved_authority",
                method="official citation authority",
                target=accepted[0],
                candidates=evaluated,
                authority_entry_id=entry.entry_id,
            )

    appnos = mention.explicit_appnos or mention.scl_appno_candidates
    metadata_pool = [
        *catalog.for_appnos(appnos),
        *catalog.for_advisory_request_ids((mention.advisory_request_id or "",)),
        *catalog.for_titles((mention.cited_name,)),
    ]
    metadata_pool = list({candidate.node_id: candidate for candidate in metadata_pool}.values())
    evaluated = [
        result
        for candidate in metadata_pool
        if (result := _evaluate_candidate(mention, candidate)) is not None
    ]
    accepted = [candidate for candidate in evaluated if _metadata_accepts(mention, candidate)]
    if len(accepted) == 1:
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
            target=accepted[0],
            candidates=evaluated,
        )
    plausible = [candidate for candidate in evaluated if _plausible_candidate(mention, candidate)]
    if len(accepted) > 1 or len(plausible) > 1:
        return CitationResolution(
            mention=mention,
            status="ambiguous_document",
            method=_ambiguous_method(mention, plausible or accepted),
            candidates=evaluated,
        )
    if authority_entries:
        if evaluated:
            return CitationResolution(
                mention=mention,
                status="unresolved_reference",
                method=_conflict_method(mention, evaluated),
                candidates=evaluated,
                authority_entry_id=authority_entries[0].entry_id,
            )
        if lookup_failed:
            return CitationResolution(
                mention=mention,
                status="unresolved_reference",
                method="HUDOC target lookup failed",
                candidates=evaluated,
                authority_entry_id=authority_entries[0].entry_id,
            )
        return CitationResolution(
            mention=mention,
            status="target_not_in_hudoc",
            method="authority target absent from catalog",
            candidates=evaluated,
            authority_entry_id=authority_entries[0].entry_id,
        )
    return CitationResolution(
        mention=mention,
        status="unresolved_reference",
        method=_conflict_method(mention, evaluated),
        candidates=evaluated,
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
    source_ids = {canonical_node_id(case) for case in sources if case.ecli or case.itemid}
    target_catalog = TargetCatalog(catalog_cases, source_node_ids=source_ids)
    override_list = list(overrides or [])
    authority_exact_index: dict[str, list[CitationAuthorityEntry]] = defaultdict(list)
    authority_reporter_index: dict[str, list[CitationAuthorityEntry]] = defaultdict(list)
    for entry in authority.entries:
        authority_exact_index[entry.normalized_citation].append(entry)
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
