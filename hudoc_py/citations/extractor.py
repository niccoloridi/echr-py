"""Parse HUDOC ``scl`` fields into typed :class:`Citation` objects."""

from __future__ import annotations

import re

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
