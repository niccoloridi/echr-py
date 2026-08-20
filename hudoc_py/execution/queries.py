"""Query builder for HUDOC-EXEC search."""

from __future__ import annotations

from .collections import EXEC_BASE_QUERY


def _quote(s: str) -> str:
    return '"' + s.replace('"', r'\"') + '"'


def build_exec_query(
    *,
    collection: str | None = None,
    state: str | None = None,
    appno: str | None = None,
    supervision: str | None = None,
    is_closed: bool | None = None,
    case_type: str | None = None,
    master_group_id: str | None = None,
    language: str | None = "ENG",
    extra: str | None = None,
) -> str:
    """Compose a HUDOC-EXEC Lucene query.

    All filters are AND-ed onto :data:`EXEC_BASE_QUERY`. Pass ``language=None``
    to disable the default ENG filter.
    """
    parts: list[str] = [EXEC_BASE_QUERY]
    if collection:
        parts.append(f"(execdocumenttypecollection={_quote(collection)})")
    if language:
        parts.append(f"(execlanguage={_quote(language)})")
    if state:
        parts.append(f"(execstate={_quote(state)})")
    if appno:
        parts.append(f"(execappno={_quote(appno)})")
    if supervision:
        parts.append(f"(execsupervision={_quote(supervision)})")
    if is_closed is not None:
        parts.append(f"(execisclosed={str(is_closed)})")
    if case_type:
        parts.append(f"(exectype={_quote(case_type)})")
    if master_group_id:
        parts.append(f"(execmastergroupid={_quote(master_group_id)})")
    if extra:
        parts.append(f"({extra})")
    return " AND ".join(parts)
