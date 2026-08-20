"""Tests for the Lucene query builder."""

from __future__ import annotations

from datetime import date

import pytest

from hudoc_py.main.dsl import Q
from hudoc_py.main.queries import (
    DOCTYPES_JUDGMENTS,
    SORT_DATE_ASC,
    SORT_DATE_DESC,
    SORT_RELEVANCE,
    build_search_query,
    resolve_sort,
)


def test_default_includes_site_doctypes_and_languages():
    q = build_search_query()
    assert "contentsitename:ECHR" in q
    assert "doctype=HEJUD" in q
    assert "doctype=HFJUD" in q
    assert 'languageisocode="ENG"' in q
    assert 'languageisocode="FRE"' in q


def test_languages_can_select_one_or_several_hudoc_flags():
    english = build_search_query(languages=("ENG",))
    assert 'languageisocode="ENG"' in english
    assert 'languageisocode="FRE"' not in english

    bilingual = build_search_query(languages=("ENG", "FRE"))
    assert 'languageisocode="ENG"' in bilingual
    assert 'languageisocode="FRE"' in bilingual


def test_date_range_uses_hudoc_long_format():
    q = build_search_query(date_from="2020-01-01", date_to=date(2022, 12, 31))
    assert 'kpdate>="2020-01-01T00:00:00.0Z"' in q
    assert 'kpdate<="2022-12-31T00:00:00.0Z"' in q


def test_article_single_value():
    q = build_search_query(article="3")
    assert 'article:"3"' in q


def test_article_multiple_values_or_grouped():
    q = build_search_query(article=["3", "8"])
    assert 'article:"3"' in q
    assert 'article:"8"' in q
    assert " OR " in q


def test_judgments_only_doctypes():
    q = build_search_query(doctypes=DOCTYPES_JUDGMENTS)
    assert "doctype=HEJUD" in q
    assert "doctype=HFJUD" in q
    assert "HEDEC" not in q


def test_itemid_quoted():
    q = build_search_query(itemid="001-94054")
    assert 'itemid:"001-94054"' in q


def test_appno_multiple_or_grouped_and_quoted():
    q = build_search_query(appno=["46221/99", "12738/10"])
    assert '"46221/99"' in q and '"12738/10"' in q


def test_extra_clause_appended():
    q = build_search_query(extra='separateopinion:"TRUE"')
    assert 'separateopinion:"TRUE"' in q


def test_conclusion_quoted_phrase():
    q = build_search_query(conclusion="Violation of Article 3")
    assert 'conclusion:"Violation of Article 3"' in q


def test_kpthesaurus_and_concepts_field_names():
    q = build_search_query(kpthesaurus="350", concepts="Expulsion")
    assert 'kpthesaurus:"350"' in q
    assert 'ECHRConcepts:"Expulsion"' in q


def test_docname_quoted():
    q = build_search_query(docname="McCann")
    assert 'docname:"McCann"' in q


def test_body_alias_maps_to_doctypebranch():
    q = build_search_query(body="grand-chamber")
    assert 'doctypebranch:"GRANDCHAMBER"' in q


def test_body_unknown_value_uppercased():
    q = build_search_query(body="admissibility")
    assert 'doctypebranch:"ADMISSIBILITY"' in q


def test_body_merges_with_doctypebranch():
    q = build_search_query(body="chamber", doctypebranch="GRANDCHAMBER")
    assert 'doctypebranch:"GRANDCHAMBER"' in q
    assert 'doctypebranch:"CHAMBER"' in q
    assert " OR " in q


def test_separate_opinion_boolean():
    assert 'separateopinion:"TRUE"' in build_search_query(separate_opinion=True)
    assert 'separateopinion:"FALSE"' in build_search_query(separate_opinion=False)
    assert "separateopinion" not in build_search_query()


def test_ecli_and_collection():
    q = build_search_query(ecli="ECLI:CE:ECHR:1995:0927JUD001898491", collection="JUDGMENTS")
    assert 'ecli:"ECLI:CE:ECHR:1995:0927JUD001898491"' in q
    assert 'documentcollectionid:"JUDGMENTS"' in q


def test_advisory_fields():
    q = build_search_query(advop_identifier="P16-2018-001", advop_status="Delivered")
    assert 'advopidentifier:"P16-2018-001"' in q
    assert 'advopstatus:"Delivered"' in q


def test_where_q_expression_anded():
    q = build_search_query(where=Q.article("3") | Q.article("8"))
    assert '(article:"3" OR article:"8")' in q
    assert " AND (" in q


def test_where_equivalent_to_extra():
    via_where = build_search_query(where=Q.raw('conclusion:"Violation of Article 3"'))
    via_extra = build_search_query(extra='conclusion:"Violation of Article 3"')
    assert via_where == via_extra


def test_resolve_sort_aliases():
    assert resolve_sort("relevance") == SORT_RELEVANCE
    assert resolve_sort("date-desc") == SORT_DATE_DESC
    assert resolve_sort("date-asc") == SORT_DATE_ASC
    assert resolve_sort(None) == SORT_RELEVANCE
    assert resolve_sort("") == SORT_RELEVANCE


def test_resolve_sort_raw_passthrough():
    assert resolve_sort("itemid Ascending") == "itemid Ascending"


def test_resolve_sort_invalid_raises():
    with pytest.raises(ValueError):
        resolve_sort("newest-first")
