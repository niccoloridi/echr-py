"""Top-level smoke import test – catches import-time errors after refactors."""

from __future__ import annotations


def test_top_level_exports():
    import hudoc_py

    assert callable(hudoc_py.search)
    assert callable(hudoc_py.fetch_case)
    assert callable(hudoc_py.fetch_text)
    assert hudoc_py.Case
    assert hudoc_py.CaseCollection
    assert hudoc_py.Sections


def test_aio_namespace_present():
    import hudoc_py

    assert hasattr(hudoc_py, "aio")
    assert callable(hudoc_py.aio.search)
    assert callable(hudoc_py.aio.fetch_case)


def test_main_subpackage_exports():
    from hudoc_py.main import (
        AsyncDocumentDownloader,
        AsyncHudocClient,
        build_search_query,
    )

    assert AsyncDocumentDownloader
    assert AsyncHudocClient
    assert callable(build_search_query)


def test_bilingual_subpackage_imports():
    from hudoc_py.bilingual import classify_ecli_cluster, normalize_ecli

    assert callable(normalize_ecli)
    assert callable(classify_ecli_cluster)
