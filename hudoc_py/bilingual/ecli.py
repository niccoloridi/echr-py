"""ECLI normalization and duplicate-cluster classification.

Pure functions (no I/O). Consolidates two historical duplicate-detection
approaches into :func:`classify_ecli_cluster`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..models.case import Case

ClusterKind = Literal[
    "unique",
    "same_language_duplicate",
    "matched_by_appno_docname",
    "ambiguous",
]

#: Minimum SequenceMatcher ratio for two normalized docnames to count as the
#: same case when appno-sets already agree.
DOCNAME_SIMILARITY_THRESHOLD = 0.85

# Party / joiner / boilerplate tokens stripped from docnames before comparison,
# so that "CASE OF A v. FRANCE" (EN) and "AFFAIRE A c. FRANCE" (FR) normalize to
# the same string. The "CASE OF"/"AFFAIRE" doc-type prefixes differ by language
# and are pure boilerplate, so they are dropped too.
_PARTY_TOKENS = (
    r"CASE\s+OF",
    r"AFFAIRE",
    r"CONTRE",
    r"AGAINST",
    r"V\.?",
    r"C\.?",
    r"THE",
    r"LES",
    r"LE",
    r"LA",
    r"L['’]",
)
_PARTY_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(_PARTY_TOKENS) + r")\b", flags=re.IGNORECASE
)
_NON_ALNUM_RE = re.compile(r"[^0-9A-Za-zÀ-ɏ]+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_ecli(ecli: str | None) -> str | None:
    """Upper-case and strip *all* whitespace; return ``None`` for empty input."""
    if not ecli:
        return None
    collapsed = re.sub(r"\s+", "", ecli).upper()
    return collapsed or None


def normalize_docname(name: str | None) -> str:
    """Normalize a case title for cross-language comparison.

    Upper-cases, removes party/joiner tokens (``v.``/``c.``/``the``/``le`` ...),
    replaces punctuation with spaces, and collapses whitespace.
    """
    if not name:
        return ""
    text = name.upper()
    text = _PARTY_TOKEN_RE.sub(" ", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_appnos(appnos: Iterable[str]) -> frozenset[str]:
    """Return the set of non-empty, stripped, upper-cased application numbers."""
    return frozenset(a.strip().upper() for a in appnos if a and a.strip())


def docname_similarity(a: str | None, b: str | None) -> float:
    """SequenceMatcher ratio over normalized docnames; 0.0 if either is empty."""
    na, nb = normalize_docname(a), normalize_docname(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def classify_ecli_cluster(cases: Sequence[Case]) -> ClusterKind:
    """Classify a group of cases sharing one normalized ECLI.

    * ``unique`` – 0 or 1 case.
    * ``same_language_duplicate`` – all cases share one language (a HUDOC
      re-index, not a translation pair).
    * ``matched_by_appno_docname`` – multiple languages whose appno-sets are
      identical and whose docnames either match exactly or exceed
      :data:`DOCNAME_SIMILARITY_THRESHOLD`.
    * ``ambiguous`` – anything else.
    """
    if len(cases) <= 1:
        return "unique"

    languages = {(c.language or "").upper() for c in cases}
    if len(languages) <= 1:
        return "same_language_duplicate"

    appno_sets = {normalize_appnos(c.appno) for c in cases}
    if len(appno_sets) == 1:
        docnames = {normalize_docname(c.docname) for c in cases}
        if len(docnames) == 1:
            return "matched_by_appno_docname"
        # appnos agree but titles differ in spelling: accept if any pair is
        # similar enough (translation / transliteration variance).
        max_sim = max(
            docname_similarity(a.docname, b.docname)
            for i, a in enumerate(cases)
            for b in cases[i + 1 :]
        )
        if max_sim >= DOCNAME_SIMILARITY_THRESHOLD:
            return "matched_by_appno_docname"

    return "ambiguous"
