"""Deterministic retrieval evaluation over labelled qrels."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from ..utils.jsonl import iter_jsonl
from .hybrid import HybridRetriever


def _dcg(relevances: list[int]) -> float:
    return sum(value / math.log2(index + 2) for index, value in enumerate(relevances))


def benchmark_retrieval(
    retriever: HybridRetriever,
    qrels_path: str | Path,
    *,
    k: int | None = None,
) -> dict[str, Any]:
    rows = list(iter_jsonl(qrels_path))
    k = k or retriever.top_k
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    latencies: list[float] = []
    details = []
    for row in rows:
        relevant = {str(value) for value in row.get("relevant_ids", [])}
        started = time.perf_counter()
        hits = retriever.search(str(row["query"]))[:k]
        latencies.append((time.perf_counter() - started) * 1000)
        found = [str(hit["paragraph_id"]) for hit in hits]
        binary = [int(value in relevant) for value in found]
        recall = len(set(found) & relevant) / len(relevant) if relevant else 0.0
        first = next((index for index, value in enumerate(binary, 1) if value), None)
        rr = 1.0 / first if first else 0.0
        ideal = [1] * min(len(relevant), k)
        ndcg = _dcg(binary) / _dcg(ideal) if ideal else 0.0
        recalls.append(recall)
        reciprocal_ranks.append(rr)
        ndcgs.append(ndcg)
        details.append(
            {
                "query_id": row.get("query_id"),
                "found_ids": found,
                "recall": recall,
                "reciprocal_rank": rr,
                "ndcg": ndcg,
            }
        )
    divisor = max(1, len(rows))
    manifest = (
        retriever.embedding_index.manifest.model_dump(mode="json")
        if retriever.embedding_index
        else None
    )
    return {
        "schema_version": "hudoc-retrieval-benchmark/v1",
        "queries": len(rows),
        "k": k,
        "mode": retriever.mode,
        "recall_at_k": sum(recalls) / divisor,
        "mrr_at_k": sum(reciprocal_ranks) / divisor,
        "ndcg_at_k": sum(ndcgs) / divisor,
        "mean_latency_ms": sum(latencies) / divisor,
        "embedding_manifest": manifest,
        "details": details,
    }


__all__ = ["benchmark_retrieval"]
