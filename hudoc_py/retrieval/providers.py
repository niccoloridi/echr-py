"""Explicit embedding-provider adapters."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    model: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformersProvider:
    name = "sentence-transformers"

    def __init__(self, *, model: str, revision: str | None = None):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError("Local embeddings require echr-py[embeddings-local]") from exc
        self.model = model
        self.revision = revision
        self._model = SentenceTransformer(model, revision=revision)

    def _encode(self, texts: list[str], *, query: bool) -> list[list[float]]:
        method = getattr(
            self._model,
            "encode_query" if query else "encode_document",
            self._model.encode,
        )
        values = method(texts, normalize_embeddings=True, convert_to_numpy=True)
        return values.astype("float32").tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, query=False)

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, query=True)


class GeminiEmbeddingProvider:
    name = "gemini"

    def __init__(self, *, model: str, client: Any = None):
        from ..llm.client import get_gemini_client

        self.model = model
        self._client = client or get_gemini_client()

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        response = self._client.models.embed_content(
            model=self.model,
            contents=texts,
            config={"task_type": task_type},
        )
        return [list(value.values) for value in response.embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_QUERY")


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(
        self,
        *,
        model: str,
        client: Any = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ImportError("OpenAI embeddings require echr-py[llm-openai]") from exc
            client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self._client = client

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model, input=texts)
        return [list(value.embedding) for value in response.data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)


def get_embedding_provider(
    name: str,
    *,
    model: str,
    model_revision: str | None = None,
    **kwargs: Any,
) -> EmbeddingProvider:
    resolved = name.lower()
    if resolved in {"sentence-transformers", "local", "sbert"}:
        return SentenceTransformersProvider(model=model, revision=model_revision)
    if model_revision is not None:
        raise ValueError("model revisions are supported only for local Sentence Transformers")
    if resolved == "gemini":
        return GeminiEmbeddingProvider(model=model, **kwargs)
    if resolved in {"openai", "openai-compatible", "openai-compat"}:
        return OpenAIEmbeddingProvider(model=model, **kwargs)
    raise ValueError(
        f"Unknown embedding provider {name!r}; use sentence-transformers, gemini or openai"
    )


__all__ = [
    "EmbeddingProvider",
    "GeminiEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "SentenceTransformersProvider",
    "get_embedding_provider",
]
