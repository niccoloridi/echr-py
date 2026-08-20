"""Deterministic, paragraph-aware location of resolved SCL authorities."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from ..models.case import Case
from ..models.common import DocumentBlock, DocumentSpine
from ..text.spine import build_spine_from_html, build_spine_from_text
from .models import (
    CitationDiscoveryResult,
    CitationMention,
    CitationOccurrence,
    CitationOccurrenceReport,
    CitationOccurrenceResult,
    CitationResolution,
    HistoricalCatalogEntry,
)
from .reporter import (
    APPNO_REGEX,
    ECLI_REGEX,
    ITEMID_REGEX,
    extract_respondent,
    normalize_reference,
    parse_reporter,
    parse_scl_mentions,
)

_PHASE_RE = re.compile(
    r"[,;]?\s*(?:admissibility|merits|just satisfaction|friendly settlement|"
    r"preliminary objections?|revision|interpretation|striking out|\[GC\]|"
    r"grande chambre)\s*$",
    re.IGNORECASE,
)
_PARTY_RE = re.compile(r"\s+(?:v\.?|c\.?|contre|against)\s+", re.IGNORECASE)
_DATE_TEXT_RE = re.compile(
    r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|janvier|février|mars|avril|mai|"
    r"juin|juillet|août|septembre|octobre|novembre|décembre)\s+\d{4}\b",
    re.IGNORECASE,
)
_UNMATCHED_ANCHORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ecli", re.compile(r"\bECLI:CE:ECHR:\d{4}:\d{4}(?:JUD|DEC|ADV)\d+\b", re.I)),
    ("application_number", re.compile(r"\b\d{1,6}/\d{2,4}\b")),
    (
        "reporter",
        re.compile(
            r"\b(?:(?:Series|s[ée]rie)\s+A\s+n(?:o\.?|°)\s*\d+(?:[-‑–][A-Z])?"
            r"|(?:ECHR|CEDH)\s+(?:19|20)\d{2}(?:[-‑–][IVX]+)?)\b",
            re.I,
        ),
    ),
    (
        "full_name",
        re.compile(
            r"\b[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+"
            r"(?:\s+(?:and|et|[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+)){0,5}"
            r"\s+(?:v\.?|c\.?|contre|against)\s+(?:the\s+)?"
            r"[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+"
            r"(?:\s+(?:and|et|the|of|[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+)){0,5}\b"
        ),
    ),
)
_CUE_RE = re.compile(
    r"(?:§§?|¶|paras?\.?|judg(?:ment|ement)|decision|décision|arrêt|"
    r"cited above|précité(?:e)?|no\.?\s*\d{3,5}/\d{2}|ECHR\s+\d{4})",
    re.IGNORECASE,
)
_PIN_RE = re.compile(
    r"(?:§§?|¶|par(?:a(?:graph)?s?)?\.?|paragraphes?)\s*"
    r"(?P<labels>\d+(?:\.\d+)?(?:\([a-z]\))?"
    r"(?:\s*(?:[-–\u2014]|to|à)\s*\d+(?:\.\d+)?(?:\([a-z]\))?)?"
    r"(?:\s*(?:,|and|et)\s*\d+(?:\.\d+)?(?:\([a-z]\))?"
    r"(?:\s*(?:[-–\u2014]|to|à)\s*\d+(?:\.\d+)?(?:\([a-z]\))?)?)*)",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(
    r"\d+(?:\.\d+)?(?:\([a-z]\))?(?:\s*(?:[-–\u2014]|to|à)\s*"
    r"\d+(?:\.\d+)?(?:\([a-z]\))?)?",
    re.IGNORECASE,
)
_GENERIC = {
    "applicant", "application", "case", "commission", "court", "decision",
    "government", "judgment", "kingdom", "republic", "state", "states",
}
_STATE_WORDS = {
    "albania", "andorra", "armenia", "austria", "azerbaijan", "belgium",
    "bulgaria", "croatia", "cyprus", "denmark", "estonia", "finland", "france",
    "georgia", "germany", "greece", "hungary", "iceland", "ireland", "italy",
    "latvia", "liechtenstein", "lithuania", "luxembourg", "malta", "moldova",
    "monaco", "montenegro", "netherlands", "norway", "poland", "portugal",
    "romania", "russia", "serbia", "slovakia", "slovenia", "spain", "sweden",
    "switzerland", "turkey", "türkiye", "ukraine", "united kingdom",
}
_UNICODE_NAME_TOKEN = r"(?-i:(?![a-zà-öø-ÿ])[^\W\d_])[\w'’&().-]*"
_DISCOVERY_NAME_RE = re.compile(
    rf"(?P<name>{_UNICODE_NAME_TOKEN}"
    rf"(?:\s+(?:and|et|Others|Autres|{_UNICODE_NAME_TOKEN})){{0,8}}"
    r"\s*\[?\s*(?:v\.?|c\.?|contre|against)\s+(?:the\s+|la\s+|le\s+)?"
    rf"{_UNICODE_NAME_TOKEN}"
    r"(?:\s+(?:and|et|the|of|de|du|des|la|le|Others|Autres|"
    rf"{_UNICODE_NAME_TOKEN})){{0,9}})",
)
_EXTERNAL_NUMBER_RE = re.compile(
    r"(?:\bCase\s+C\s*[-‑–]?|\bDirective\s+|\bRegulation\s+|"
    r"\b(?:Commission|Council)\s+Decision\s+|\bDecision\s+\d{4}/\d+|"
    r"\bIPT/|\bBvR\s+|\bCommunication\s+(?:no\.?\s*)?|"
    r"\bResolution\s+|\bOC[-‑–])[^.;]{0,45}$",
    re.I,
)
_MONTH_WORD = (
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|janvier|février|fevrier|mars|avril|mai|juin|juillet|"
    r"août|aout|septembre|octobre|novembre|décembre|decembre)"
)
_HISTORICAL_ENVELOPE_RE = re.compile(
    r"(?P<raw>(?:"
    r"(?:(?:the\s+)?(?:case\s+of\s+)?)"
    rf"(?P<en_name>{_UNICODE_NAME_TOKEN}[^.;\n]{{2,150}}?\s+(?:v\.?|c\.?|contre|against)\s+"
    r"[^.;\n]{2,100}?)\s+(?:judgment|decision)(?:\s+of)?\s+"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}}"
    r"|(?:arr[êe]t|d[ée]cision)\s+"
    rf"(?P<fr_name>{_UNICODE_NAME_TOKEN}[^.;\n]{{2,120}}?\s+"
    r"(?:v\.?|c\.?|contre|against)\s+[^.;\n]{2,100}?)\s+du\s+"
    rf"\d{{1,2}}(?:er)?\s+{_MONTH_WORD}\s+(?:19|20)\d{{2}})"
    r"(?:[^.;\n]{0,160}?(?:Series|s[ée]rie)\s+A\s+n(?:o\.?|°|º)\s*\d+"
    r"(?:[-‑–]\s*[A-Z])?)?)",
    re.I,
)
_SERIES_ONLY_RE = re.compile(
    r"\b(?:Series|s[ée]rie)\s+A\s+n(?:o\.?|°|º)\s*\d+(?:[-‑–]\s*[A-Z])?\b",
    re.I,
)


def _fold(value: str) -> str:
    value = value.replace("‑", "-").replace("–", "-").replace("\u2014", "-")
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def _unicode_case_name_valid(value: str) -> bool:
    """Require genuinely capitalised parties without limiting Unicode scripts."""
    value = re.sub(r"\[\s*(?=(?:v\.?|c\.?|contre|against)\b)", "", value, flags=re.I)
    parties = re.split(r"\s+(?:v\.?|c\.?|contre|against)\s+", value, maxsplit=1, flags=re.I)
    if len(parties) != 2:
        return False
    for index, party in enumerate(parties):
        candidate = party.strip(" [](),.")
        if index == 1:
            candidate = re.sub(r"^(?:the|la|le)\s+", "", candidate, flags=re.I)
        first_letter = next((char for char in candidate if char.isalpha()), "")
        if not first_letter or not first_letter.isupper():
            return False
    return True


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _fold(value)).strip()


def _match_key(value: str) -> str:
    """Normalize the extra Unicode letters matched by ``re.IGNORECASE``.

    Python treats ASCII ``I`` and Turkish dotless ``ı`` as case-insensitive
    equivalents.  Citation identity keeps using :func:`_key`; this broader key
    is only a necessary-token prefilter before the authoritative regex match.
    """
    return _key(value.replace("ı", "i"))


def _pattern(value: str) -> re.Pattern[str]:
    value = re.sub(r"([\[(])\s+", r"\1", value)
    value = re.sub(r"\s+([])])", r"\1", value)
    bits = [re.escape(bit) for bit in re.split(r"\s+", value.strip()) if bit]
    body = r"\s+".join(bits).replace(r"\-", r"[-‑–\u2014]")
    # Spine v1 used BeautifulSoup's space separator while flattening inline
    # markup.  That could materialise ``( re Crimea )`` even though the HTML
    # renders ``(re Crimea)``.  Keep the delimiters authoritative while
    # accepting optional whitespace immediately inside them so cached v1
    # spines remain searchable.
    body = (
        body.replace(r"\(", r"\(\s*")
        .replace(r"\)", r"\s*\)")
        .replace(r"\[", r"\[\s*")
        .replace(r"\]", r"\s*\]")
    )
    left = r"(?<!\w)" if value[:1].isalnum() else ""
    right = r"(?!\w)" if value[-1:].isalnum() else ""
    return re.compile(left + body + right, re.IGNORECASE)


def _anchor_token(value: str) -> str | None:
    """Return a necessary normalized token for a successful alias match."""
    tokens = _match_key(value).split()
    return max(tokens, key=len) if tokens else None


def _strip_phase(value: str) -> str:
    previous = ""
    while value != previous:
        previous = value
        value = _PHASE_RE.sub("", value).strip(" ,;")
    return value


def _distinctive_short_forms(name: str) -> list[str]:
    party = _PARTY_RE.split(_strip_phase(name), maxsplit=1)[0].strip(" ,")
    if not party:
        return []
    forms = [party]
    words = re.findall(r"[\wÀ-ÖØ-öø-ÿ'’-]+", party, re.UNICODE)
    if words:
        forms.append(words[-1])
    out: list[str] = []
    for value in forms:
        folded = _key(value)
        if len(folded) < 5 or folded in _GENERIC or folded in _STATE_WORDS:
            continue
        if value not in out:
            out.append(value)
    return out


def _party_name_variants(name: str) -> list[str]:
    """Generate close English/French connector variants without bare states."""
    match = _PARTY_RE.search(name)
    if match is None:
        return []
    applicant = name[:match.start()].strip()
    respondent = name[match.end():].strip()
    if not applicant or not respondent:
        return []
    return [
        f"{applicant} v. {respondent}",
        f"{applicant} v {respondent}",
        f"{applicant} c. {respondent}",
        f"{applicant} c {respondent}",
        f"{applicant} contre {respondent}",
    ]


@dataclass(frozen=True)
class _Owner:
    mention: CitationMention
    resolution: CitationResolution | None

    @property
    def identity(self) -> str:
        if self.resolution and self.resolution.target:
            return self.resolution.target.node_id
        return self.mention.mention_id


@dataclass(frozen=True)
class _Alias:
    text: str
    key: str
    owner: _Owner
    finder: str
    strong: bool


def _owners(
    case: Case, resolutions: Iterable[CitationResolution] | None
) -> list[_Owner]:
    """Return one owner for each authoritative SCL mention.

    Inclusive callers may supply resolutions for thousands of text-discovered
    envelopes as well as SCL mentions.  Seeding the gazetteer with every one
    of those envelopes makes repeated discoveries of the same authority look
    like competing owners, especially when one cached lookup is unresolved.
    SCL owns the baseline here; genuinely non-covered text discoveries are
    appended after identity coverage is evaluated in
    :func:`extract_citation_occurrences`.
    """
    supplied = {
        value.mention.mention_id: value for value in (resolutions or [])
    }
    return [
        _Owner(mention, supplied.get(mention.mention_id))
        for mention in parse_scl_mentions(case)
    ]


def _aliases(owner: _Owner) -> list[tuple[str, str, bool]]:
    mention = owner.mention
    values: list[tuple[str, str, bool]] = []

    def add(value: str | None, finder: str, strong: bool = True) -> None:
        value = (value or "").strip()
        if len(value) >= 5:
            values.append((value, finder, strong))

    add(mention.raw_ref, "scl_reference")
    add(mention.cited_name, "full_name")
    if mention.cited_name:
        phase_stripped = _strip_phase(mention.cited_name)
        add(phase_stripped, "full_name")
        for variant in _party_name_variants(phase_stripped):
            add(variant, "party_variant")
        date_match = _DATE_TEXT_RE.search(mention.raw_ref)
        if date_match:
            add(f"{phase_stripped}, {date_match.group()}", "name_date")
        for short in _distinctive_short_forms(mention.cited_name):
            add(short, "short_form", False)
    for appno in mention.explicit_appnos:
        add(appno, "application_number")
    add(mention.explicit_ecli, "ecli")
    if mention.reporter and mention.reporter.family == "series_a":
        add(mention.reporter.raw, "reporter")
    if owner.resolution and owner.resolution.target:
        target = owner.resolution.target
        add(target.docname, "target_name")
        for title in target.title_aliases:
            add(title, "target_name")
        for appno in target.appnos:
            add(appno, "application_number")
        add(target.ecli, "ecli")

    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, bool]] = []
    for value, finder, strong in values:
        key = _key(value)
        if not key or (key, finder) in seen:
            continue
        seen.add((key, finder))
        out.append((value, finder, strong))
    return out


def _gazetteer(owners: list[_Owner]) -> tuple[list[_Alias], dict[str, list[_Alias]]]:
    grouped: dict[str, list[_Alias]] = defaultdict(list)
    for owner in owners:
        for text, finder, strong in _aliases(owner):
            grouped[_key(text)].append(
                _Alias(text=text, key=_key(text), owner=owner, finder=finder, strong=strong)
            )
    accepted: list[_Alias] = []
    ambiguous: dict[str, list[_Alias]] = {}
    for key, entries in grouped.items():
        identities = {entry.owner.identity for entry in entries}
        if len(identities) > 1:
            ambiguous[key] = entries
            continue
        # Prefer the first SCL entry for duplicate aliases resolving to one target.
        chosen = sorted(entries, key=lambda value: (not value.strong, value.owner.mention.ordinal))[0]
        accepted.append(chosen)
    accepted.sort(key=lambda value: (-len(value.text), not value.strong, value.owner.mention.ordinal))
    return accepted, ambiguous


def _style_at(block: DocumentBlock, start: int, end: int) -> tuple[bool, bool]:
    runs = [run for run in block.inline_runs if run.start < end and run.end > start]
    return any(run.italic for run in runs), any(run.bold for run in runs)


def _component(block: DocumentBlock) -> Literal["majority", "opinion", "appendix"]:
    if block.opinion_id or block.section == "separate_opinion":
        return "opinion"
    if block.section == "appendix":
        return "appendix"
    return "majority"


def _evidence_offset(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def _coverage_matches(
    discovered: CitationMention,
    scl_mentions: list[CitationMention],
    resolutions: dict[str, CitationResolution],
) -> list[CitationMention]:
    appnos = set(discovered.explicit_appnos)
    name_key = _key(discovered.cited_name or "")
    discovered_target = resolutions.get(discovered.mention_id)
    discovered_node = (
        discovered_target.target.node_id
        if discovered_target and discovered_target.target else None
    )
    matches = []
    for mention in scl_mentions:
        scl_resolution = resolutions.get(mention.mention_id)
        scl_node = (
            scl_resolution.target.node_id
            if scl_resolution and scl_resolution.target else None
        )
        if discovered_node and scl_node:
            if discovered_node == scl_node:
                matches.append(mention)
            continue
        discovered_kind: str = discovered.document_kind
        discovered_phase = discovered.procedural_phase
        discovered_date = discovered.target_date
        if discovered_target and discovered_target.target:
            target = discovered_target.target
            if discovered_kind == "unknown":
                discovered_kind = target.document_kind
            if discovered_phase == "unknown":
                discovered_phase = target.procedural_phase
            discovered_date = discovered_date or target.date
        scl_kind: str = mention.document_kind
        scl_phase = mention.procedural_phase
        scl_date = mention.target_date
        if scl_resolution and scl_resolution.target:
            target = scl_resolution.target
            if scl_kind == "unknown":
                scl_kind = target.document_kind
            if scl_phase == "unknown":
                scl_phase = target.procedural_phase
            scl_date = scl_date or target.date
        if (
            discovered_kind != "unknown"
            and scl_kind != "unknown"
            and discovered_kind != scl_kind
        ):
            continue
        if (
            discovered_phase != "unknown"
            and scl_phase != "unknown"
            and discovered_phase != scl_phase
        ):
            continue
        if discovered_date and scl_date and discovered_date != scl_date:
            continue
        if appnos and appnos.intersection(mention.explicit_appnos):
            matches.append(mention)
            continue
        other = _key(mention.cited_name or "")
        if name_key and other and (name_key == other or name_key in other or other in name_key):
            matches.append(mention)
    return matches


def _discovery_spine(
    case: Case, *, html: str | None, spine: DocumentSpine | None
) -> DocumentSpine:
    if spine is not None:
        return spine
    if case.sections and case.sections.spine:
        return case.sections.spine
    source = html if html is not None else case.text or ""
    if html is not None or source.lstrip().startswith("<"):
        from ..text.segmentation import segment_html

        sections = segment_html(
            source,
            doctype=case.doctype,
            doctype_branch=case.doctype_branch,
            document_id=case.itemid,
        )
        return sections.spine or build_spine_from_html(source, document_id=case.itemid)
    from ..text.segmentation import segment_full

    sections = segment_full(
        source,
        doctype=case.doctype,
        doctype_branch=case.doctype_branch,
        document_id=case.itemid,
    )
    return sections.spine or build_spine_from_text(source, document_id=case.itemid)


def discover_citation_mentions(
    case: Case,
    *,
    html: str | None = None,
    spine: DocumentSpine | None = None,
) -> CitationDiscoveryResult:
    """Discover strong, name-bearing ECtHR citation envelopes without SCL."""
    spine = _discovery_spine(case, html=html, spine=spine)
    found: list[tuple[int, CitationMention]] = []
    diagnostics: list[dict[str, object]] = []
    source_appnos = set(case.appno)
    ordinal = 0
    series_catalog: dict[str, list[HistoricalCatalogEntry]] | None = None
    for block in spine.blocks:
        if (
            (
                block.type == "heading"
                and "\n" not in block.text
                and not APPNO_REGEX.search(block.text)
            )
            or block.heading_role == "frontmatter"
        ):
            continue
        claimed_app_spans: list[tuple[int, int]] = []
        for app_match in APPNO_REGEX.finditer(block.text):
            if any(start <= app_match.start() < end for start, end in claimed_app_spans):
                continue
            appno = app_match.group(1)
            prefix_start = max(0, app_match.start() - 260)
            prefix = block.text[prefix_start:app_match.start()]
            if appno in source_appnos:
                diagnostics.append({
                    "code": "self_reference", "source_itemid": case.itemid,
                    "block_id": block.block_id, "raw_text": appno,
                })
                continue
            if _EXTERNAL_NUMBER_RE.search(prefix):
                diagnostics.append({
                    "code": "external_identifier", "source_itemid": case.itemid,
                    "block_id": block.block_id, "raw_text": appno,
                })
                continue
            names = [
                match for match in _DISCOVERY_NAME_RE.finditer(prefix)
                if _unicode_case_name_valid(match.group("name"))
            ]
            if not names:
                diagnostics.append({
                    "code": "unanchored_application_number", "source_itemid": case.itemid,
                    "block_id": block.block_id, "raw_text": appno,
                })
                continue
            name_match = names[-1]
            if app_match.start() - (prefix_start + name_match.end()) > 100:
                diagnostics.append({
                    "code": "unanchored_application_number", "source_itemid": case.itemid,
                    "block_id": block.block_id, "raw_text": appno,
                })
                continue
            printed_name = name_match.group("name")
            # Do not let a preceding structural heading become part of the
            # authority merely because the HTML spine joined it with a
            # newline (for example ``THE LAW\nA v. France``).
            line_start = printed_name.rfind("\n") + 1
            leading = re.match(
                r"(?:(?:See|Compare|Contrast|And)\s+)+",
                printed_name[line_start:],
            )
            leading_chars = line_start + (leading.end() if leading else 0)
            start = prefix_start + name_match.start() + leading_chars
            cited_name = re.sub(
                r"\s*\[\s*(?=(?:v\.?|c\.?|contre|against)\b)",
                " ",
                printed_name[leading_chars:],
                flags=re.I,
            ).strip()
            tail_limit = min(len(block.text), app_match.end() + 180)
            tail = block.text[app_match.end():tail_limit]
            boundary = re.search(
                r"[;\n]|(?<=[.!?)])\s+(?=[A-Z])",
                tail,
            )
            boundary_offset = boundary.start() if boundary else len(tail)
            # A following authority owns its own envelope and any pinpoint
            # after it.  This is essential for ``A ... and B ..., § 42``.
            following_names = [
                match for match in _DISCOVERY_NAME_RE.finditer(tail)
                if _unicode_case_name_valid(match.group("name"))
            ]
            if following_names:
                boundary_offset = min(boundary_offset, following_names[0].start())
            end = app_match.end() + boundary_offset
            raw = block.text[start:end].strip(" ,")
            parsed_case = case.model_copy(update={"scl": raw, "sclappnos": []})
            parsed = parse_scl_mentions(parsed_case)
            if not parsed:
                continue
            mention = parsed[0]
            digest = hashlib.sha256(
                f"{case.itemid}|{block.block_id}|{start}|{end}|{normalize_reference(raw).casefold()}".encode()
            ).hexdigest()
            mention = mention.model_copy(update={
                "mention_id": digest,
                "reference_hash": hashlib.sha256(normalize_reference(raw).casefold().encode()).hexdigest(),
                "ordinal": ordinal,
                "origin": "text_discovery",
                "cited_name": cited_name,
                "respondent": extract_respondent(cited_name),
                "source_section": block.section,
                "source_block_id": block.block_id,
                "source_para_id": block.para_id,
                "source_opinion_id": block.opinion_id,
                "source_footnote_id": block.footnote_id,
                "source_invoking_block_ids": list(block.referenced_by_block_ids),
                "source_invoking_para_ids": list(block.referenced_by_para_ids),
                "discovery_evidence": {
                    "method": "name_application_number",
                    "block_start": start,
                    "block_end": end,
                    "application_start": app_match.start(),
                    "application_end": app_match.end(),
                },
            })
            found.append((block.char_start + start, mention))
            claimed_app_spans.append((start, end))
            ordinal += 1
        occupied = [
            (
                _evidence_offset(value.discovery_evidence.get("block_start")),
                _evidence_offset(value.discovery_evidence.get("block_end")),
            )
            for _, value in found
            if value.source_block_id == block.block_id
        ]
        for match in _HISTORICAL_ENVELOPE_RE.finditer(block.text):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            raw = match.group("raw").strip(" ,")
            printed_names = [
                candidate for candidate in _DISCOVERY_NAME_RE.finditer(raw)
                if _unicode_case_name_valid(candidate.group("name"))
            ]
            if not printed_names:
                continue
            printed = printed_names[-1]
            printed_name = printed.group("name")
            leading = re.match(
                r"(?:(?:See|Compare|Contrast|And|Voir)\s+)+(?:l['’])?(?:the\s+)?",
                printed_name,
                flags=re.I,
            )
            leading_chars = leading.end() if leading else 0
            actual_start = match.start() + printed.start() + leading_chars
            raw = block.text[actual_start:match.end()].strip(" ,")
            parsed_case = case.model_copy(update={"scl": raw, "sclappnos": []})
            parsed = parse_scl_mentions(parsed_case)
            if not parsed or not parsed[0].cited_name:
                continue
            mention = parsed[0]
            cited_name = printed_name[leading_chars:].strip()
            if match.group("fr_name"):
                cited_name = re.sub(r"\s+(?:du|de)$", "", cited_name, flags=re.I)
            digest = hashlib.sha256(
                f"{case.itemid}|{block.block_id}|{actual_start}|{match.end()}|"
                f"{normalize_reference(raw).casefold()}".encode()
            ).hexdigest()
            mention = mention.model_copy(update={
                "mention_id": digest,
                "reference_hash": hashlib.sha256(
                    normalize_reference(raw).casefold().encode()
                ).hexdigest(),
                "ordinal": ordinal,
                "origin": "text_discovery",
                "cited_name": cited_name,
                "respondent": extract_respondent(cited_name),
                "source_section": block.section,
                "source_block_id": block.block_id,
                "source_para_id": block.para_id,
                "source_opinion_id": block.opinion_id,
                "source_footnote_id": block.footnote_id,
                "source_invoking_block_ids": list(block.referenced_by_block_ids),
                "source_invoking_para_ids": list(block.referenced_by_para_ids),
                "discovery_evidence": {
                    "method": "historical_name_date",
                    "block_start": actual_start,
                    "block_end": match.end(),
                },
            })
            found.append((block.char_start + actual_start, mention))
            ordinal += 1
        occupied = [
            (
                _evidence_offset(value.discovery_evidence.get("block_start")),
                _evidence_offset(value.discovery_evidence.get("block_end")),
            )
            for _, value in found
            if value.source_block_id == block.block_id
        ]
        for method, pattern in (("exact_ecli", ECLI_REGEX), ("exact_itemid", ITEMID_REGEX)):
            for match in pattern.finditer(block.text):
                if any(match.start() < end and match.end() > start for start, end in occupied):
                    continue
                raw = match.group(1)
                if raw == case.itemid or raw == case.ecli:
                    diagnostics.append({
                        "code": "self_reference",
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "raw_text": raw,
                    })
                    continue
                parsed = parse_scl_mentions(
                    case.model_copy(update={"scl": raw, "sclappnos": []})
                )
                if not parsed:
                    continue
                digest = hashlib.sha256(
                    f"{case.itemid}|{block.block_id}|{match.start()}|{match.end()}|"
                    f"{raw.casefold()}".encode()
                ).hexdigest()
                mention = parsed[0].model_copy(update={
                    "mention_id": digest,
                    "reference_hash": hashlib.sha256(raw.casefold().encode()).hexdigest(),
                    "ordinal": ordinal,
                    "origin": "text_discovery",
                    "source_section": block.section,
                    "source_block_id": block.block_id,
                    "source_para_id": block.para_id,
                    "source_opinion_id": block.opinion_id,
                    "source_footnote_id": block.footnote_id,
                    "source_invoking_block_ids": list(block.referenced_by_block_ids),
                    "source_invoking_para_ids": list(block.referenced_by_para_ids),
                    "discovery_evidence": {
                        "method": method,
                        "block_start": match.start(),
                        "block_end": match.end(),
                    },
                })
                found.append((block.char_start + match.start(), mention))
                occupied.append((match.start(), match.end()))
                ordinal += 1
        for match in _SERIES_ONLY_RE.finditer(block.text):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            reporter = parse_reporter(match.group())
            if reporter is None:
                continue
            if series_catalog is None:
                from .catalog import load_historical_catalog

                series_catalog = defaultdict(list)
                for entry in load_historical_catalog().entries:
                    series_catalog[entry.reporter_key].append(entry)
            entries = series_catalog.get(reporter.key, [])
            identities = {
                getattr(entry, "target_ecli", None) or getattr(entry, "target_itemid", None)
                for entry in entries
            } - {None}
            if len(identities) != 1:
                diagnostics.append({
                    "code": "ambiguous_reporter",
                    "source_itemid": case.itemid,
                    "block_id": block.block_id,
                    "raw_text": match.group(),
                    "candidate_count": len(identities),
                })
                continue
            entry = next(
                value for value in entries
                if (getattr(value, "target_ecli", None) or getattr(value, "target_itemid", None))
                in identities
            )
            raw = match.group()
            parsed = parse_scl_mentions(
                case.model_copy(update={"scl": raw, "sclappnos": []})
            )
            if not parsed:
                continue
            digest = hashlib.sha256(
                f"{case.itemid}|{block.block_id}|{match.start()}|{match.end()}|"
                f"{raw.casefold()}".encode()
            ).hexdigest()
            mention = parsed[0].model_copy(update={
                "mention_id": digest,
                "reference_hash": hashlib.sha256(raw.casefold().encode()).hexdigest(),
                "ordinal": ordinal,
                "origin": "text_discovery",
                "cited_name": getattr(entry, "title", None),
                "explicit_ecli": getattr(entry, "target_ecli", None),
                "explicit_itemid": getattr(entry, "target_itemid", None),
                "explicit_appnos": list(getattr(entry, "appnos", [])),
                "target_date": getattr(entry, "date", None),
                "document_kind": getattr(entry, "document_kind", "unknown"),
                "source_section": block.section,
                "source_block_id": block.block_id,
                "source_para_id": block.para_id,
                "source_opinion_id": block.opinion_id,
                "source_footnote_id": block.footnote_id,
                "source_invoking_block_ids": list(block.referenced_by_block_ids),
                "source_invoking_para_ids": list(block.referenced_by_para_ids),
                "discovery_evidence": {
                    "method": "unique_series_a",
                    "block_start": match.start(),
                    "block_end": match.end(),
                    "reporter_key": reporter.key,
                },
            })
            found.append((block.char_start + match.start(), mention))
            ordinal += 1
    found.sort(key=lambda value: (value[0], value[1].mention_id))
    blocks = {block.block_id: block for block in spine.blocks}
    preliminary: list[CitationOccurrence] = []
    for _, mention in found:
        source_block = blocks.get(mention.source_block_id or "")
        if source_block is None:
            continue
        evidence = mention.discovery_evidence
        start = _evidence_offset(evidence.get("block_start"))
        end = _evidence_offset(evidence.get("block_end"), start)
        italic, bold = _style_at(source_block, start, end)
        method = str(evidence.get("method", "text_discovery"))
        occurrence_id = hashlib.sha256(
            f"{case.itemid}|{source_block.block_id}|{start}|{end}|{mention.mention_id}".encode()
        ).hexdigest()
        preliminary.append(CitationOccurrence(
            occurrence_id=occurrence_id,
            mention_id=mention.mention_id,
            source_itemid=case.itemid,
            source_language=case.language,
            source_section=source_block.section,
            source_block_id=source_block.block_id,
            source_para_id=source_block.para_id,
            source_para_num=source_block.para_num,
            block_start=start,
            block_end=end,
            document_start=source_block.char_start + start,
            document_end=source_block.char_start + end,
            raw_text=source_block.text[start:end],
            source_context=source_block.text,
            italic=italic,
            bold=bold,
            finder=method,
            evidence={"discovery_evidence": evidence},
            target_paragraphs=[],
            source_component=_component(source_block),
            source_opinion_id=source_block.opinion_id,
            source_opinion_ordinal=source_block.opinion_ordinal,
            source_opinion_type=source_block.opinion_type,
            source_opinion_authors=list(source_block.opinion_authors),
            source_opinion_joined_by=list(source_block.opinion_joined_by),
            source_footnote_id=source_block.footnote_id,
            source_invoking_block_ids=list(source_block.referenced_by_block_ids),
            source_invoking_para_ids=list(source_block.referenced_by_para_ids),
            scl_coverage="indeterminate",
            discovery_methods=[method],
            resolution_scope="unresolved",
        ))
    _assign_owned_pinpoints(preliminary, blocks, diagnostics)
    return CitationDiscoveryResult(
        mentions=[value[1] for value in found],
        preliminary_occurrences=preliminary,
        rejected_candidates=diagnostics,
        diagnostics=diagnostics,
    )


def _is_article_pinpoint(text: str, start: int) -> bool:
    before = text[max(0, start - 40):start]
    return bool(
        re.search(r"(?:Article|Protocol|Rule|Articles)\s+\d+(?:\s*§\s*\d+)?\s*$", before, re.I)
    )


def _first_owned_pinpoint(text: str) -> re.Match[str] | None:
    return next(
        (
            match for match in _PIN_RE.finditer(text)
            if not _is_article_pinpoint(text, match.start())
        ),
        None,
    )


def _pinpoint_labels(match: re.Match[str]) -> list[str]:
    """Parse labels without swallowing a following citation date as a label."""
    labels: list[str] = []
    group = match.group("labels")
    for label_match in _LABEL_RE.finditer(group):
        absolute_end = match.start("labels") + label_match.end()
        following = match.string[absolute_end:absolute_end + 24]
        if labels and re.match(rf"\s+{_MONTH_WORD}\b", following, re.I):
            break
        labels.append(label_match.group())
    return labels


def _target_paragraphs(
    text: str,
    start: int,
    end: int,
    *,
    next_citation_start: int | None = None,
) -> tuple[list[str], str | None]:
    """Return only pinpoints owned by this citation envelope.

    A pinpoint inside the matched citation wins. Otherwise scanning stops at
    the next accepted citation, semicolon, newline, or sentence boundary. It
    never looks backwards, because that lets the preceding authority steal a
    following authority's pinpoint.
    """
    if match := _first_owned_pinpoint(text[start:end]):
        return _pinpoint_labels(match), "inside_envelope"
    limit = min(len(text), end + 240)
    if next_citation_start is not None:
        limit = min(limit, next_citation_start)
    after = text[end:limit]
    boundaries = [
        match.start()
        for pattern in (r"[;\n]", r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ])")
        if (match := re.search(pattern, after))
    ]
    if boundaries:
        after = after[:min(boundaries)]
    if match := _first_owned_pinpoint(after):
        return _pinpoint_labels(match), "following_envelope"
    return [], None


def _assign_owned_pinpoints(
    occurrences: list[CitationOccurrence],
    blocks: dict[str, DocumentBlock],
    diagnostics: list[dict[str, object]],
) -> None:
    grouped: dict[str, list[CitationOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        grouped[occurrence.source_block_id].append(occurrence)
    for block_id, values in grouped.items():
        block = blocks.get(block_id)
        if block is None:
            continue
        values.sort(key=lambda value: (value.block_start, value.block_end))
        for index, occurrence in enumerate(values):
            next_start = values[index + 1].block_start if index + 1 < len(values) else None
            labels, source = _target_paragraphs(
                block.text,
                occurrence.block_start,
                occurrence.block_end,
                next_citation_start=next_start,
            )
            occurrence.target_paragraphs = labels
            occurrence.evidence["pinpoint_source"] = source
            occurrence.evidence["pinpoint_boundary"] = next_start
            if not labels and next_start is not None:
                intervening = block.text[occurrence.block_end:next_start]
                if _PIN_RE.search(intervening):
                    diagnostics.append({
                        "code": "unowned_pinpoint",
                        "source_itemid": occurrence.source_itemid,
                        "block_id": block_id,
                        "occurrence_id": occurrence.occurrence_id,
                        "raw_text": intervening,
                    })


def _duplicates_named_anchor(
    candidate: tuple[int, int, _Alias, bool, bool],
    candidates: list[tuple[int, int, _Alias, bool, bool]],
) -> bool:
    """Return whether a machine identifier is part of an already named cite."""
    start, end, alias, _, _ = candidate
    if alias.finder not in {"application_number", "ecli", "reporter"}:
        return False
    return any(
        other.owner.identity == alias.owner.identity
        and other.finder in {"full_name", "target_name"}
        and other_start <= start
        and start - other_end <= 200
        for other_start, other_end, other, _, _ in candidates
    )


def _context(text: str, start: int, end: int, radius: int = 180) -> str:
    return text[max(0, start - radius):min(len(text), end + radius)]


def _all_caps(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return len(letters) >= 8 and all(char.isupper() for char in letters)


def _unmatched_candidates(
    case: Case,
    block: DocumentBlock,
    known_spans: list[tuple[int, int]],
) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    seen: set[tuple[int, int, str]] = set()
    for finder, pattern in _UNMATCHED_ANCHORS:
        for match in pattern.finditer(block.text):
            start, end = match.span()
            if any(start < known_end and end > known_start for known_start, known_end in known_spans):
                continue
            identity = (start, end, finder)
            if identity in seen:
                continue
            seen.add(identity)
            italic, bold = _style_at(block, start, end)
            diagnostics.append(
                {
                    "code": "unmatched_candidate",
                    "source_itemid": case.itemid,
                    "block_id": block.block_id,
                    "para_id": block.para_id,
                    "block_start": start,
                    "block_end": end,
                    "document_start": block.char_start + start,
                    "document_end": block.char_start + end,
                    "raw_text": block.text[start:end],
                    "finder": finder,
                    "italic": italic,
                    "bold": bold,
                }
            )
    return diagnostics


def _occurrence(
    case: Case,
    block: DocumentBlock,
    alias: _Alias,
    start: int,
    end: int,
    *,
    italic: bool,
    bold: bool,
) -> CitationOccurrence:
    target = alias.owner.resolution.target if alias.owner.resolution else None
    raw = block.text[start:end]
    digest = hashlib.sha256(
        f"{case.itemid}|{block.block_id}|{start}|{end}|{alias.owner.mention.mention_id}".encode()
    ).hexdigest()
    return CitationOccurrence(
        occurrence_id=digest,
        mention_id=alias.owner.mention.mention_id,
        source_itemid=case.itemid,
        source_language=case.language,
        source_section=block.section,
        source_block_id=block.block_id,
        source_para_id=block.para_id,
        source_para_num=block.para_num,
        block_start=start,
        block_end=end,
        document_start=block.char_start + start,
        document_end=block.char_start + end,
        raw_text=raw,
        source_context=block.text,
        italic=italic,
        bold=bold,
        finder=alias.finder,
        evidence={
            "alias": alias.text,
            "strong": alias.strong,
            "typography": "italic" if italic else "bold" if bold else "plain",
            "scl_reference": alias.owner.mention.raw_ref,
            "mention_reference_hash": alias.owner.mention.reference_hash,
            "mention_ordinal": alias.owner.mention.ordinal,
            "target_identity": alias.owner.identity,
            "discovery_evidence": alias.owner.mention.discovery_evidence,
            "citation_cue_present": bool(
                _CUE_RE.search(_context(block.text, start, end, radius=80))
            ),
            "resolution_status": (
                alias.owner.resolution.status if alias.owner.resolution else None
            ),
            "resolution_method": (
                alias.owner.resolution.method if alias.owner.resolution else None
            ),
            "short_form_gate": (
                "italic" if not alias.strong and italic
                else "cue_or_prior_strong" if not alias.strong
                else None
            ),
        },
        target_node_id=target.node_id if target else None,
        target_ecli=target.ecli if target else None,
        target_itemid=target.itemid if target else None,
        target_appnos=list(target.appnos) if target else [],
        target_paragraphs=[],
        source_component=_component(block),
        source_opinion_id=block.opinion_id,
        source_opinion_ordinal=block.opinion_ordinal,
        source_opinion_type=block.opinion_type,
        source_opinion_authors=list(block.opinion_authors),
        source_opinion_joined_by=list(block.opinion_joined_by),
        source_footnote_id=block.footnote_id,
        source_invoking_block_ids=list(block.referenced_by_block_ids),
        source_invoking_para_ids=list(block.referenced_by_para_ids),
        scl_coverage=(
            "not_covered" if alias.owner.mention.origin == "text_discovery" else "covered"
        ),
        scl_mention_ids=(
            [] if alias.owner.mention.origin == "text_discovery"
            else [alias.owner.mention.mention_id]
        ),
        discovery_methods=[alias.finder],
        resolution_scope=(
            "document" if target and target.node_id else
            "application" if (
                alias.owner.resolution
                and alias.owner.resolution.candidates
                and any(
                    set(candidate.appnos).intersection(alias.owner.mention.explicit_appnos)
                    for candidate in alias.owner.resolution.candidates
                )
            ) else "unresolved"
        ),
        resolution_candidates=(
            [
                {
                    "node_id": candidate.node_id,
                    "itemid": candidate.itemid,
                    "ecli": candidate.ecli,
                    "docname": candidate.docname,
                    "appnos": list(candidate.appnos),
                    "date": candidate.date.isoformat() if candidate.date else None,
                    "document_kind": candidate.document_kind,
                }
                for candidate in alias.owner.resolution.candidates
            ]
            if alias.owner.resolution else []
        ),
    )


def extract_citation_occurrences(
    case: Case,
    resolutions: Iterable[CitationResolution] | None = None,
    *,
    html: str | None = None,
    spine: DocumentSpine | None = None,
    scope: str = "scl",
) -> CitationOccurrenceResult:
    """Locate SCL authorities in source paragraphs without an LLM.

    ``resolutions`` supplies authoritative target identities.  When omitted,
    occurrences still link to parsed SCL mention IDs but remain unresolved.
    """
    if scope not in {"scl", "inclusive"}:
        raise ValueError("scope must be 'scl' or 'inclusive'")
    spine = _discovery_spine(case, html=html, spine=spine)
    resolution_values = list(resolutions or [])
    owners = _owners(case, resolution_values)
    scl_mentions = parse_scl_mentions(case)
    discovery = (
        discover_citation_mentions(case, html=html, spine=spine)
        if scope == "inclusive" else CitationDiscoveryResult()
    )
    supplied_by_mention = {
        value.mention.mention_id: value for value in resolution_values
    }
    coverage = {
        mention.mention_id: _coverage_matches(
            mention, scl_mentions, supplied_by_mention
        )
        for mention in discovery.mentions
    }
    for mention in discovery.mentions:
        if coverage[mention.mention_id]:
            continue
        owners.append(_Owner(mention, supplied_by_mention.get(mention.mention_id)))
    aliases, ambiguous = _gazetteer(owners)
    prepared_aliases = [
        (alias, _pattern(alias.text), _anchor_token(alias.text))
        for alias in aliases
    ]
    prepared_ambiguous = [
        (key, entries, _pattern(entries[0].text), _anchor_token(entries[0].text))
        for key, entries in ambiguous.items()
    ]
    occurrences: list[CitationOccurrence] = []
    diagnostics: list[dict[str, object]] = list(discovery.diagnostics)
    established: set[str] = set()
    ambiguous_hits = 0
    unmatched_hits = 0

    for block in spine.blocks:
        if (
            (
                block.type == "heading"
                and "\n" not in block.text
                and not APPNO_REGEX.search(block.text)
            )
            or block.heading_role == "frontmatter"
        ):
            continue
        if str(block.para_id or "").startswith("pre-") or _all_caps(block.text):
            continue

        block_tokens = set(_match_key(block.text).split())
        candidates: list[tuple[int, int, _Alias, bool, bool]] = []
        raw_candidates: list[tuple[int, int, _Alias, bool, bool]] = []
        for alias, pattern, anchor in prepared_aliases:
            if anchor is not None and anchor not in block_tokens:
                continue
            for match in pattern.finditer(block.text):
                italic, bold = _style_at(block, match.start(), match.end())
                raw_candidates.append((match.start(), match.end(), alias, italic, bold))

        for start, end, alias, italic, bold in raw_candidates:
            nearby = _context(block.text, start, end, radius=80)
            earlier_strong = any(
                other.strong
                and other.owner.identity == alias.owner.identity
                and other_end <= start
                for _, other_end, other, _, _ in raw_candidates
            )
            if not alias.strong and not (
                italic
                or _CUE_RE.search(nearby)
                or alias.owner.identity in established
                or earlier_strong
            ):
                continue
            raw = block.text[start:end]
            if (
                not alias.strong
                and " " not in raw.strip()
                and raw[:1].islower()
            ):
                continue
            candidates.append((start, end, alias, italic, bold))

        for key, entries, pattern, anchor in prepared_ambiguous:
            if anchor is not None and anchor not in block_tokens:
                continue
            for match in pattern.finditer(block.text):
                ambiguous_hits += 1
                diagnostics.append(
                    {
                        "code": "ambiguous_alias",
                        "source_itemid": case.itemid,
                        "block_id": block.block_id,
                        "para_id": block.para_id,
                        "block_start": match.start(),
                        "block_end": match.end(),
                        "raw_text": block.text[match.start():match.end()],
                        "alias_key": key,
                        "mention_ids": sorted({entry.owner.mention.mention_id for entry in entries}),
                    }
                )
                raw_candidates.append((match.start(), match.end(), entries[0], False, False))

        unmatched = _unmatched_candidates(
            case,
            block,
            [(start, end) for start, end, _, _, _ in raw_candidates],
        )
        unmatched_hits += len(unmatched)
        diagnostics.extend(unmatched)

        # At one locus, retain the longest/highest-confidence alias only.
        candidates.sort(key=lambda item: (item[0], not item[2].strong, -(item[1] - item[0])))
        accepted_spans: list[tuple[int, int]] = []
        for start, end, alias, italic, bold in candidates:
            if any(start < old_end and end > old_start for old_start, old_end in accepted_spans):
                continue
            if _duplicates_named_anchor((start, end, alias, italic, bold), candidates):
                continue
            occurrence = _occurrence(
                case, block, alias, start, end, italic=italic, bold=bold
            )
            occurrences.append(occurrence)
            accepted_spans.append((start, end))
            if alias.strong:
                established.add(alias.owner.identity)

    # SCL-covered text envelopes may differ substantially from the printed SCL
    # form. Add the exact envelope when the ordinary gazetteer did not already
    # occupy that locus, retaining the SCL identity and provenance.
    blocks = {block.block_id: block for block in spine.blocks}
    resolution_by_scl = {
        value.mention.mention_id: value for value in resolution_values
    }
    for mention in discovery.mentions:
        matches = coverage[mention.mention_id]
        if not matches:
            continue
        evidence = mention.discovery_evidence
        discovery_block = blocks.get(mention.source_block_id or "")
        if discovery_block is None:
            continue
        start_value = evidence.get("block_start", 0)
        end_value = evidence.get("block_end", start_value)
        start = start_value if isinstance(start_value, int) else 0
        end = end_value if isinstance(end_value, int) else start
        overlapping = next(
            (
                value for value in occurrences
                if value.source_block_id == discovery_block.block_id
                and start < value.block_end and end > value.block_start
            ),
            None,
        )
        ids = sorted(value.mention_id for value in matches)
        if overlapping is not None:
            overlapping.scl_coverage = "covered"
            overlapping.scl_mention_ids = sorted(set([*overlapping.scl_mention_ids, *ids]))
            overlapping.discovery_methods = sorted(
                set([
                    *overlapping.discovery_methods,
                    str(mention.discovery_evidence.get("method", "text_discovery")),
                ])
            )
            continue
        selected = matches[0]
        alias = _Alias(
            text=mention.raw_ref,
            key=_key(mention.raw_ref),
            owner=_Owner(selected, resolution_by_scl.get(selected.mention_id)),
            finder=str(mention.discovery_evidence.get("method", "text_discovery")),
            strong=True,
        )
        italic, bold = _style_at(discovery_block, start, end)
        value = _occurrence(
            case, discovery_block, alias, start, end, italic=italic, bold=bold
        )
        value.scl_mention_ids = ids
        value.discovery_methods = [
            str(mention.discovery_evidence.get("method", "text_discovery")),
            "scl_identity",
        ]
        occurrences.append(value)

    _assign_owned_pinpoints(occurrences, blocks, diagnostics)
    occurrences.sort(key=lambda value: (value.document_start, value.document_end, value.mention_id))
    located = {value.mention_id for value in occurrences}
    for owner in owners:
        if owner.mention.mention_id not in located:
            diagnostics.append(
                {
                    "code": "unlocated_mention",
                    "source_itemid": case.itemid,
                    "mention_id": owner.mention.mention_id,
                    "raw_reference": owner.mention.raw_ref,
                }
            )
    methods = Counter(value.finder for value in occurrences)
    components = Counter(value.source_component for value in occurrences)
    sections = Counter(value.source_section or "unknown" for value in occurrences)
    inclusive_edges: list[dict[str, object]] = []
    edge_rows: dict[tuple[str, str], list[CitationOccurrence]] = defaultdict(list)
    for value in occurrences:
        if value.target_node_id and value.resolution_scope == "document":
            edge_rows[(case.itemid or "", value.target_node_id)].append(value)
    for (source, target), values in sorted(edge_rows.items()):
        inclusive_edges.append({
            "source": source,
            "target": target,
            "occurrence_count": len(values),
            "mention_count": len({value.mention_id for value in values}),
            "scl_covered_occurrence_count": sum(
                value.scl_coverage == "covered" for value in values
            ),
            "text_only_occurrence_count": sum(
                value.scl_coverage == "not_covered" for value in values
            ),
            "occurrence_ids": [value.occurrence_id for value in values],
        })
    scl_ids = {value.mention_id for value in scl_mentions}
    located_scl = located.intersection(scl_ids)
    report = CitationOccurrenceReport(
        documents=1,
        scl_mentions=len(scl_mentions),
        occurrences=len(occurrences),
        located_mentions=len(located_scl),
        unlocated_mentions=max(0, len(scl_mentions) - len(located_scl)),
        ambiguous_hits=ambiguous_hits,
        unmatched_candidates=unmatched_hits,
        methods=dict(methods),
        text_discovered_mentions=len(discovery.mentions),
        text_only_occurrences=sum(
            value.scl_coverage == "not_covered" for value in occurrences
        ),
        scl_covered_occurrences=sum(
            value.scl_coverage == "covered" for value in occurrences
        ),
        components={str(key): value for key, value in components.items()},
        sections={str(key): value for key, value in sections.items()},
    )
    return CitationOccurrenceResult(
        occurrences=occurrences,
        report=report,
        diagnostics=diagnostics,
        mentions=[*scl_mentions, *discovery.mentions],
        inclusive_edges=inclusive_edges,
    )
