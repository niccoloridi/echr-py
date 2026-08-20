"""Registry of local corpus tables (friendly name → file under a data dir).

A corpus directory produced by ``echr-py corpus build`` (plus any extraction
JSONLs written alongside it) is the input to the local search/browse/export
tools. Everything keys off three assumptions: this registry, ``itemid`` as the
universal join key, and ISO-string dates for lexicographic range filters.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    import pandas as pd

# Friendly name → relative filename. Parquet tables load as DataFrames directly;
# JSONL tables are line-delimited records (texts, extraction outputs). Citation
# tables join on canonical ECLI/item-ID node IDs, not language-specific item IDs.
PARQUET_TABLES = {
    "paragraphs": "paragraphs.parquet",
    "footnotes": "footnotes.parquet",
    "cases": "cases.parquet",
    "duplicates": "duplicates.parquet",
    "metrics": "metrics.parquet",
    "edges": "edges.parquet",
    "nodes": "nodes.parquet",
    "citation_mentions": "citations/mentions.parquet",
    "citation_targets": "citations/targets.parquet",
    "citation_candidates": "citations/candidates.parquet",
    "citation_edges": "citations/edges.parquet",
    "citation_nodes": "citations/nodes.parquet",
    "citation_occurrences": "citations/occurrences.parquet",
}

JSONL_TABLES = {
    "texts": "texts.jsonl",
    "language_texts": "language-texts.jsonl",
    "exec_docs": "exec_docs.jsonl",
    "rescue": "rescue.jsonl",
}


def data_root(data_dir: str | Path | None = None) -> Path:
    """Resolve the corpus directory (defaults to the package cache dir)."""
    return Path(data_dir) if data_dir else config.DATA_DIR


def find_table(name: str, data_dir: str | Path | None = None) -> tuple[Path, str] | None:
    """Return ``(path, "parquet"|"jsonl")`` for a registered table, or ``None``."""
    root = data_root(data_dir)
    if name in PARQUET_TABLES:
        p = root / PARQUET_TABLES[name]
        if p.exists():
            return p, "parquet"
    if name in JSONL_TABLES:
        p = root / JSONL_TABLES[name]
        if p.exists():
            return p, "jsonl"
    return None


def available_tables(data_dir: str | Path | None = None) -> list[tuple[str, str, Path]]:
    """List ``(name, fmt, path)`` for every registered table present on disk."""
    root = data_root(data_dir)
    out: list[tuple[str, str, Path]] = []
    for name, rel in PARQUET_TABLES.items():
        p = root / rel
        if p.exists():
            out.append((name, "parquet", p))
    for name, rel in JSONL_TABLES.items():
        p = root / rel
        if p.exists():
            out.append((name, "jsonl", p))
    return out


def all_table_names() -> list[str]:
    """Every registered friendly name (whether present on disk or not)."""
    return list(PARQUET_TABLES) + list(JSONL_TABLES)


def load_table(name: str, data_dir: str | Path | None = None) -> pd.DataFrame:
    """Load a registered table into a DataFrame (Parquet or JSONL)."""
    import pandas as pd

    found = find_table(name, data_dir)
    if found is None:
        raise FileNotFoundError(f"Table {name!r} not found under {data_root(data_dir)}")
    path, fmt = found
    if fmt == "parquet":
        return pd.read_parquet(path)
    from ..utils.jsonl import iter_jsonl

    return pd.DataFrame(list(iter_jsonl(path)))
