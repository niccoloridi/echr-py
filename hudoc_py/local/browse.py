"""Terminal data browser for local corpus tables.

Adapted from cjeu-py's ``browse.py`` (generic table tooling). Lists tables,
shows column info and per-column statistics, and previews rows.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from .registry import all_table_names, available_tables, find_table, load_table


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _bar(fraction: float, width: int = 20) -> str:
    filled = int(round(fraction * width))
    return "█" * filled + " " * (width - filled)


def _suggest_similar(name: str) -> str:
    matches = difflib.get_close_matches(name, all_table_names(), n=1)
    return f" Did you mean {matches[0]!r}?" if matches else ""


def list_tables(data_dir: str | Path | None = None) -> str:
    rows = available_tables(data_dir)
    if not rows:
        return "No corpus tables found. Run `echr-py corpus build` first."
    lines = ["TABLE".ljust(14) + "FMT".ljust(9) + "ROWS".rjust(10) + "  SIZE"]
    lines.append("─" * 45)
    for name, fmt, path in rows:
        try:
            if fmt == "parquet":
                import pyarrow.parquet as pq

                nrows = pq.read_metadata(path).num_rows
            else:
                nrows = sum(1 for _ in path.open("r", encoding="utf-8"))
        except Exception:
            nrows = -1
        size = _human_size(path.stat().st_size)
        rows_str = "?" if nrows < 0 else str(nrows)
        lines.append(name.ljust(14) + fmt.ljust(9) + rows_str.rjust(10) + f"  {size}")
    return "\n".join(lines)


def show_columns(table: str, data_dir: str | Path | None = None) -> str:
    if find_table(table, data_dir) is None:
        return f"Table {table!r} not found.{_suggest_similar(table)}"
    df = load_table(table, data_dir)
    lines = [f"{table}: {len(df.columns)} columns, {len(df)} rows", ""]
    for col in df.columns:
        dtype = _friendly_dtype(str(df[col].dtype))
        nulls = int(df[col].isna().sum())
        samples = [str(v) for v in df[col].dropna().unique()[:3]]
        lines.append(
            f"  {col:<22} {dtype:<8} nulls={nulls:<6} e.g. {', '.join(samples)[:50]}"
        )
    return "\n".join(lines)


def _friendly_dtype(dtype: str) -> str:
    if dtype.startswith("int") or dtype.startswith("float"):
        return "number"
    if dtype == "bool":
        return "bool"
    if "datetime" in dtype:
        return "date"
    return "text"


def show_stats(table: str, data_dir: str | Path | None = None) -> str:
    if find_table(table, data_dir) is None:
        return f"Table {table!r} not found.{_suggest_similar(table)}"
    df = load_table(table, data_dir)
    lines = [f"{table}: {len(df)} rows, {len(df.columns)} columns"]
    mem = df.memory_usage(deep=True).sum() / (1024 * 1024)
    lines.append(f"memory: {mem:.1f}MB")
    for date_col in ("kp_date", "decision_date", "kp_date_text"):
        if date_col in df.columns and df[date_col].notna().any():
            col = df[date_col].astype(str)
            lines.append(f"date range ({date_col}): {col.min()} … {col.max()}")
            break
    lines.append("")

    for col in df.columns:
        series = df[col].dropna()
        if series.empty:
            continue
        dtype = str(series.dtype)
        if dtype.startswith(("int", "float")):
            lines.append(
                f"{col}: min={series.min():.2f} max={series.max():.2f} "
                f"mean={series.mean():.2f} median={series.median():.2f}"
            )
        else:
            nunique = series.nunique()
            if nunique > 100:
                lines.append(f"{col}: {nunique} distinct (too many to display)")
                continue
            lines.append(f"{col}: top values")
            counts = series.astype(str).value_counts().head(10)
            total = len(series)
            for value, n in counts.items():
                frac = n / total
                lines.append(f"  {value[:24]:<24} {_bar(frac)} {n} ({frac:.0%})")
    return "\n".join(lines)


def preview_table(
    table: str, data_dir: str | Path | None = None, *, limit: int = 20, fmt: str = "table"
) -> str:
    if find_table(table, data_dir) is None:
        return f"Table {table!r} not found.{_suggest_similar(table)}"
    df = load_table(table, data_dir).head(limit)
    if fmt == "csv":
        return df.to_csv(index=False)
    if fmt == "json":
        return df.to_json(orient="records", indent=2, default_handler=str)
    return df.to_string(max_colwidth=40)


def run_browse(
    data_dir: str | Path | None,
    table: str | None = None,
    *,
    stats: bool = False,
    columns: bool = False,
    limit: int = 20,
    fmt: str = "table",
) -> str:
    """Dispatch a browse action and return rendered output."""
    if not table:
        return list_tables(data_dir)
    if columns:
        return show_columns(table, data_dir)
    if stats:
        return show_stats(table, data_dir)
    return preview_table(table, data_dir, limit=limit, fmt=fmt)
