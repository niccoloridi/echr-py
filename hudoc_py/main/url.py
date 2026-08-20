"""Convert shareable HUDOC browser URLs into deterministic API queries."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .queries import _quote

_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ALIASES = {
    "separateopinions": "separateopinion",
}
_EQUALS_FIELDS = {"doctype", "languageisocode"}


def _values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
    return [str(value)]


def _field_clause(field: str, values: list[str]) -> str:
    if not _FIELD_RE.fullmatch(field):
        raise ValueError(f"Invalid HUDOC filter field in URL: {field!r}")
    separator = "=" if field in _EQUALS_FIELDS else ":"
    clauses = [f"{field}{separator}{_quote(value)}" for value in values if value != ""]
    if not clauses:
        return ""
    return clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"


def _fragment_payload(fragment: str) -> dict[str, Any]:
    decoded = unquote(fragment).strip()
    start = decoded.find("{")
    end = decoded.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(decoded[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("HUDOC URL fragment does not contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("HUDOC URL fragment must contain a JSON object")
    return payload


def query_from_hudoc_url(url: str) -> str:
    """Return a raw Lucene query from a shareable HUDOC URL.

    Supported forms include HUDOC's ``?i=001-...`` document link, a direct
    API URL carrying ``?query=...``, and the browser application's JSON hash
    such as ``#{\"article\":[\"3\"],\"languageisocode\":[\"ENG\"]}``.
    Unknown JSON keys are preserved as validated HUDOC field filters rather
    than discarded.
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host not in {"hudoc.echr.coe.int", "www.hudoc.echr.coe.int"}:
        raise ValueError("Expected a hudoc.echr.coe.int URL")

    query_params = parse_qs(parsed.query)
    if query_params.get("query"):
        query = unquote(query_params["query"][0]).strip()
        if not query:
            raise ValueError("HUDOC API URL contains an empty query")
        return query

    if query_params.get("i"):
        itemids = [item.strip() for item in query_params["i"] if item.strip()]
        return "contentsitename:ECHR AND " + _field_clause("itemid", itemids)

    payload = _fragment_payload(parsed.fragment)
    if not payload:
        raise ValueError("HUDOC URL contains no query, item ID, or JSON filter fragment")

    clauses = ["contentsitename:ECHR"]
    for raw_field, raw_values in payload.items():
        if raw_field == "sort":
            continue
        values = _values(raw_values)
        if raw_field == "fulltext":
            terms = [value.strip() for value in values if value.strip()]
            if terms:
                clauses.append("(" + " ".join(terms) + ")")
            continue
        if raw_field == "kpdate":
            bounds = (values + [""])[:2]
            lower = bounds[0] or "1959-01-01"
            upper = bounds[1] or "2999-12-31"
            clauses.append(f'(kpdate>={_quote(lower)} AND kpdate<={_quote(upper)})')
            continue
        field = _ALIASES.get(raw_field, raw_field)
        clause = _field_clause(field, values)
        if clause:
            clauses.append(clause)

    if len(clauses) == 1:
        raise ValueError("HUDOC URL contains no usable filters")
    return " AND ".join(clauses)
