"""HUDOC-EXEC: Committee of Ministers execution data.

This is the symmetric counterpart of :mod:`hudoc_py.main`. The top-level
sync surface mirrors the main package::

    from hudoc_py.execution import search, fetch_case, fetch_document

For async, import from :mod:`hudoc_py.execution.aio`.
"""

from . import aio
from ._sync import count, fetch_case, fetch_document, fetch_text, search, search_documents
from .bridge import LinkedCase, link_application, link_applications
from .client import AsyncHudocExecClient
from .collections import COLLECTION_CODES, EXEC_BASE_QUERY, collection_code
from .downloader import AsyncExecDocumentDownloader, fetch_exec_document_html
from .imports import parse_exec_export
from .queries import build_exec_query
from .raw_download import AsyncExecRawDownloader, fetch_exec_document_pdf

__all__ = [
    "aio",
    "search",
    "count",
    "search_documents",
    "fetch_case",
    "fetch_document",
    "fetch_text",
    "AsyncHudocExecClient",
    "AsyncExecDocumentDownloader",
    "fetch_exec_document_html",
    "fetch_exec_document_pdf",
    "AsyncExecRawDownloader",
    "LinkedCase",
    "link_application",
    "link_applications",
    "parse_exec_export",
    "build_exec_query",
    "COLLECTION_CODES",
    "EXEC_BASE_QUERY",
    "collection_code",
]
