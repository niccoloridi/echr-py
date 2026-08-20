"""Tests for the HUDOC-EXEC query builder."""

from __future__ import annotations

from hudoc_py.execution import EXEC_BASE_QUERY, build_exec_query


def test_default_includes_base_query_and_english():
    q = build_exec_query()
    assert EXEC_BASE_QUERY in q
    assert 'execlanguage="ENG"' in q


def test_collection_filter():
    q = build_exec_query(collection="acp")
    assert 'execdocumenttypecollection="acp"' in q


def test_state_supervision_combined():
    q = build_exec_query(state="ITA", supervision="enhanced")
    assert 'execstate="ITA"' in q
    assert 'execsupervision="enhanced"' in q


def test_is_closed_serialized_as_python_bool():
    q = build_exec_query(is_closed=True)
    assert "(execisclosed=True)" in q
    q2 = build_exec_query(is_closed=False)
    assert "(execisclosed=False)" in q2


def test_language_none_disables_filter():
    q = build_exec_query(language=None)
    assert "execlanguage" not in q


def test_appno_quoted():
    q = build_exec_query(appno="46221/99")
    assert 'execappno="46221/99"' in q
