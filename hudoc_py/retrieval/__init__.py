"""Portable lexical, dense and hybrid retrieval."""

from .benchmark import benchmark_retrieval
from .embeddings import (
    EmbeddingIndex,
    EmbeddingManifest,
    build_embedding_index,
    load_embedding_manifest,
    verify_embedding_index,
)
from .hybrid import HybridRetriever, reciprocal_rank_fusion
from .providers import (
    EmbeddingProvider,
    GeminiEmbeddingProvider,
    OpenAIEmbeddingProvider,
    SentenceTransformersProvider,
    get_embedding_provider,
)

__all__ = [
    "EmbeddingIndex",
    "EmbeddingManifest",
    "EmbeddingProvider",
    "GeminiEmbeddingProvider",
    "HybridRetriever",
    "OpenAIEmbeddingProvider",
    "SentenceTransformersProvider",
    "benchmark_retrieval",
    "build_embedding_index",
    "get_embedding_provider",
    "load_embedding_manifest",
    "reciprocal_rank_fusion",
    "verify_embedding_index",
]
