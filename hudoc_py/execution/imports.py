"""Import helpers for official HUDOC-EXEC CSV/XLSX exports."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def parse_exec_export(path: str | Path) -> pd.DataFrame:
    """Read a HUDOC-EXEC CSV/XLSX export, normalizing empty column names."""
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        try:
            frame = pd.read_excel(path)
        except ImportError as exc:
            raise ImportError("Excel imports require echr-py[convenience]") from exc
    else:
        last: UnicodeDecodeError | None = None
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                frame = pd.read_csv(path, encoding=encoding)
                break
            except UnicodeDecodeError as exc:
                last = exc
        else:
            assert last is not None
            raise last
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


__all__ = ["parse_exec_export"]
