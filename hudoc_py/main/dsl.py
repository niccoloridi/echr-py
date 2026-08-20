"""Fluent query DSL for HUDOC Lucene queries.

Build composable boolean expressions with ``&``, ``|`` and ``~`` and compile
them with :meth:`Q.to_lucene`::

    from hudoc_py import Q

    q = (Q.article("3") | Q.article("8")) & Q.respondent("ITA") & ~Q.body("committee")
    q &= Q.phrase("positive obligations")
    search(query=q)

``Q`` objects are immutable; every operator returns a new node. The DSL is a
thin layer over the same rendering rules as
:func:`hudoc_py.main.queries.build_search_query`, which also accepts a ``Q``
via its ``where=`` parameter.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from .queries import BODY_ALIASES, _coerce_date, _quote

# Lucene proximity ("slop") operator: "w1 w2"~N. HUDOC's web UI also advertises
# an infix NEAR form; if a live probe shows the API needs it, change only this
# template.
_NEAR_TEMPLATE = '"{phrase}"~{distance}'

_Kind = Literal["leaf", "and", "or", "not"]


class Q:
    """One node of a composable HUDOC query expression."""

    __slots__ = ("_kind", "_children", "_fragment")

    def __init__(self, kind: _Kind, children: tuple[Q, ...] = (), fragment: str = ""):
        self._kind = kind
        self._children = children
        self._fragment = fragment

    # --- combinators ---------------------------------------------------------

    def __and__(self, other: Q) -> Q:
        return self._combine("and", other)

    def __or__(self, other: Q) -> Q:
        return self._combine("or", other)

    def __invert__(self) -> Q:
        return Q("not", (self,))

    def _combine(self, kind: _Kind, other: Q) -> Q:
        if not isinstance(other, Q):
            raise TypeError(f"Cannot combine Q with {type(other).__name__}")
        left = self._children if self._kind == kind else (self,)
        right = other._children if other._kind == kind else (other,)
        return Q(kind, left + right)

    # --- rendering -------------------------------------------------------------

    def to_lucene(self) -> str:
        """Compile the expression to a HUDOC Lucene query string."""
        if self._kind == "leaf":
            return self._fragment
        if self._kind == "not":
            return f"NOT ({self._children[0].to_lucene()})"
        joiner = " AND " if self._kind == "and" else " OR "
        rendered = [
            child.to_lucene() if child._kind in ("leaf", "not") else f"({child.to_lucene()})"
            for child in self._children
        ]
        return joiner.join(rendered)

    def __str__(self) -> str:
        return self.to_lucene()

    def __repr__(self) -> str:
        return f"Q<{self.to_lucene()}>"

    # --- generic factories -------------------------------------------------------

    @classmethod
    def raw(cls, fragment: str) -> Q:
        """Wrap a raw Lucene fragment verbatim."""
        return cls("leaf", fragment=fragment)

    @classmethod
    def field(cls, name: str, value: Any, *, equals: bool = False, quote: bool = True) -> Q:
        """Render ``name:value`` (or ``name=value`` with ``equals=True``)."""
        sep = "=" if equals else ":"
        rendered = _quote(str(value)) if quote else str(value)
        return cls("leaf", fragment=f"{name}{sep}{rendered}")

    # --- full-text helpers -------------------------------------------------------

    @classmethod
    def text(cls, terms: str) -> Q:
        """Bare full-text terms (analyzed, ranked by HUDOC's relevance model)."""
        return cls("leaf", fragment=f"({terms})")

    @classmethod
    def phrase(cls, phrase: str) -> Q:
        """Exact-phrase full-text search."""
        return cls("leaf", fragment=_quote(phrase))

    @classmethod
    def text_near(cls, phrase: str, distance: int = 5) -> Q:
        """Proximity search: the words of ``phrase`` within ``distance`` words."""
        escaped = phrase.replace('"', r"\"")
        return cls("leaf", fragment=_NEAR_TEMPLATE.format(phrase=escaped, distance=distance))

    # --- typed shortcuts -----------------------------------------------------------

    @classmethod
    def article(cls, value: str | int) -> Q:
        return cls.field("article", value)

    @classmethod
    def respondent(cls, value: str) -> Q:
        return cls.field("respondent", value)

    @classmethod
    def appno(cls, value: str) -> Q:
        return cls.field("appno", value)

    @classmethod
    def itemid(cls, value: str) -> Q:
        return cls.field("itemid", value)

    @classmethod
    def importance(cls, value: int) -> Q:
        return cls.field("importance", value, quote=False)

    @classmethod
    def conclusion(cls, value: str) -> Q:
        return cls.field("conclusion", value)

    @classmethod
    def thesaurus(cls, value: str | int) -> Q:
        """Filter by numeric thesaurus ID (e.g. 350). Labels do not match."""
        return cls.field("kpthesaurus", value)

    @classmethod
    def concept(cls, value: str) -> Q:
        return cls.field("ECHRConcepts", value)

    @classmethod
    def docname(cls, value: str) -> Q:
        return cls.field("docname", value)

    @classmethod
    def body(cls, value: str) -> Q:
        """Bench composition: ``grand-chamber`` / ``chamber`` / ``committee``."""
        return cls.field("doctypebranch", BODY_ALIASES.get(value.lower(), value.upper()))

    @classmethod
    def ecli(cls, value: str) -> Q:
        return cls.field("ecli", value)

    @classmethod
    def doctype(cls, value: str) -> Q:
        return cls.field("doctype", value, equals=True, quote=False)

    @classmethod
    def separate_opinion(cls, value: bool = True) -> Q:
        return cls.field("separateopinion", "TRUE" if value else "FALSE")

    @classmethod
    def date_range(
        cls,
        date_from: str | date | datetime | None = None,
        date_to: str | date | datetime | None = None,
    ) -> Q:
        df = _coerce_date(date_from) if date_from is not None else "1959-01-01T00:00:00.0Z"
        dt = _coerce_date(date_to) if date_to is not None else "2999-12-31T00:00:00.0Z"
        return cls("leaf", fragment=f'(kpdate>="{df}" AND kpdate<="{dt}")')


def as_query_string(query: str | Q | None) -> str | None:
    """Coerce a query argument (raw string or Q expression) to a string."""
    if query is None or isinstance(query, str):
        return query
    if isinstance(query, Q):
        return query.to_lucene()
    raise TypeError(f"query must be str or Q, not {type(query).__name__}")
