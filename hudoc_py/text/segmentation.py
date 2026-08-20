"""Segment a HUDOC judgment text into canonical sections.

Two granularities:

* :func:`segment_main_sections` – fast simple split: ``the_law`` plus
  ``dispositif`` only.
* :func:`segment_full` – rich nine-section split with doctype-aware
  routing, multiple layout variants per section, and a confidence score.

Sections and their markers (English / French, all matched case-insensitively
with multiple layout variants – inline, standalone header, flattened
text, Roman-numeral-prefixed, etc.):

============== ============================================================
Section         Markers
============== ============================================================
procedure       PROCEDURE / THE PROCEDURE / PROCEEDINGS BEFORE THE
                COMMISSION AND THE COURT / FACTS AND PROCEDURE /
                PROCEDURE AND FACTS / PROCÉDURE / LA PROCÉDURE
facts           THE FACTS / AS TO THE FACTS / FACTS OF THE CASE /
                THE CIRCUMSTANCES OF THE CASE / EN FAIT / LES FAITS /
                LES CIRCONSTANCES DE L'AFFAIRE / LES CIRCONSTANCES
                DE L'ESPÈCE
subject_matter  SUBJECT MATTER OF THE CASE (Committee judgments)
                OBJET DE L'AFFAIRE
complaints      COMPLAINTS / ALLEGED VIOLATION(S) (OF) / GRIEFS /
                VIOLATIONS ALLÉGUÉES
the_law         THE LAW / AS TO THE LAW / RELEVANT LEGAL FRAMEWORK
                (AND PRACTICE) / EN DROIT / LE DROIT / SUR LE DROIT /
                QUESTIONS TO THE PARTIES (for communicated cases)
court_assessment THE COURT'S ASSESSMENT (Committee judgments)
operative       FOR THESE REASONS / ON THESE GROUNDS /
                PAR CES MOTIFS / POUR CES MOTIFS
separate_opinion SEPARATE / DISSENTING / CONCURRING / JOINT (PARTLY)
                DISSENTING OPINION / OPINION SÉPARÉE / DISSIDENTE /
                CONCORDANTE / PARTIELLEMENT DISSIDENTE
appendix        APPENDIX / APPENDICES / ANNEX / ANNEXES / ANNEXE
============== ============================================================

Doctype routing: info notes (``CLIN``, ``CLINF``) and press releases
(``PR``) are not real judgments and segment to an empty Sections with
``doctype_mode="info_note"`` or ``"press_release"``. Communicated cases
(``HECOM``, ``HFCOM``) and old Commission decisions (``HEDEC``/``HFDEC``
with branch ``DECCOMMISSION``/``ADMISSIBILITYCOM``) are tagged so
callers know which template applies.
"""

from __future__ import annotations

import hashlib
import re

from ..models.common import (
    DocumentSpine,
    Sections,
    SectionSpan,
    SegmentationDiagnostic,
)
from .spine import build_spine_from_html, build_spine_from_text, spine_text

# ---------------------------------------------------------------------------
# Doctype routing
# ---------------------------------------------------------------------------
INFO_NOTE_DOCTYPES = frozenset({"CLIN", "CLINF"})
PRESS_RELEASE_DOCTYPES = frozenset({"PR"})
JUDGMENT_DOCTYPES = frozenset({"HEJUD", "HFJUD"})
COMMUNICATED_DOCTYPES = frozenset({"HECOM", "HFCOM"})
COMMUNICATED_BRANCHES = frozenset({"COMMUNICATEDCASES"})
COMMISSION_DECISION_BRANCHES = frozenset({"DECCOMMISSION", "ADMISSIBILITYCOM"})

# Content-sniff patterns when metadata isn't conclusive.
_SNIFF_INFO_NOTE = re.compile(
    r"Information\s+Note\s+on\s+the\s+Court[’']s\s+case[\s-]?law"
    r"|Note\s+d[’']information\s+sur\s+la\s+jurisprudence",
    re.IGNORECASE,
)
_SNIFF_PRESS_RELEASE = re.compile(
    r"\bPress\s+release\b|\bCOMMUNIQU[ÉE]\s+DE\s+PRESSE\b", re.IGNORECASE
)
_SNIFF_COMMUNICATED = re.compile(
    r"(?:Communicated\s+on|Communiqu[ée]e?\s+le|OBJET\s+DE\s+L[’']AFFAIRE)",
    re.IGNORECASE,
)


def _route_doctype(text: str, doctype: str | None, doctype_branch: str | None) -> str:
    if doctype and doctype.upper() in INFO_NOTE_DOCTYPES:
        return "info_note"
    if doctype and doctype.upper() in PRESS_RELEASE_DOCTYPES:
        return "press_release"
    if (doctype and doctype.upper() in COMMUNICATED_DOCTYPES) or (
        doctype_branch and doctype_branch.upper() in COMMUNICATED_BRANCHES
    ):
        return "communicated_case"
    if doctype_branch and doctype_branch.upper() in COMMISSION_DECISION_BRANCHES:
        return "commission_decision"
    # Explicit judgment metadata is authoritative. Historical judgments can
    # discuss or quote a "press release"/"communiqué de presse" in their body;
    # content sniffing must not reclassify those records.
    if doctype and doctype.upper() in JUDGMENT_DOCTYPES:
        return "judgment"
    if _SNIFF_INFO_NOTE.search(text or ""):
        return "info_note"
    if _SNIFF_PRESS_RELEASE.search(text or ""):
        return "press_release"
    if _SNIFF_COMMUNICATED.search(text or ""):
        return "communicated_case"
    return "judgment"


# ---------------------------------------------------------------------------
# Simple-split patterns (kept for the fast path)
# ---------------------------------------------------------------------------
_BODY_RE = re.compile(r"^\s*(THE\s+LAW|EN\s+DROIT)\b", re.IGNORECASE | re.MULTILINE)
_DISPOSITIF_RE = re.compile(
    r"^\s*(FOR\s+THESE\s+REASONS|ON\s+THESE\s+GROUNDS|PAR\s+CES\s+MOTIFS)\b",
    re.IGNORECASE | re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Rich-split patterns. Each section has several variants. ``IM`` = inline-mid
# (e.g. modern flattened text where the heading appears after a period and
# before a digit/Roman numeral); ``H`` = standalone header line. The
# Roman-numeral prefix ``(?:[IVX]+\.\s+)?`` and an optional ``THE/LA``
# are absorbed into each pattern. Patterns are tried in declaration order
# and the first match wins (per section).
# ---------------------------------------------------------------------------
_ROMAN = r"(?:[IVX]+\.\s+)?"
# A character class for "right after a sentence-ish boundary"
_BEFORE = r"(?<=[\.\)\s:;”“\"])"
# What can follow a section header inline (number, roman, capital, line break,
# or a comma/semicolon separator before the same – e.g. "PAR CES MOTIFS, LA COUR")
_AFTER = r"(?=[\s,;:.\u2014–\-]*(?:\d|[IVX]+\.|[A-Z]|\n))"


def _block(*labels: str) -> str:
    """Build a non-capturing alternation."""
    return r"(?:" + "|".join(labels) + r")"


# English variants
_EN_PROCEDURE = _block(
    r"(?:THE\s+)?PROCEDURE",
    r"PROCEEDINGS\s+BEFORE\s+THE\s+COMMISSION(?:\s+AND\s+THE\s+COURT)?",
    r"FACTS\s+AND\s+PROCEDURE",
    r"PROCEDURE\s+AND\s+FACTS",
)
_EN_FACTS = _block(
    r"(?:AS\s+TO\s+)?THE\s+FACTS",
    r"FACTS\s+OF\s+THE\s+CASE",
    r"(?:THE\s+)?CIRCUMSTANCES\s+OF\s+THE\s+CASE",
)
_EN_SUBJECT_MATTER = r"SUBJECT\s+MATTER\s+OF\s+THE\s+CASE"
_EN_COMPLAINTS = _block(
    # Real headers are plural ("COMPLAINTS"). Singular "complaint" appears
    # as prose ("The complaint is admissible") and must not match.
    r"(?:THE\s+)?COMPLAINTS",
    r"ALLEGED\s+VIOLATIONS(?:\s+OF)?",
)
_EN_LAW = _block(
    r"(?:AS\s+TO\s+)?THE\s+LAW",
    r"RELEVANT\s+LEGAL\s+FRAMEWORK(?:\s+AND\s+PRACTICE)?",
    r"QUESTIONS?\s+TO\s+THE\s+PARTIES",
)
# Strict variants used for the word-boundary fallback only. "LE DROIT" alone
# is too permissive – French judgments routinely use it as a sub-heading
# inside THE FACTS for "domestic law applicable". Restrict the fallback to
# unambiguous markers.
_EN_LAW_STRICT = _block(
    r"(?:AS\s+TO\s+)?THE\s+LAW",
    r"RELEVANT\s+LEGAL\s+FRAMEWORK(?:\s+AND\s+PRACTICE)?",
)
_EN_COURT_ASSESSMENT = r"THE\s+COURT[’‘']S\s+ASSESSMENT"
_EN_OPERATIVE = _block(
    r"FOR\s+THE(?:SE)?\s+(?:ABOVE\s+|FOREGOING\s+)?REAS\s*O\s*NS",
    r"ON\s+THESE\s+GROUNDS",
    r"NOW\s+THEREFORE\s+THE\s+(?:COURT|COMMISSION)",
)
_EN_SEPARATE = _block(
    r"(?:JOINT\s+)?(?:PARTLY|PARTIALLY)\s+(?:CONCURRING|DISSENTING),\s*"
    r"(?:PARTLY|PARTIALLY)\s+(?:CONCURRING|DISSENTING)\s+OPINION",
    r"(?:JOINT\s+)?(?:PARTLY\s+|PARTIALLY\s+)?(?:CONCURRING|DISSENTING)\s+OPINION",
    r"SEPARATE\s+OPINIONS?",
    r"SEPARATE\s+JOINT\s+CONCURRING\s+OPINION",
    r"DECLARATIONS?\s+OF\s+(?:THE\s+)?JUDGES?\b",
)
_EN_APPENDIX = _block(r"APPENDIX(?:ES)?", r"ANNEX(?:ES)?")

# French variants
_FR_PROCEDURE = _block(
    r"(?:LA\s+)?PROC(?:[ÉE]|É|é)DURE",
    r"PROC(?:[ÉE]|É|é)DURE\s+DEVANT\s+LA\s+COMMISSION",
    r"PROC(?:[ÉE]|É|é)DURE\s+ET\s+FAITS",
    r"FAITS\s+ET\s+PROC(?:[ÉE]|É|é)DURE",
)
_FR_FACTS = _block(
    r"EN\s+FAIT",
    r"FAITS",
    r"LES\s+FAITS",
    r"LES\s+CIRCONSTANCES\s+DE\s+L[’']\s*(?:AFFAIRE|ESP[ÈE]CE)",
)
_FR_SUBJECT_MATTER = r"OBJET\s+DE\s+L[’']\s*AFFAIRE"
_FR_COMPLAINTS = _block(
    # Real headers are plural ("GRIEFS"). "grief" singular is prose.
    r"(?:LES\s+)?GRIEFS",
    r"VIOLATIONS?\s+ALL[ÉE]GU[ÉE]ES",
)
_FR_LAW = _block(
    r"EN\s+DROIT",
    r"(?:LE|SUR\s+LE)\s+DROIT",
    r"QUESTIONS?\s+AUX\s+PARTIES",
)
_FR_LAW_STRICT = _block(r"EN\s+DROIT", r"SUR\s+LE\s+DROIT")
_FR_OPERATIVE = _block(r"PAR\s+CES\s+MOTIFS", r"POUR\s+CES\s+MOTIFS")
_FR_SEPARATE = _block(
    r"OPINIONS?\s+S[ÉE]PAR[ÉE]ES?(?:\s+SUIVANTES)?",
    # Optional "EN PARTIE"/"PARTIELLEMENT" before either type – real GC output
    # includes e.g. "OPINION PARTIELLEMENT CONCORDANTE ET PARTIELLEMENT
    # DISSIDENTE DE M. LE JUGE GARLICKI" (001-69023).
    r"OPINION\s+(?:COMMUNE\s+)?(?:EN\s+PARTIE\s+|PARTIELLEMENT\s+)?"
    r"(?:CONCORDANTE|DISSIDENTE)",
    r"D[ÉE]CLARATIONS?\s+(?:DU\s+JUGE|DE\s+LA\s+JUGE|DES\s+JUGES|DE\s+M(?:ME|M)?\b)",
)
_FR_APPENDIX = r"ANNEXE"


def _section_re(
    en_token: str | None, fr_token: str | None, *, ignorecase: bool = True
) -> re.Pattern[str]:
    """Compile a multi-variant pattern for a section.

    Three layouts (in priority order – earliest match wins):

      H_strict: standalone header line – section name on its own line.
                ``OPINION DISSIDENTE\\n``
      H_loose:  line starts with section name, trailing text allowed.
                ``OPINION DISSIDENTE DE M. TÜRMEN\\n`` – common in
                French Grand Chamber output.
      F:        flattened text after a sentence-ish boundary, with
                content following.
                ``...dispositif.  PAR CES MOTIFS, LA COUR  1. Dit...``

    No "word anywhere" fallback – that's too noisy because words like
    "annexes" or "le droit" appear in normal prose. Doctype routing
    handles the cases where headers don't appear at all (info notes,
    press releases).
    """
    tokens = [t for t in (en_token, fr_token) if t]
    alt = _block(*tokens) if len(tokens) > 1 else tokens[0]
    parts = [
        # H_strict: section name on a line by itself
        rf"(?:^|\n)\s*{_ROMAN}{alt}\s*(?:\n|$)",
        # H_loose: line starts with the marker (trailing text allowed)
        rf"(?:^|\n)\s*{_ROMAN}{alt}\b",
        # F: flattened (mid-paragraph after sentence boundary)
        rf"{_BEFORE}{_ROMAN}{alt}{_AFTER}",
    ]
    return re.compile("|".join(parts), re.IGNORECASE if ignorecase else 0)


_RICH_PATTERNS: dict[str, re.Pattern[str]] = {
    "procedure": _section_re(_EN_PROCEDURE, _FR_PROCEDURE),
    "facts": _section_re(_EN_FACTS, _FR_FACTS),
    # These are literal judgment headers. Case-sensitive matching prevents
    # ordinary prose in a long opinion ("the subject matter", "complaints")
    # from becoming a later section boundary.
    "subject_matter": _section_re(_EN_SUBJECT_MATTER, _FR_SUBJECT_MATTER, ignorecase=False),
    "complaints": _section_re(_EN_COMPLAINTS, _FR_COMPLAINTS, ignorecase=False),
    # the_law uses _FR_LAW_STRICT (no bare "LE DROIT") to avoid matching the
    # "domestic law applicable" sub-heading inside FACTS in French judgments.
    "the_law": _section_re(_EN_LAW_STRICT, _FR_LAW_STRICT, ignorecase=False),
    # This heading is title case in some templates, so match case-insensitively
    # but require the entire standalone line rather than the loose/prose forms.
    "court_assessment": re.compile(rf"(?:^|\n)\s*{_EN_COURT_ASSESSMENT}\s*(?:\n|$)", re.IGNORECASE),
    "operative": _section_re(_EN_OPERATIVE, _FR_OPERATIVE, ignorecase=False),
    # separate_opinion and appendix match case-SENSITIVELY: their markers
    # routinely occur lowercase in prose right before the real headings
    # ("the joint dissenting opinion of Judges X and Y is annexed to this
    # judgment"), which used to truncate the opinions block (live-verified
    # on Jeunesse, 001-147117). Real headings are uppercase.
    "separate_opinion": _section_re(_EN_SEPARATE, _FR_SEPARATE, ignorecase=False),
    "appendix": _section_re(_EN_APPENDIX, _FR_APPENDIX, ignorecase=False),
}


def _structural_re(
    en_token: str | None,
    fr_token: str | None,
    *,
    suffix: str = r"\s*",
) -> re.Pattern[str]:
    """Compile a whole-block heading pattern for an established spine block.

    This deliberately excludes the flattened-text fallbacks in
    :func:`_section_re`. A subsection such as ``C. The Code of Criminal
    Procedure`` must not become a second top-level PROCEDURE boundary merely
    because it contains a canonical word.
    """
    tokens = [token for token in (en_token, fr_token) if token]
    alt = _block(*tokens) if len(tokens) > 1 else tokens[0]
    return re.compile(rf"\s*{_ROMAN}{alt}{suffix}", re.IGNORECASE)


# These patterns are evaluated only for blocks already supported as headings
# by source structure, formatting, outline grammar, or exact ECtHR grammar.
_STRUCTURAL_PATTERNS = {
    "procedure": _structural_re(_EN_PROCEDURE, _FR_PROCEDURE),
    "facts": _structural_re(_EN_FACTS, _FR_FACTS),
    "subject_matter": _structural_re(_EN_SUBJECT_MATTER, _FR_SUBJECT_MATTER),
    "complaints": _structural_re(_EN_COMPLAINTS, _FR_COMPLAINTS),
    "the_law": _structural_re(_EN_LAW, _FR_LAW),
    "court_assessment": _structural_re(_EN_COURT_ASSESSMENT, None),
    "operative": _structural_re(_EN_OPERATIVE, _FR_OPERATIVE, suffix=r"(?:\s*,.*)?\s*"),
    "separate_opinion": _structural_re(
        _EN_SEPARATE,
        _FR_SEPARATE,
        suffix=r"(?:\s+.*)?\s*",
    ),
    "appendix": _structural_re(
        _EN_APPENDIX,
        _FR_APPENDIX,
        suffix=r"(?:\s+.*)?\s*",
    ),
}
_STRUCTURAL_EXTRA = {
    "facts": re.compile(r"^(?:FAITS|STATEMENT\s+OF\s+FACTS)$", re.IGNORECASE),
    "complaints": re.compile(r"^(?:COMPLAINT|GRIEF)$", re.IGNORECASE),
    "the_law": re.compile(
        r"^(?:QUESTIONS?\s+TO\s+THE\s+PARTIES|QUESTIONS?\s+AUX\s+PARTIES)$",
        re.IGNORECASE,
    ),
}

# Canonical reading order – sections appear in judgments in this sequence.
# subject_matter and court_assessment are used by post-2018 Committee judgments
# (simplified template); when present they replace facts/the_law respectively.
_CANONICAL_ORDER = (
    "procedure",
    "facts",
    "subject_matter",
    "complaints",
    "the_law",
    "court_assessment",
    "operative",
    "separate_opinion",
    "appendix",
)


def segment_main_sections(text: str) -> Sections:
    """Fast simple-split: returns a :class:`Sections` with ``full``,
    ``the_law``, and ``dispositif`` only.

    Use when you just need "reasoning" vs "operative part" – the rich
    fields stay ``None`` and ``confidence`` is 0 (the fast path doesn't
    score itself).
    """
    if not text:
        return Sections()

    body_match = _BODY_RE.search(text)
    disp_match = _DISPOSITIF_RE.search(text)

    the_law: str | None = None
    dispositif: str | None = None

    if body_match:
        body_start = body_match.start()
        body_end = disp_match.start() if disp_match else len(text)
        the_law = text[body_start:body_end].strip() or None

    if disp_match:
        dispositif = text[disp_match.start() :].strip() or None

    return Sections(full=text, the_law=the_law, dispositif=dispositif, doctype_mode="judgment")


def _expected_slots(mode: str, found: set[str]) -> list[set[str]]:
    """Return template-aware alternative slots required for a complete split."""
    if mode == "communicated_case":
        return [{"facts", "subject_matter"}, {"complaints", "the_law"}]
    if mode == "commission_decision":
        return [{"facts", "procedure", "subject_matter"}, {"the_law", "operative"}]
    if "subject_matter" in found or "court_assessment" in found:
        return [{"subject_matter"}, {"court_assessment"}, {"operative"}]
    return [{"the_law"}, {"operative"}]


def _attach_structure(
    *,
    spine: DocumentSpine,
    positions: dict[str, int],
    slices: dict[str, str],
    mode: str,
) -> tuple[list[SectionSpan], list[SegmentationDiagnostic], str, float]:
    """Attach canonical section labels, spans, diagnostics, and quality state."""
    diagnostics = list(spine.diagnostics)
    found_order = sorted(positions, key=positions.__getitem__)
    spans: list[SectionSpan] = []

    for index, name in enumerate(found_order):
        start = positions[name]
        end = (
            positions[found_order[index + 1]]
            if index + 1 < len(found_order)
            else (spine.blocks[-1].char_end if spine.blocks else start)
        )
        start_block = next(
            (
                block_index
                for block_index, block in enumerate(spine.blocks)
                if block.char_end > start
            ),
            0,
        )
        end_block = next(
            (
                block_index
                for block_index, block in enumerate(spine.blocks)
                if block.char_start >= end
            ),
            len(spine.blocks),
        )
        heading_block = spine.blocks[start_block]
        heading_block.type = "heading"
        heading_block.heading_level = heading_block.heading_level or 1
        heading_block.heading_role = (
            "operative"
            if name == "operative"
            else "separate_opinion"
            if name == "separate_opinion"
            else "appendix"
            if name == "appendix"
            else "section"
        )
        if "canonical_ecthr_heading" not in heading_block.heading_source:
            heading_block.heading_source.append("canonical_ecthr_heading")

        paragraph_ids: list[str] = []
        for block in spine.blocks[start_block:end_block]:
            block.section = name  # type: ignore[assignment]
            if block.para_id:
                paragraph_ids.append(block.para_id)
        spans.append(
            SectionSpan(
                section=name,  # type: ignore[arg-type]
                heading=heading_block.text,
                char_start=heading_block.char_start,
                char_end=end,
                start_block=start_block,
                end_block=end_block,
                paragraph_ids=paragraph_ids,
            )
        )
        section_text = slices.get(name, "")
        if section_text and len(section_text) < 60:
            diagnostics.append(
                SegmentationDiagnostic(
                    code="short_section",
                    severity="info",
                    message=(
                        f"The {name} slice is only {len(section_text)} characters; "
                        "inspect the recorded span if this is unexpected."
                    ),
                    section=name,  # type: ignore[arg-type]
                    block_index=start_block,
                    char_start=heading_block.char_start,
                    char_end=end,
                )
            )

    # Several canonical-looking headings before the selected first boundary
    # are characteristic of a table of contents. Preserve them as evidence,
    # but do not let them masquerade as body sections.
    if spans:
        first_body_block = min(span.start_block for span in spans)
        toc_candidates = [
            block
            for block in spine.blocks[:first_body_block]
            if "canonical_ecthr_heading" in block.heading_source
        ]
        if len(toc_candidates) >= 2:
            for block in toc_candidates:
                block.heading_role = "toc_entry"
            diagnostics.append(
                SegmentationDiagnostic(
                    code="table_of_contents_candidates",
                    severity="info",
                    message=(
                        f"Preserved {len(toc_candidates)} canonical-looking front-matter "
                        "headings as table-of-contents entries."
                    ),
                )
            )

    found = set(found_order)
    slots = _expected_slots(mode, found)
    satisfied = sum(bool(slot & found) for slot in slots)
    for slot in slots:
        if slot & found:
            continue
        label = " or ".join(sorted(slot))
        diagnostics.append(
            SegmentationDiagnostic(
                code="missing_expected_section",
                severity="warning",
                message=f"No {label} boundary was found for the {mode} template.",
            )
        )

    if not found:
        status = "unsegmented"
        confidence = 0.0
    else:
        status = "complete" if satisfied == len(slots) else "partial"
        core_score = satisfied / len(slots) if slots else 1.0
        breadth_score = min(len(found) / 5.0, 1.0)
        source_score = 1.0 if spine.source_format == "hudoc_html" else 0.75
        short_count = sum(d.code == "short_section" for d in diagnostics)
        confidence = round(
            max(
                0.0,
                min(
                    1.0,
                    core_score * 0.65
                    + breadth_score * 0.2
                    + source_score * 0.15
                    - short_count * 0.03,
                ),
            ),
            3,
        )

    spine.diagnostics = diagnostics
    return spans, diagnostics, status, confidence


def _find_in_canonical_order(
    text: str,
    spine: DocumentSpine | None = None,
    *,
    mode: str = "judgment",
) -> dict[str, int]:
    """Find section positions, enforcing canonical reading order.

    For each section (in canonical order), pick the earliest occurrence
    that comes AFTER the previous section's chosen position. This rejects
    prose mentions of section names (e.g. "ses annexes" mid-paragraph
    before a real APPENDIX header), because they violate canonical
    ordering relative to already-found sections.
    """
    has_source_blocks = spine is not None and len(spine.blocks) > 1
    matches: dict[str, list[int]] = {}
    for name, pattern in _RICH_PATTERNS.items():
        # Structured input is deliberately block-first: flattened regex
        # matches are not candidates. This prevents a styled subsection such
        # as "C. The Code of Criminal Procedure" or a short quotation from
        # being promoted because it contains a canonical phrase.
        matches[name] = [] if has_source_blocks else [m.start() for m in pattern.finditer(text)]

    if spine is not None:
        for block in spine.blocks:
            if block.type != "heading":
                continue
            normalized = " ".join(block.text.split())
            for name, pattern in _STRUCTURAL_PATTERNS.items():
                if pattern.fullmatch(normalized):
                    matches[name].append(block.char_start)
            for name, pattern in _STRUCTURAL_EXTRA.items():
                if pattern.fullmatch(" ".join(block.text.split())):
                    matches[name].append(block.char_start)
        for name in matches:
            matches[name] = sorted(set(matches[name]))

    # Source-aware hardening: in real HUDOC HTML, each Word paragraph is an
    # explicit block. A phrase such as "the facts of the case" inside a body
    # paragraph cannot be a section boundary, even if a permissive flattened-
    # text regex can see it. Keep the legacy fallback only when the input has
    # collapsed into a single block.
    if has_source_blocks and spine is not None:
        position_to_block: dict[int, int] = {}
        for positions_for_name in matches.values():
            for position in positions_for_name:
                block_index = next(
                    (
                        index
                        for index, block in enumerate(spine.blocks)
                        if block.char_start <= position < block.char_end
                        or (
                            position <= block.char_start
                            and not text[position : block.char_start].strip()
                        )
                    ),
                    -1,
                )
                position_to_block[position] = block_index
        for name, positions_for_name in matches.items():
            matches[name] = [
                position
                for position in positions_for_name
                if position_to_block.get(position, -1) >= 0
                and spine.blocks[position_to_block[position]].type == "heading"
            ]

        # Contents pages repeat canonical headings before the body. Suppress a
        # pre-body candidate only when the same section has a later candidate.
        # Historical Commission decisions often put several real sections
        # before their first numbered paragraph, so density alone is not proof
        # of a table of contents.
        first_substantive = spine.first_substantive_block_index
        if first_substantive is not None:
            for name, positions_for_name in matches.items():
                by_heading: dict[str, list[int]] = {}
                for position in positions_for_name:
                    block_index = position_to_block.get(position, -1)
                    if block_index < 0:
                        continue
                    normalized = " ".join(spine.blocks[block_index].text.upper().split())
                    by_heading.setdefault(normalized, []).append(position)
                discarded: set[int] = set()
                for duplicate_positions in by_heading.values():
                    if len(duplicate_positions) < 2:
                        continue
                    # Contents entries and the body heading have the same text.
                    # Keep the last duplicate nearest the substantive content.
                    discarded.update(duplicate_positions[:-1])
                if discarded:
                    matches[name] = [
                        position for position in positions_for_name if position not in discarded
                    ]

    # "ALLEGED VIOLATION(S)" is both an old top-level complaints marker and
    # a very common subheading inside THE LAW. Once an explicit law header is
    # present, later matches cannot be the earlier complaints section.
    if matches.get("the_law"):
        first_law = matches["the_law"][0]
        matches["complaints"] = [
            position for position in matches["complaints"] if position < first_law
        ]

    # There is no single canonical order for historical Commission decisions
    # or communicated cases. FACTS, COMPLAINTS, PROCEDURE, LAW is common in the
    # former; an ANNEX can precede QUESTIONS TO THE PARTIES in the latter. At
    # this point every candidate is an independently identified source heading,
    # so preserve source order.
    if (
        mode in {"commission_decision", "communicated_case"}
        and spine is not None
        and len(spine.blocks) > 1
    ):
        return {name: values[0] for name, values in matches.items() if values}

    positions: dict[str, int] = {}
    last_pos = -1
    for name in _CANONICAL_ORDER:
        for pos in matches.get(name, []):
            if pos > last_pos:
                positions[name] = pos
                last_pos = pos
                break
    return positions


def segment_full(
    text: str,
    *,
    doctype: str | None = None,
    doctype_branch: str | None = None,
    document_id: str | None = None,
    _spine: DocumentSpine | None = None,
) -> Sections:
    """Rich nine-section split with doctype routing and confidence scoring.

    Pass ``doctype`` (e.g. ``"HEJUD"``, ``"CLIN"``) and ``doctype_branch``
    (e.g. ``"GRANDCHAMBER"``, ``"COMMUNICATEDCASES"``) from your :class:`Case`
    to let the segmenter route correctly. Info notes and press releases
    return an empty :class:`Sections` with ``doctype_mode`` set – they
    aren't judgments and shouldn't be parsed with judgment patterns.

    Section markers must appear in canonical reading order: an apparent
    APPENDIX before the operative section is rejected as a prose mention
    of the word "annexe" / "annexes", not a real header.
    """
    if not text:
        return Sections()

    spine = _spine or build_spine_from_text(text, document_id=document_id)
    mode = _route_doctype(text, doctype, doctype_branch)
    if mode in ("info_note", "press_release"):
        return Sections(
            full=text,
            doctype_mode=mode,
            confidence=1.0,
            status="not_applicable",
            spine=spine,
        )

    positions = _find_in_canonical_order(text, spine, mode=mode)
    if not positions:
        diagnostic = SegmentationDiagnostic(
            code="no_section_boundaries",
            severity="error",
            message="No canonical ECtHR section boundary was found.",
        )
        spine.diagnostics.append(diagnostic)
        return Sections(
            full=text,
            doctype_mode=mode,
            confidence=0.0,
            status="unsegmented",
            diagnostics=[diagnostic],
            spine=spine,
        )

    # Slice in canonical order to avoid overlaps.
    found_in_order = sorted(positions, key=positions.__getitem__)
    slices: dict[str, str] = {}
    for i, name in enumerate(found_in_order):
        start = positions[name]
        end = positions[found_in_order[i + 1]] if i + 1 < len(found_in_order) else len(text)
        slice_text = text[start:end].strip()
        if slice_text:
            slices[name] = slice_text

    spans, diagnostics, status, confidence = _attach_structure(
        spine=spine,
        positions=positions,
        slices=slices,
        mode=mode,
    )

    # Footnote bodies are separately addressable spine content. HUDOC places
    # them at the physical end of the converted Word document, where flattening
    # would otherwise append majority footnotes to the final opinion (or the
    # operative section). Keep canonical flat section fields footnote-free;
    # callers can join through ``spine.footnotes`` explicitly.
    for section_name in list(slices):
        section_blocks = [
            block.text
            for block in spine.blocks
            if block.section == section_name and block.type != "footnote"
        ]
        if section_blocks:
            slices[section_name] = "\n\n".join(section_blocks)

    # Split the combined separate-opinions block into individual opinions.
    from .opinions import split_opinions_report

    opinion_report = split_opinions_report(slices.get("separate_opinion"))
    from .composition import extract_bench_composition

    bench = extract_bench_composition(text, spine=spine)
    opinions = []
    opinion_cursor = positions.get("separate_opinion", 0)
    for ordinal, opinion in enumerate(opinion_report.opinions, 1):
        start = text.find(opinion.raw_header, opinion_cursor)
        if start < 0:
            start = text.find(opinion.text[:80], opinion_cursor)
        if start < 0:
            start = opinion_cursor
        end = min(len(text), start + len(opinion.text))
        start_block = next(
            (i for i, block in enumerate(spine.blocks) if block.char_end > start),
            len(spine.blocks),
        )
        end_block = next(
            (i for i, block in enumerate(spine.blocks) if block.char_start >= end),
            len(spine.blocks),
        )
        identity = hashlib.sha256(
            f"{document_id or ''}|{opinion.raw_header.casefold()}|{start}|{ordinal}".encode()
        ).hexdigest()[:20]
        opinion_id = f"{document_id or 'document'}:opinion:{identity}"
        enriched = opinion.model_copy(
            update={
                "opinion_id": opinion_id,
                "ordinal": ordinal,
                "char_start": start,
                "char_end": end,
                "start_block": start_block,
                "end_block": end_block,
            }
        )
        opinions.append(enriched)
        for block in spine.blocks[start_block:end_block]:
            if block.section != "separate_opinion":
                continue
            block.opinion_id = opinion_id
            block.opinion_ordinal = ordinal
            block.opinion_type = opinion.opinion_type
            block.opinion_authors = list(opinion.authors)
            block.opinion_joined_by = list(opinion.joined_by)
        opinion_cursor = end

    # Footnote bodies are physically stored after the judgment/opinions in
    # HUDOC's converted Word HTML. Their legal context comes from the inline
    # reference, not that terminal position.
    from .spine import link_footnote_context

    link_footnote_context(spine)
    for span in spans:
        span.paragraph_ids = [
            block.para_id
            for block in spine.blocks[span.start_block:span.end_block]
            if block.type != "footnote" and block.para_id is not None
        ]

    return Sections(
        full=text,
        doctype_mode=mode,
        confidence=confidence,
        status=status,  # type: ignore[arg-type]
        diagnostics=diagnostics,
        spans=spans,
        spine=spine,
        procedure=slices.get("procedure"),
        facts=slices.get("facts"),
        subject_matter=slices.get("subject_matter"),
        complaints=slices.get("complaints"),
        the_law=slices.get("the_law"),
        court_assessment=slices.get("court_assessment"),
        operative=slices.get("operative"),
        dispositif=slices.get("operative"),  # alias for backwards compat
        separate_opinion=slices.get("separate_opinion"),
        opinions=opinions,
        opinions_confidence=opinion_report.confidence,
        opinion_diagnostics=opinion_report.diagnostics,
        bench=bench,
        appendix=slices.get("appendix"),
    )


def segment_html(
    html: str,
    *,
    doctype: str | None = None,
    doctype_branch: str | None = None,
    document_id: str | None = None,
) -> Sections:
    """Segment HUDOC HTML through a source-aware document spine.

    Unlike ``html_to_text(html)`` followed by :func:`segment_full`, this path
    retains paragraph tags, generated Word styles, heading evidence, and stable
    local paragraph IDs.  Canonical flat section fields remain available for
    backwards compatibility.
    """
    spine = build_spine_from_html(html, document_id=document_id)
    text = spine_text(spine)
    return segment_full(
        text,
        doctype=doctype,
        doctype_branch=doctype_branch,
        document_id=document_id,
        _spine=spine,
    )
