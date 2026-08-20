"""Simple Parquet helpers for caching HUDOC results to disk."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    import pandas as pd


def cache_dir(subpath: str | None = None) -> Path:
    """Return the cache root (or a sub-path inside it), creating it if missing."""
    root = config.DATA_DIR
    if subpath:
        root = root / subpath
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    """Write a DataFrame to Parquet, creating parent dirs as needed."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return out


def read_parquet(path: str | Path) -> pd.DataFrame:
    """Read a Parquet file."""
    import pandas as pd

    return pd.read_parquet(path)
