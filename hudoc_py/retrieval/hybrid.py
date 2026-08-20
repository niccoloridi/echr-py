"""Lexical, semantic and reciprocal-rank-fused paragraph retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..local import search_paragraphs
from ..studies.models import RetrievalSpec
from .embeddings import EmbeddingIndex
from .providers import EmbeddingProvider


def reciprocal_rank_fusion(
    lexical: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
    *,
    top_k: int,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    for method, values in (("lexical", lexical), ("dense", semantic)):
        for rank, row in enumerate(values, 1):
            key = str(row["paragraph_id"])
            rows.setdefault(key, dict(row)).update({k: v for k, v in row.items() if v is not None})
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            rows[key][f"{method}_rank"] = rank
    ranked = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
    output = []
    for rank, key in enumerate(ranked, 1):
        row = rows[key]
        row["fused_score"] = scores[key]
        row["fused_rank"] = rank
        row.setdefault("lexical_score", None)
        row.setdefault("lexical_rank", None)
        row.setdefault("dense_score", None)
        row.setdefault("dense_rank", None)
        output.append(row)
    return output


class HybridRetriever:
    def __init__(
        self,
        database: str | Path,
        *,
        mode: str = "lexical",
        embeddings: str | Path | None = None,
        provider: EmbeddingProvider | None = None,
        top_k: int = 25,
        candidate_k: int = 100,
        rrf_k: int = 60,
        filters: dict[str, Any] | None = None,
    ):
        if mode not in {"lexical", "semantic", "hybrid"}:
            raise ValueError("retrieval mode must be lexical, semantic or hybrid")
        if mode != "lexical" and not embeddings:
            raise ValueError(f"{mode} retrieval requires an embedding index")
        self.database = Path(database).resolve()
        self.mode = mode
        self.top_k = top_k
        self.candidate_k = max(candidate_k, top_k)
        self.rrf_k = rrf_k
        self.filters = filters or {}
        self.embedding_index = (
            EmbeddingIndex(embeddings, database=self.database, provider=provider)
            if embeddings
            else None
        )

    @classmethod
    def from_spec(
        cls, spec: RetrievalSpec, *, provider: EmbeddingProvider | None = None
    ) -> HybridRetriever:
        if not spec.database:
            raise ValueError("retrieval database is required")
        return cls(
            spec.database,
            mode=spec.mode,
            embeddings=spec.embeddings,
            provider=provider,
            top_k=spec.top_k,
            candidate_k=spec.candidate_k,
            rrf_k=spec.rrf_k,
            filters=spec.filters,
        )

    def search(self, query: str, **filter_overrides: Any) -> list[dict[str, Any]]:
        filters = {**self.filters, **{k: v for k, v in filter_overrides.items() if v is not None}}
        filters = {k: v for k, v in filters.items() if v is not None}
        allowed = {
            "section",
            "language",
            "itemid",
            "source_component",
            "opinion_id",
            "date_from",
            "date_to",
        }
        unknown = set(filters) - allowed
        if unknown:
            raise ValueError(f"unsupported retrieval filters: {sorted(unknown)}")
        lexical = []
        semantic = []
        if self.mode in {"lexical", "hybrid"}:
            lexical = search_paragraphs(
                self.database,
                query,
                limit=self.candidate_k if self.mode == "hybrid" else self.top_k,
                **filters,
            )
        if self.mode in {"semantic", "hybrid"}:
            assert self.embedding_index is not None
            semantic = self.embedding_index.search(
                query,
                top_k=self.candidate_k if self.mode == "hybrid" else self.top_k,
                filters=filters,
            )
        if self.mode == "lexical":
            return lexical[: self.top_k]
        if self.mode == "semantic":
            return semantic[: self.top_k]
        return reciprocal_rank_fusion(lexical, semantic, top_k=self.top_k, rrf_k=self.rrf_k)


__all__ = ["HybridRetriever", "reciprocal_rank_fusion"]
