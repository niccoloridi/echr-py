"""Tests for the fluent Q query DSL."""

from __future__ import annotations

import pytest

from hudoc_py.main.dsl import Q, as_query_string


def test_leaf_rendering():
    assert Q.article("3").to_lucene() == 'article:"3"'
    assert Q.respondent("ITA").to_lucene() == 'respondent:"ITA"'
    assert Q.importance(1).to_lucene() == "importance:1"
    assert Q.doctype("HEJUD").to_lucene() == "doctype=HEJUD"


def test_and_composition():
    q = Q.article("3") & Q.respondent("ITA")
    assert q.to_lucene() == 'article:"3" AND respondent:"ITA"'


def test_and_flattens():
    q = Q.article("3") & Q.respondent("ITA") & Q.importance(1)
    assert q.to_lucene() == 'article:"3" AND respondent:"ITA" AND importance:1'


def test_or_flattens():
    q = Q.article("3") | Q.article("8") | Q.article("10")
    assert q.to_lucene() == 'article:"3" OR article:"8" OR article:"10"'


def test_mixed_precedence_parenthesized():
    q = (Q.article("3") & Q.respondent("ITA")) | Q.docname("McCann")
    assert q.to_lucene() == '(article:"3" AND respondent:"ITA") OR docname:"McCann"'


def test_or_inside_and_parenthesized():
    q = (Q.article("3") | Q.article("8")) & Q.respondent("ITA")
    assert q.to_lucene() == '(article:"3" OR article:"8") AND respondent:"ITA"'


def test_not_rendering():
    q = ~Q.body("committee")
    assert q.to_lucene() == 'NOT (doctypebranch:"COMMITTEE")'


def test_not_inside_and():
    q = Q.article("3") & ~Q.separate_opinion()
    assert q.to_lucene() == 'article:"3" AND NOT (separateopinion:"TRUE")'


def test_phrase_escapes_quotes():
    q = Q.phrase('the "living instrument" doctrine')
    assert q.to_lucene() == '"the \\"living instrument\\" doctrine"'


def test_text_near_rendering():
    q = Q.text_near("positive obligations", 5)
    assert q.to_lucene() == '"positive obligations"~5'


def test_text_wraps_in_parens():
    assert Q.text("torture inhuman").to_lucene() == "(torture inhuman)"


def test_body_alias():
    assert Q.body("grand-chamber").to_lucene() == 'doctypebranch:"GRANDCHAMBER"'


def test_date_range():
    q = Q.date_range("2020-01-01", "2022-12-31")
    assert 'kpdate>="2020-01-01T00:00:00.0Z"' in q.to_lucene()
    assert 'kpdate<="2022-12-31T00:00:00.0Z"' in q.to_lucene()


def test_str_equals_to_lucene():
    q = Q.article("3") & Q.respondent("ITA")
    assert str(q) == q.to_lucene()


def test_combine_with_non_q_raises():
    with pytest.raises(TypeError):
        Q.article("3") & "respondent:ITA"  # type: ignore[operator]


def test_as_query_string():
    assert as_query_string(None) is None
    assert as_query_string("article:3") == "article:3"
    assert as_query_string(Q.article("3")) == 'article:"3"'
    with pytest.raises(TypeError):
        as_query_string(42)  # type: ignore[arg-type]
