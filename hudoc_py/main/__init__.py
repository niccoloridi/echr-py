"""HUDOC main database: cases, judgments, decisions, advisory opinions."""

from .client import AsyncHudocClient, HudocResultWindowError
from .downloader import (
    AsyncDocumentDownloader,
    fetch_document_docx,
    fetch_document_html,
)
from .queries import METADATA_SELECT_FIELDS, build_search_query
from .url import query_from_hudoc_url

__all__ = [
    "AsyncHudocClient",
    "HudocResultWindowError",
    "AsyncDocumentDownloader",
    "fetch_document_html",
    "fetch_document_docx",
    "build_search_query",
    "METADATA_SELECT_FIELDS",
    "query_from_hudoc_url",
]
