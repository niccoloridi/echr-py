"""Parse HUDOC ``scl`` and ``externalsources`` fields into typed structures."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from ..models import Case
from ..models.citation import Citation

# Application numbers look like 12345/67. Pre-1998 cases (Series A) often
# don't appear in this form in citations and will simply not match – those
# refs end up unresolved in the missing_refs bucket of CitationGraph.
APPNO_REGEX = re.compile(r"\b(\d{3,5}/\d{2})\b")

# Heuristic case-name extractor: everything up to the first comma, "[GC]",
# or appno marker. Handles "X v. Y" (English) and "X c. Y" (French).
_NAME_RE = re.compile(
    r"^\s*(?P<name>[^,\[]+?\s+(?:v\.|c\.)\s+[^,\[]+?)"
    r"\s*(?:,|\[GC\]|\(|no\b|nos?\.|requ[êe]te)",
    re.IGNORECASE,
)


def _extract_name(raw_ref: str) -> str | None:
    """Best-effort case-name capture from a single SCL fragment."""
    m = _NAME_RE.match(raw_ref)
    if m:
        return m.group("name").strip()
    # Fallback: take the chunk before the first comma if it contains "v." or "c."
    head = raw_ref.split(",", 1)[0].strip()
    if " v. " in head or " c. " in head:
        return head
    return None


def parse_scl(scl: str | None) -> list[tuple[str, str | None, list[str]]]:
    """Split a raw ``scl`` field into ``(raw_ref, cited_name, cited_appnos)`` triples.

    This is the low-level parser used by :func:`extract_citations`.
    """
    if not scl:
        return []
    fragments = [frag.strip() for frag in str(scl).split(";") if frag.strip()]
    out: list[tuple[str, str | None, list[str]]] = []
    for raw in fragments:
        clean = re.sub(r"\s+", " ", raw)
        appnos = list(dict.fromkeys(APPNO_REGEX.findall(clean)))  # dedup, preserve order
        name = _extract_name(clean)
        out.append((clean, name, appnos))
    return out


def extract_citations(case: Case) -> list[Citation]:
    """Return a list of unresolved :class:`Citation` objects for one case.

    Citations come from the case's ``scl`` field. ``resolved`` is False
    until they are processed by :class:`CitationGraph`.
    """
    citations: list[Citation] = []
    for raw, name, appnos in parse_scl(case.scl):
        citations.append(
            Citation(
                source_itemid=case.itemid,
                source_appno=list(case.appno),
                raw_ref=raw,
                cited_name=name,
                cited_appnos=appnos,
            )
        )
    return citations


# ---------------------------------------------------------------------------
# HUDOC "International Law" field (``externalsources``)
#
# This field is the Court's own inventory of non-Strasbourg authority relied on
# in a judgment: treaties, Council of Europe and UN instruments, CPT reports,
# and decisions of other tribunals. It is deliberately kept OUT of resolution:
# it never supplies the application number or date that ECtHR resolution needs.
#
# It is used only to *refuse* — never to assert. A match is evidence that a
# printed name belongs to a non-ECtHR authority and must not be promoted to a
# HUDOC document; a miss proves nothing, because the field is sparse (populated
# for roughly a quarter of judgments) and not exhaustive where present.
# ---------------------------------------------------------------------------

# Respondent-state and jurisdiction words. A candidate supported only by these
# must never match: "D. v. the United Kingdom" must not attach to an unrelated
# United Kingdom entry.
_EXTERNAL_STATE_WORDS = frozenset(
    (
        "albania", "andorra", "armenia", "austria", "azerbaijan", "belgium", "bosnia",
        "bulgaria", "croatia", "cyprus", "czech", "denmark", "estonia", "finland", "france",
        "georgia", "germany", "greece", "herzegovina", "hungary", "iceland", "ireland",
        "italy", "latvia", "liechtenstein", "lithuania", "luxembourg", "malta", "moldova",
        "monaco", "montenegro", "netherlands", "norway", "poland", "portugal", "romania",
        "russia", "serbia", "slovakia", "slovenia", "spain", "sweden", "switzerland", "turkey",
        "turkiye", "ukraine", "kingdom", "britain", "states", "america", "brazil", "uruguay",
        "macedonia", "republic", "federation",
    )
)

_EXTERNAL_STOPWORDS = frozenset(
    (
        "and", "others", "case", "cases", "judgment", "judgments", "decision", "decisions",
        "the", "of", "in", "on", "from", "application", "applications", "court", "european",
        "human", "rights", "committee", "general", "comment", "report", "resolution",
        "recommendation", "article", "articles", "paragraph", "rule", "rules", "against",
        "inter", "american", "nations", "united",
    )
)

# If an entry names the Strasbourg Court itself it is not external authority.
_STRASBOURG_MARKERS = (
    "european court of human rights",
    "cour europeenne des droits de l'homme",
    "cour europeenne des droits de l homme",
    "european commission of human rights",
)


def _external_fold(value: str) -> str:
    """Casefold, strip accents, and collapse whitespace for robust comparison."""
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped.casefold()).strip()


def parse_external_sources(external_sources: str | None) -> list[str]:
    """Split HUDOC's ``externalsources`` field into normalised entries.

    The field uses the same semicolon-delimited shape as ``scl``. Order is
    preserved and duplicates are dropped.
    """
    if not external_sources:
        return []
    entries: list[str] = []
    for fragment in str(external_sources).split(";"):
        clean = re.sub(r"\s+", " ", fragment).strip()
        if clean and clean not in entries:
            entries.append(clean)
    return entries


def _external_distinctive_tokens(name: str) -> set[str]:
    """Tokens that can carry identity: not stopwords, not bare state names."""
    folded = _external_fold(name)
    tokens = re.findall(r"[a-z]{4,}", folded)
    return {
        token
        for token in tokens
        if token not in _EXTERNAL_STOPWORDS and token not in _EXTERNAL_STATE_WORDS
    }


def match_external_source(name: str | None, entries: Sequence[str]) -> str | None:
    """Return the International Law entry attributing *name* to a non-ECtHR body.

    Conservative by construction: every distinctive token of *name* must appear
    in a single entry, a name carrying no distinctive token never matches, and
    entries naming the Strasbourg Court are excluded. Returns ``None`` when
    there is no confident match.
    """
    if not name:
        return None
    distinctive = _external_distinctive_tokens(name)
    if not distinctive:
        return None
    for entry in entries:
        folded_entry = _external_fold(entry)
        if any(marker in folded_entry for marker in _STRASBOURG_MARKERS):
            continue
        if all(token in folded_entry for token in distinctive):
            return entry
    return None


def external_source_authority(case: Case, name: str | None) -> str | None:
    """Match *name* against the case's own HUDOC International Law field.

    Returns the matching entry, suitable as an abstention reason, or ``None``.
    """
    return match_external_source(name, parse_external_sources(case.external_sources))
