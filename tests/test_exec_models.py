"""Tests for the ExecutionCase / ExecutionDocument Pydantic models."""

from __future__ import annotations

from datetime import date

import pytest

from hudoc_py.models import (
    ExecutionCase,
    ExecutionCaseCollection,
    ExecutionDocument,
)

SAMPLE_CASE_ROW = {
    "execidentifier": "004-1234",
    "execdocumenttype": "MERITS",
    "execdocumenttypecollection": "CEC",
    "exectitle": "Foo v. Italy",
    "execstate": "ITA",
    "execappno": "46221/99;12738/10",
    "execsupervision": "enhanced",
    "execisclosed": "FALSE",
    "execisprecedent": "TRUE",
    "execlanguage": "ENG",
    "execjudgmentdate": "2020-03-15T00:00:00.0Z",
    "execfinaljudgmentdate": "2020-09-15T00:00:00.0Z",
    "execviolations": "Art. 3; Art. 8",
    "execprecedentappnos": "11111/11;22222/22",
    "execmastergroupid": "GRP-001",
    "isplaceholder": "FALSE",
    "noise_field": "ignored",
}


SAMPLE_DOC_ROW = {
    "execidentifier": "DH-DD(2023)123",
    "execdocumenttype": "acp",
    "execdocumenttypecollection": "acp",
    "exectitle": "Action plan for Foo v. Italy",
    "execstate": "ITA",
    "execappno": "46221/99",
    "execlanguage": "ENG",
    "execpublisheddate": "2023-06-01T00:00:00.0Z",
    "execpublisheddateastext": "01/06/2023",
}


def test_execution_case_parses_basic_metadata():
    c = ExecutionCase.model_validate(SAMPLE_CASE_ROW)
    assert c.execidentifier == "004-1234"
    assert c.state == "ITA"
    assert c.title == "Foo v. Italy"
    assert c.supervision == "enhanced"


def test_execution_case_lists_split():
    c = ExecutionCase.model_validate(SAMPLE_CASE_ROW)
    assert c.appno == ["46221/99", "12738/10"]
    assert c.precedent_appnos == ["11111/11", "22222/22"]


def test_execution_case_dates_parsed():
    c = ExecutionCase.model_validate(SAMPLE_CASE_ROW)
    assert c.judgment_date == date(2020, 3, 15)
    assert c.final_judgment_date == date(2020, 9, 15)


def test_execution_case_booleans():
    c = ExecutionCase.model_validate(SAMPLE_CASE_ROW)
    assert c.is_closed is False
    assert c.is_precedent is True
    assert c.is_placeholder is False


def test_execution_case_buckets_default_empty():
    c = ExecutionCase.model_validate(SAMPLE_CASE_ROW)
    assert c.action_plans == []
    assert c.cm_decisions == []
    assert c.resolutions == []


def test_execution_document_parses():
    d = ExecutionDocument.model_validate(SAMPLE_DOC_ROW)
    assert d.execidentifier == "DH-DD(2023)123"
    assert d.document_type_collection == "acp"
    assert d.appno == ["46221/99"]
    assert d.published_date == date(2023, 6, 1)


def test_collection_to_dataframe():
    pd = pytest.importorskip("pandas")
    coll = ExecutionCaseCollection(
        [
            ExecutionCase.model_validate(SAMPLE_CASE_ROW),
            ExecutionCase.model_validate({**SAMPLE_CASE_ROW, "execidentifier": "004-9999"}),
        ]
    )
    df = coll.to_dataframe()
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "execidentifier" in df.columns
