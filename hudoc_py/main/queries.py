"""Lucene query construction for the HUDOC main search endpoint.

The HUDOC search API speaks a Lucene-style boolean query language with
named fields. This module builds those query strings from typed kwargs.

Field notes (semantics live-probed against the real API, July 2026):

* ``conclusion`` – analyzed text, phrase-matched: ``conclusion:"Violation of
  Article 3"``. Note a phrase like this also occurs inside "No violation of
  Article 3"; combine with a NOT clause for strict exclusion.
* ``kpthesaurus`` – stored as **numeric keypoint IDs** (e.g. ``449;231;350``).
  The query builder accepts either an ID (``kpthesaurus="350"``) or keyword
  text (``kpthesaurus="torture"``), resolving text to IDs via the vendored
  ECHR keyword taxonomy (see :mod:`hudoc_py.thesaurus`).
* ``ECHRConcepts`` – case-sensitive field name; empty on ordinary case-law
  rows (populated for ECHR knowledge-sharing content only).
* ``originatingbody`` – a **numeric code** (e.g. a Grand Chamber judgment
  carries ``"8"``); labels like "Grand Chamber" do not match. For bench
  filtering prefer ``body=`` → ``doctypebranch``.
* ``doctypebranch`` – bench composition: ``GRANDCHAMBER`` / ``CHAMBER`` /
  ``COMMITTEE`` (see :data:`BODY_ALIASES`).
* ``documentcollectionid`` – ``JUDGMENTS``, ``ADMISSIBILITY``,
  ``ADMISSIBILITYCOM``, ``DECGRANDCHAMBER``, ...
* ``separateopinion`` – string booleans ``"TRUE"`` / ``"FALSE"``.
* Full-text proximity uses Lucene slop (``"w1 w2"~N``, verified); the infix
  ``NEAR`` form advertised by the web UI is not honoured by the API.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .dsl import Q

# Document types used by HUDOC's CMS. HE* = English variants, HF* = French variants.
DOCTYPES_ALL = (
    "HEJUD",
    "HEDEC",
    "HEADO",  # English: judgment, decision, advisory opinion
    "HFJUD",
    "HFDEC",
    "HFADO",  # French equivalents
)

DOCTYPES_JUDGMENTS = ("HEJUD", "HFJUD")
DOCTYPES_DECISIONS = ("HEDEC", "HFDEC")
DOCTYPES_ADVISORY = ("HEADO", "HFADO")

# --- Sorting -----------------------------------------------------------------
# HUDOC's sort parameter is "<column> <Ascending|Descending>"; the empty string
# means "use the ranking model" i.e. relevance order.
SORT_RELEVANCE = ""
SORT_DATE_DESC = "kpdate Descending"
SORT_DATE_ASC = "kpdate Ascending"

SORT_ALIASES = {
    "relevance": SORT_RELEVANCE,
    "date-desc": SORT_DATE_DESC,
    "date-asc": SORT_DATE_ASC,
    "": SORT_RELEVANCE,
}


def resolve_sort(sort: str | None) -> str:
    """Resolve a friendly sort alias to HUDOC's sort string.

    Accepts ``relevance`` / ``date-desc`` / ``date-asc``, the empty string,
    ``None``, or a raw ``"column Direction"`` string which is passed through.
    """
    if sort is None:
        return SORT_RELEVANCE
    if sort in SORT_ALIASES:
        return SORT_ALIASES[sort]
    parts = sort.split()
    if len(parts) == 2 and parts[1] in ("Ascending", "Descending"):
        return sort
    raise ValueError(
        f"Unknown sort {sort!r}: use 'relevance', 'date-desc', 'date-asc' "
        "or a raw '<column> <Ascending|Descending>' string"
    )


# Bench composition: friendly alias -> doctypebranch value.
BODY_ALIASES = {
    "grand-chamber": "GRANDCHAMBER",
    "chamber": "CHAMBER",
    "committee": "COMMITTEE",
}

# documentcollectionid values in common use.
COLLECTION_JUDGMENTS = "JUDGMENTS"
COLLECTION_ADMISSIBILITY = ("ADMISSIBILITY", "ADMISSIBILITYCOM", "DECGRANDCHAMBER")

# Fields requested from HUDOC's search "select" parameter. Keep broad so the Case
# model can be populated comprehensively – scholars want "any data one could dream of."
METADATA_SELECT_FIELDS = (
    "sharepointid,Rank,ECHRRanking,languagenumber,itemid,docname,doctype,"
    "application,appno,conclusion,importance,originatingbody,typedescription,"
    "kpdate,kpdateAsText,documentcollectionid,documentcollectionid2,"
    "languageisocode,extractedappno,isplaceholder,doctypebranch,respondent,"
    "advopidentifier,advopstatus,ecli,appnoparts,sclappnos,ECHRConcepts,"
    "representedby,applicability,article,decisiondate,externalsources,"
    "introductiondate,issue,judgementdate,kpthesaurus,meetingnumber,"
    "publishedby,referencedate,reportdate,resolutiondate,resolutionnumber,"
    "rulesofcourt,separateopinion,scl,casecitation"
)


def _quote(value: str) -> str:
    """Escape and quote a Lucene string literal."""
    return '"' + value.replace('"', r"\"") + '"'


def _coerce_date(value: str | date | datetime) -> str:
    """Render a date in HUDOC's expected format: ``YYYY-MM-DDTHH:MM:SS.0Z``."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S.0Z")
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00.0Z"
    if isinstance(value, str):
        # If already in the HUDOC long form, leave it; otherwise assume bare YYYY-MM-DD.
        if "T" in value:
            return value
        return f"{value}T00:00:00.0Z"
    raise TypeError(f"Unsupported date type: {type(value).__name__}")


def _or_group(field: str, values: Iterable[Any], *, equals: bool = False) -> str:
    """Render ``(field=X OR field=Y)`` or ``(field:X OR field:Y)`` for a list."""
    sep = "=" if equals else ":"
    parts = [f"{field}{sep}{v}" for v in values]
    return "(" + " OR ".join(parts) + ")"


def build_search_query(
    *,
    text: str | None = None,
    appno: str | Iterable[str] | None = None,
    itemid: str | Iterable[str] | None = None,
    article: str | Iterable[str] | None = None,
    respondent: str | Iterable[str] | None = None,
    importance: int | Iterable[int] | None = None,
    conclusion: str | Iterable[str] | None = None,
    kpthesaurus: str | Iterable[str] | None = None,
    concepts: str | Iterable[str] | None = None,
    docname: str | Iterable[str] | None = None,
    body: str | Iterable[str] | None = None,
    doctypebranch: str | Iterable[str] | None = None,
    originatingbody: str | Iterable[str] | None = None,
    separate_opinion: bool | None = None,
    ecli: str | Iterable[str] | None = None,
    collection: str | Iterable[str] | None = None,
    advop_identifier: str | None = None,
    advop_status: str | None = None,
    doctypes: Iterable[str] = DOCTYPES_ALL,
    languages: Iterable[str] = ("ENG", "FRE"),
    date_from: str | date | datetime | None = None,
    date_to: str | date | datetime | None = None,
    where: Q | None = None,
    extra: str | None = None,
) -> str:
    """Compose a HUDOC Lucene query from typed filters.

    All filters are AND-ed together; multi-valued filters are OR-ed internally.
    Pass ``where`` (a :class:`~hudoc_py.main.dsl.Q` expression) for arbitrary
    boolean logic, or ``extra`` to append a raw Lucene fragment.
    """
    clauses: list[str] = ["contentsitename:ECHR"]

    if doctypes:
        clauses.append(_or_group("doctype", doctypes, equals=True))

    if languages:
        language_values = [_quote(lang) for lang in languages]
        clauses.append(
            "(" + " OR ".join(f"languageisocode={value}" for value in language_values) + ")"
        )

    if date_from is not None or date_to is not None:
        df = _coerce_date(date_from) if date_from is not None else "1959-01-01T00:00:00.0Z"
        dt = _coerce_date(date_to) if date_to is not None else "2999-12-31T00:00:00.0Z"
        clauses.append(f'(kpdate>="{df}" AND kpdate<="{dt}")')

    def _normalize(v: Any) -> list[Any]:
        if v is None:
            return []
        if isinstance(v, str) or not isinstance(v, Iterable):
            return [v]
        return list(v)

    if body is not None:
        aliased = [BODY_ALIASES.get(str(v).lower(), str(v).upper()) for v in _normalize(body)]
        doctypebranch = list(_normalize(doctypebranch)) + aliased if doctypebranch else aliased

    if kpthesaurus is not None:
        # Accept keyword text ("torture") or numeric IDs; resolve text to the
        # matching keypoint ID(s) via the vendored ECHR keyword taxonomy.
        from ..thesaurus import resolve_keypoint

        resolved: list[str] = []
        for v in _normalize(kpthesaurus):
            resolved.extend(resolve_keypoint(v))
        kpthesaurus = list(dict.fromkeys(resolved))  # dedup, keep order

    for field, value, quoted in (
        ("itemid", itemid, True),
        ("appno", appno, True),
        ("article", article, True),
        ("respondent", respondent, True),
        ("importance", importance, False),
        ("conclusion", conclusion, True),
        ("kpthesaurus", kpthesaurus, True),
        ("ECHRConcepts", concepts, True),
        ("docname", docname, True),
        ("doctypebranch", doctypebranch, True),
        ("originatingbody", originatingbody, True),
        ("ecli", ecli, True),
        ("documentcollectionid", collection, True),
        ("advopidentifier", advop_identifier, True),
        ("advopstatus", advop_status, True),
    ):
        vs = _normalize(value)
        if not vs:
            continue
        if quoted:
            vs = [_quote(str(v)) for v in vs]
        clauses.append(_or_group(field, vs, equals=False) if len(vs) > 1 else f"{field}:{vs[0]}")

    if separate_opinion is not None:
        clauses.append(f'separateopinion:"{"TRUE" if separate_opinion else "FALSE"}"')

    if text:
        clauses.append(f"({text})")

    if where is not None:
        clauses.append(f"({where.to_lucene()})")

    if extra:
        clauses.append(f"({extra})")

    return " AND ".join(clauses)
