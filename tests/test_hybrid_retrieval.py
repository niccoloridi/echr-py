"""Portable exact embeddings and reciprocal-rank hybrid retrieval."""

import json
import sqlite3

import pytest

from hudoc_py.local import build_paragraph_index
from hudoc_py.retrieval import (
    EmbeddingIndex,
    HybridRetriever,
    benchmark_retrieval,
    build_embedding_index,
    reciprocal_rank_fusion,
    verify_embedding_index,
)


class FakeEmbeddings:
    name = "fake"
    model = "fake-2d"

    @staticmethod
    def _one(text):
        folded = text.casefold()
        return [float("privacy" in folded or "private" in folded), float("torture" in folded)]

    def embed_documents(self, texts):
        return [self._one(text) for text in texts]

    def embed_queries(self, texts):
        return [self._one(text) for text in texts]


@pytest.fixture
def paragraph_database(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    rows = [
        {
            "itemid": "privacy-case",
            "language": "ENG",
            "kp_date": "2020-01-01",
            "text": "THE FACTS\n\n1. Background.\n\nTHE LAW\n\n2. Private life requires safeguards.\n\nFOR THESE REASONS\n\n1. Holds.",
        },
        {
            "itemid": "torture-case",
            "language": "ENG",
            "kp_date": "2021-01-01",
            "text": "THE FACTS\n\n1. Background.\n\nTHE LAW\n\n2. Torture is absolutely prohibited.\n\nFOR THESE REASONS\n\n1. Holds.",
        },
    ]
    (corpus / "texts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    database = tmp_path / "paragraphs.sqlite"
    build_paragraph_index(corpus, database)
    return database


def test_exact_semantic_and_hybrid_search(paragraph_database, tmp_path):
    embeddings = tmp_path / "embeddings"
    manifest = build_embedding_index(
        paragraph_database,
        embeddings,
        provider_name="fake",
        model="fake-2d",
        provider=FakeEmbeddings(),
        batch_size=1,
    )
    semantic = EmbeddingIndex(embeddings, database=paragraph_database, provider=FakeEmbeddings())

    dense = semantic.search("private", top_k=2, filters={"section": "the_law"})
    hybrid = HybridRetriever(
        paragraph_database,
        mode="hybrid",
        embeddings=embeddings,
        provider=FakeEmbeddings(),
        top_k=2,
    ).search("private", section="the_law")

    assert manifest.dimensions == 2
    assert manifest.model_revision is None
    assert dense[0]["itemid"] == "privacy-case"
    assert dense[0]["dense_rank"] == 1
    assert hybrid[0]["itemid"] == "privacy-case"
    assert hybrid[0]["lexical_rank"] == 1
    assert hybrid[0]["dense_rank"] == 1
    assert hybrid[0]["fused_rank"] == 1


def test_embedding_manifest_records_model_revision(paragraph_database, tmp_path):
    manifest = build_embedding_index(
        paragraph_database,
        tmp_path / "embeddings-revision",
        provider_name="sentence-transformers",
        model="example/model",
        model_revision="0123456789abcdef",
        provider=FakeEmbeddings(),
    )

    assert manifest.model_revision == "0123456789abcdef"


def test_embedding_index_can_select_substantive_sections(paragraph_database, tmp_path):
    manifest = build_embedding_index(
        paragraph_database,
        tmp_path / "body-embeddings",
        provider_name="fake",
        model="fake-2d",
        provider=FakeEmbeddings(),
        sections=["the_law"],
        batch_size=1,
    )

    assert manifest.count == 2
    assert manifest.metadata["section_filter"] == ["the_law"]


def test_embedding_section_filter_can_retain_footnotes(paragraph_database, tmp_path):
    with sqlite3.connect(paragraph_database) as connection:
        connection.execute(
            "UPDATE paragraphs SET footnote_id = 'ftn1' WHERE rowid = "
            "(SELECT rowid FROM paragraphs WHERE section = 'facts' LIMIT 1)"
        )
    manifest = build_embedding_index(
        paragraph_database,
        tmp_path / "body-with-footnotes",
        provider_name="fake",
        model="fake-2d",
        provider=FakeEmbeddings(),
        sections=["the_law"],
        include_footnotes=True,
        batch_size=1,
    )

    assert manifest.count == 3
    assert manifest.paragraph_schema_version == "hudoc-paragraphs.v3"
    assert manifest.metadata["include_footnotes"] is True


def test_embedding_index_refuses_stale_database(paragraph_database, tmp_path):
    embeddings = tmp_path / "embeddings"
    build_embedding_index(
        paragraph_database,
        embeddings,
        provider_name="fake",
        model="fake-2d",
        provider=FakeEmbeddings(),
    )
    with sqlite3.connect(paragraph_database) as connection:
        connection.execute("INSERT INTO metadata VALUES ('changed', 'yes')")

    verification = verify_embedding_index(embeddings, database=paragraph_database)

    assert verification["valid"] is False
    with pytest.raises(ValueError, match="database checksum"):
        EmbeddingIndex(embeddings, database=paragraph_database, provider=FakeEmbeddings())


def test_rrf_is_stable_and_keeps_separate_scores():
    lexical = [
        {"paragraph_id": "b", "lexical_score": -2},
        {"paragraph_id": "a", "lexical_score": -3},
    ]
    semantic = [
        {"paragraph_id": "a", "dense_score": 0.9},
        {"paragraph_id": "b", "dense_score": 0.8},
    ]

    result = reciprocal_rank_fusion(lexical, semantic, top_k=2, rrf_k=60)

    assert [row["paragraph_id"] for row in result] == ["a", "b"]
    assert result[0]["lexical_rank"] == 2
    assert result[0]["dense_rank"] == 1


def test_retrieval_benchmark_reports_standard_metrics(paragraph_database, tmp_path):
    qrels = tmp_path / "qrels.jsonl"
    rows = HybridRetriever(paragraph_database, mode="lexical", top_k=5).search(
        "torture", section="the_law"
    )
    qrels.write_text(
        json.dumps(
            {
                "query_id": "q1",
                "query": "torture",
                "relevant_ids": [rows[0]["paragraph_id"]],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = benchmark_retrieval(
        HybridRetriever(paragraph_database, mode="lexical", top_k=5), qrels
    )

    assert result["recall_at_k"] == 1
    assert result["mrr_at_k"] == 1
    assert result["ndcg_at_k"] == 1
    assert result["mean_latency_ms"] >= 0
