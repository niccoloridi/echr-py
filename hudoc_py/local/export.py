"""Export local corpus tables to CSV / XLSX (one file per table).

Adapted from cjeu-py's ``export.py``. Columns holding dicts/lists are
JSON-encoded to strings so CSV/XLSX can represent them.
"""

from __future__ import annotations

import json
from pathlib import Path

from .registry import available_tables, load_table


def _flatten_cell(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def export_data(
    data_dir: str | Path | None,
    output_dir: str | Path,
    *,
    fmt: str = "csv",
    tables: list[str] | None = None,
) -> dict[str, tuple[str, int]]:
    """Write each corpus table to ``output_dir/{name}.{fmt}``.

    Returns ``{name: (path, row_count)}``. ``fmt`` is ``"csv"`` or ``"xlsx"``
    (xlsx needs the ``convenience`` extra / openpyxl).
    """
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    wanted = set(tables) if tables else None

    results: dict[str, tuple[str, int]] = {}
    for name, _fmt, _path in available_tables(data_dir):
        if wanted is not None and name not in wanted:
            continue
        df = load_table(name, data_dir)
        # Flatten any nested (dict/list) columns for tabular output.
        for col in df.columns:
            if df[col].map(lambda v: isinstance(v, (dict, list))).any():
                df[col] = df[col].map(_flatten_cell)
        out_path = out_root / f"{name}.{fmt}"
        if fmt == "xlsx":
            df.to_excel(out_path, index=False)
        else:
            df.to_csv(out_path, index=False)
        results[name] = (str(out_path), len(df))
    return results
