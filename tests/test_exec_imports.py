"""Smoke imports for the execution subpackage."""

from __future__ import annotations


def test_execution_top_level_exports():
    from hudoc_py import execution

    assert callable(execution.search)
    assert callable(execution.search_documents)
    assert callable(execution.fetch_case)
    assert callable(execution.fetch_document)
    assert callable(execution.fetch_text)
    assert execution.AsyncHudocExecClient
    assert execution.AsyncExecDocumentDownloader
    assert execution.AsyncExecRawDownloader
    assert callable(execution.link_application)
    assert callable(execution.parse_exec_export)
    assert execution.collection_code("acp;all") == "acp"
    assert "CEC" in execution.COLLECTION_CODES


def test_execution_aio_namespace():
    from hudoc_py.execution import aio

    assert callable(aio.search)
    assert callable(aio.search_documents)
    assert callable(aio.fetch_case)
    assert callable(aio.fetch_document)


def test_models_exposed_at_top_level():
    from hudoc_py.models import (
        ExecutionCase,
        ExecutionDocument,
    )

    assert ExecutionCase
    assert ExecutionDocument


def test_execution_acquisition_helpers_import_without_side_effects():
    from hudoc_py.execution import related_scraper, textract

    assert callable(textract.extract_text)
    assert callable(related_scraper.infer_document_type)
