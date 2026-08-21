"""echr-py – pythonic access to HUDOC and HUDOC-EXEC.

Quick start::

    from hudoc_py import search, fetch_case

    case = fetch_case(appno="46221/99", with_text=True)
    print(case.docname, case.articles, case.sections.dispositif)

    results = search(article="3", respondent="ITA", limit=100)
    df = results.to_dataframe()

Full-text relevance workflow::

    from hudoc_py import Q, count, smart_fetch

    n = count(text='"positive obligations"', article="8")
    cases = smart_fetch(text='"positive obligations"', article="8", top=5)
    q = (Q.article("3") | Q.article("8")) & Q.respondent("ITA")
    cases = smart_fetch(query=q, top=5)

Async users can import from ``hudoc_py.aio`` instead, which exposes the same
coroutines without the sync wrapper.
"""

from . import _aio as aio
from ._sync import (
    count,
    download_versions,
    fetch_case,
    fetch_docx,
    fetch_text,
    list_versions,
    search,
    smart_fetch,
)
from .main.dsl import Q
from .models import Case, CaseCollection, Document, Sections

__version__ = "0.2.1"

__all__ = [
    "__version__",
    "aio",
    "search",
    "count",
    "smart_fetch",
    "fetch_case",
    "fetch_text",
    "fetch_docx",
    "list_versions",
    "download_versions",
    "Q",
    "Case",
    "CaseCollection",
    "Document",
    "Sections",
]
