"""Tests for the Case Pydantic model."""

from __future__ import annotations

from datetime import date

import pytest

from hudoc_py.models import Case, CaseCollection

# Representative HUDOC search row, fields preserved from a real response shape.
SAMPLE_ROW = {
    "itemid": "001-94054",
    "docname": "CASE OF JEUNESSE v. THE NETHERLANDS",
    "doctype": "HEJUD",
    "doctypebranch": "GRANDCHAMBER",
    "appno": "12738/10",
    "appnoparts": "12738/10",
    "ecli": "ECLI:CE:ECHR:2014:1003JUD001273810",
    "respondent": "NLD",
    "article": "8;14",
    "importance": "1",
    "conclusion": "Violation of Article 8 - Right to respect for private and family life",
    "languageisocode": "ENG",
    "kpdate": "2014-10-03T00:00:00.0Z",
    "kpdateAsText": "03/10/2014",
    "judgementdate": "2014-10-03T00:00:00.0Z",
    "documentcollectionid": "JUDGMENTS;GRANDCHAMBER",
    "documentcollectionid2": "CHAMBER",
    "separateopinion": "TRUE",
    "isplaceholder": "FALSE",
    "representedby": "BÖHLER M.",
    "originatingbody": "Grand Chamber",
    "typedescription": "15",
    "rulesofcourt": "",
    "extra_unknown_field": "should-be-ignored",
}


def test_parses_basic_metadata():
    case = Case.model_validate(SAMPLE_ROW)
    assert case.itemid == "001-94054"
    assert case.docname == "CASE OF JEUNESSE v. THE NETHERLANDS"
    assert case.doctype == "HEJUD"
    assert case.ecli is not None and case.ecli.startswith("ECLI:CE:ECHR")
    assert case.language == "ENG"


def test_semicolon_lists_are_split():
    case = Case.model_validate(SAMPLE_ROW)
    assert case.appno == ["12738/10"]
    assert case.articles == ["8", "14"]
    assert case.respondent == ["NLD"]


def test_parquet_array_like_lists_are_not_stringified():
    np = pytest.importorskip("numpy")

    case = Case.model_validate({"appno": np.array(["38263/08"], dtype=object)})

    assert case.appno == ["38263/08"]


def test_dates_are_parsed_to_date_objects():
    case = Case.model_validate(SAMPLE_ROW)
    assert case.kp_date == date(2014, 10, 3)
    assert case.judgement_date == date(2014, 10, 3)


def test_booleans_normalized():
    case = Case.model_validate(SAMPLE_ROW)
    assert case.separate_opinion is True
    assert case.is_placeholder is False


def test_unknown_fields_ignored():
    case = Case.model_validate(SAMPLE_ROW)
    assert not hasattr(case, "extra_unknown_field")


def test_empty_fields_normalize_safely():
    row = {"itemid": "x", "article": "", "appno": "", "kpdate": ""}
    case = Case.model_validate(row)
    assert case.articles == []
    assert case.appno == []
    assert case.kp_date is None


def test_rank_parsed_to_float():
    case = Case.model_validate({**SAMPLE_ROW, "Rank": "13.45"})
    assert case.rank == 13.45


def test_rank_empty_and_garbage_normalize_to_none():
    assert Case.model_validate({**SAMPLE_ROW, "Rank": ""}).rank is None
    assert Case.model_validate({**SAMPLE_ROW, "Rank": "n/a"}).rank is None
    assert Case.model_validate(SAMPLE_ROW).rank is None


def test_collection_result_count_defaults_to_none():
    coll = CaseCollection([Case.model_validate(SAMPLE_ROW)])
    assert coll.result_count is None
    coll.result_count = 1234
    assert coll.result_count == 1234


def test_case_collection_to_dataframe():
    pd = pytest.importorskip("pandas")
    cases = CaseCollection(
        [
            Case.model_validate(SAMPLE_ROW),
            Case.model_validate({**SAMPLE_ROW, "itemid": "001-99999", "appno": "99/99"}),
        ]
    )
    df = cases.to_dataframe()
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "itemid" in df.columns
    assert "articles" in df.columns


def test_case_collection_to_jsonl(tmp_path):
    cases = CaseCollection([Case.model_validate(SAMPLE_ROW)])
    out = tmp_path / "cases.jsonl"
    cases.to_jsonl(str(out))
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "001-94054" in lines[0]


def test_derived_fields_default_none_and_ignored_when_absent():
    case = Case.model_validate(SAMPLE_ROW)
    assert case.french_itemid is None
    assert case.text_source_itemid is None
    assert case.text_source_language is None
    assert case.text_provenance is None


def test_derived_fields_round_trip_through_json():
    case = Case.model_validate(SAMPLE_ROW)
    case.french_itemid = "001-99998"
    case.text_source_itemid = "001-99998"
    case.text_source_language = "FRE"
    reloaded = Case.model_validate(case.model_dump(mode="json"))
    assert reloaded.french_itemid == "001-99998"
    assert reloaded.text_source_itemid == "001-99998"
    assert reloaded.text_source_language == "FRE"


def test_text_provenance_property():
    # Own text: not a fallback.
    own = Case.model_validate(SAMPLE_ROW)
    own.text_source_itemid = own.itemid
    own.text_source_language = "ENG"
    prov = own.text_provenance
    assert prov is not None
    assert prov.is_fallback is False
    assert prov.source_language == "ENG"

    # French sibling: is a fallback.
    fb = Case.model_validate(SAMPLE_ROW)
    fb.text_source_itemid = "001-77777"
    fb.text_source_language = "FRE"
    assert fb.text_provenance is not None
    assert fb.text_provenance.is_fallback is True


def test_from_records_scrubs_nan_artifacts():
    rows = [
        {"itemid": "001-1", "ecli": float("nan"), "represented_by": "nan"},
        {"itemid": "001-2", "docname": "REAL NAME"},
    ]
    coll = CaseCollection.from_records(rows)
    assert len(coll) == 2
    assert coll[0].ecli is None
    assert coll[0].represented_by is None
    assert coll[1].docname == "REAL NAME"


def test_from_dataframe_round_trip():
    pytest.importorskip("pandas")
    cases = CaseCollection(
        [
            Case.model_validate(SAMPLE_ROW),
            Case.model_validate({**SAMPLE_ROW, "itemid": "001-99999"}),
        ]
    )
    df = cases.to_dataframe()
    back = CaseCollection.from_dataframe(df)
    assert [c.itemid for c in back] == ["001-94054", "001-99999"]
    assert back[0].articles == ["8", "14"]
