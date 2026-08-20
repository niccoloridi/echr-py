"""Local corpus convenience layer: search / browse / export over cached tables.

Operates offline on the files a ``echr-py corpus build`` produces (plus any
extraction JSONLs). All keyed on ``itemid`` and ISO-string dates.
"""

from __future__ import annotations

from .browse import run_browse
from .codebook import generate_codebook_markdown, write_codebook
from .export import export_data
from .paragraphs import (
    build_paragraph_index,
    get_paragraphs,
    paragraph_index_metadata,
    search_paragraphs,
)
from .registry import (
    available_tables,
    find_table,
    load_table,
)
from .search import MODES, run_search

__all__ = [
    "run_search",
    "run_browse",
    "MODES",
    "available_tables",
    "find_table",
    "load_table",
    "export_data",
    "generate_codebook_markdown",
    "write_codebook",
    "build_paragraph_index",
    "get_paragraphs",
    "search_paragraphs",
    "paragraph_index_metadata",
]
