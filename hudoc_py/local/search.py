"""Local corpus search over cached tables (no network).

Adapted from cjeu-py's ``search.py``. Modes operate on the files a
``corpus build`` produces; the fixed-width table / CSV / JSON renderer and the
ISO-date filter are reused verbatim from that project.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..utils.jsonl import iter_jsonl
from .registry import find_table, load_table

if TYPE_CHECKING:
    import pandas as pd

MODES = ("text", "party", "citing", "cited-by", "list")


# --- Generic renderer (reused verbatim) --------------------------------------


def _truncate(value: Any, width: int) -> str:
    s = "" if value is None else str(value)
    return s if len(s) <= width else s[: width - 3] + "..."


def _extract_snippet(text: str, query: str, *, radius: int = 100) -> str:
    idx = text.lower().find(query.lower())
    if idx < 0:
        return text[: radius * 2].strip()
    start = max(0, idx - radius)
    end = min(len(text), idx + len(query) + radius)
    snippet = text[start:end].strip()
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")


def format_results(
    df: pd.DataFrame,
    columns: list[str],
    widths: dict[str, int],
    *,
    limit: int,
    fmt: str,
    header_msg: str = "",
) -> str:
    cols = [c for c in columns if c in df.columns]
    view = df[cols].head(limit)
    if fmt == "csv":
        return view.to_csv(index=False)
    if fmt == "json":
        return view.to_json(orient="records", indent=2, default_handler=str)
    # Fixed-width ASCII table.
    lines: list[str] = []
    if header_msg:
        lines.append(header_msg)
    header = "  ".join(c.upper().ljust(widths.get(c, 20)) for c in cols)
    lines.append(header)
    lines.append("─" * len(header))
    for _, row in view.iterrows():
        lines.append(
            "  ".join(_truncate(row[c], widths.get(c, 20)).ljust(widths.get(c, 20)) for c in cols)
        )
    lines.append(f"\nShowing {len(view)} of {len(df)} results")
    return "\n".join(lines)


def _apply_filters(
    df: pd.DataFrame,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    respondent: str | None = None,
) -> pd.DataFrame:
    """ISO-string date range + exact respondent filter (join key: itemid)."""
    if date_from and "kp_date" in df.columns:
        df = df[df["kp_date"].astype(str) >= date_from]
    if date_to and "kp_date" in df.columns:
        df = df[df["kp_date"].astype(str) <= date_to]
    if respondent and "respondent" in df.columns:
        df = df[df["respondent"].astype(str).str.contains(respondent, case=False, na=False)]
    return df


# --- Modes -------------------------------------------------------------------


def _search_text(data_dir, query, limit) -> pd.DataFrame:
    import pandas as pd

    found = find_table("texts", data_dir)
    if found is None:
        return pd.DataFrame(columns=["itemid", "snippet"])
    path, _ = found
    q = query.lower()
    rows = []
    for rec in iter_jsonl(path):
        text = rec.get("text") or ""
        if q in text.lower():
            rows.append({"itemid": rec.get("itemid"), "snippet": _extract_snippet(text, query)})
            if len(rows) >= limit * 4:  # gather extra; enrich then head(limit)
                break
    return pd.DataFrame(rows)


def _enrich_with_cases(df: pd.DataFrame, data_dir) -> pd.DataFrame:
    """Left-join case metadata on itemid (docname/respondent/date/importance)."""
    if df.empty or "itemid" not in df.columns:
        return df
    try:
        cases = load_table("cases", data_dir)
    except FileNotFoundError:
        return df
    keep = [
        c
        for c in ("itemid", "docname", "respondent", "kp_date", "importance", "articles")
        if c in cases.columns
    ]
    return df.merge(cases[keep], on="itemid", how="left")


def _search_party(data_dir, query, limit) -> pd.DataFrame:
    cases = load_table("cases", data_dir)
    q = query.lower()
    mask = cases.get("docname", "").astype(str).str.lower().str.contains(q, na=False)
    if "represented_by" in cases.columns:
        mask = mask | cases["represented_by"].astype(str).str.lower().str.contains(q, na=False)
    return cases[mask]


def _search_citation(data_dir, query, limit, *, direction: str) -> pd.DataFrame:
    import pandas as pd

    try:
        try:
            edges = load_table("citation_edges", data_dir)
        except FileNotFoundError:
            edges = load_table("edges", data_dir)
    except FileNotFoundError:
        return pd.DataFrame()
    src = "source" if direction == "citing" else "target"
    if src not in edges.columns:
        return pd.DataFrame()
    return edges[edges[src].astype(str).str.contains(query, case=False, na=False)]


def _list_categories(data_dir, query) -> pd.DataFrame:
    cases = load_table("cases", data_dir)
    col = query if query in cases.columns else "respondent"
    counts = cases[col].astype(str).value_counts().reset_index()
    counts.columns = [col, "count"]
    return counts


def run_search(
    data_dir: str | Path | None,
    mode: str,
    query: str,
    *,
    limit: int = 50,
    fmt: str = "table",
    date_from: str | None = None,
    date_to: str | None = None,
    respondent: str | None = None,
) -> str:
    """Run a local search and return rendered output (table/csv/json)."""
    if mode == "text":
        df = _enrich_with_cases(_search_text(data_dir, query, limit), data_dir)
        cols = ["itemid", "docname", "respondent", "snippet"]
        widths = {"itemid": 12, "docname": 40, "respondent": 6, "snippet": 60}
    elif mode == "party":
        df = _search_party(data_dir, query, limit)
        cols = ["itemid", "docname", "respondent", "represented_by"]
        widths = {"itemid": 12, "docname": 40, "respondent": 6, "represented_by": 30}
    elif mode in ("citing", "cited-by"):
        df = _search_citation(data_dir, query, limit, direction=mode)
        cols = list(df.columns)
        widths = {c: 18 for c in cols}
    elif mode == "list":
        df = _list_categories(data_dir, query)
        cols = list(df.columns)
        widths = {cols[0]: 30, "count": 8} if cols else {}
    else:
        raise ValueError(f"Unknown search mode {mode!r}; choose from {MODES}")

    df = _apply_filters(df, date_from=date_from, date_to=date_to, respondent=respondent)
    return format_results(df, cols, widths, limit=limit, fmt=fmt)
