"""Portable Parquet embedding indexes and chunked exact cosine search."""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .. import __version__
from ..local.paragraphs import paragraph_index_metadata
from .providers import EmbeddingProvider, get_embedding_provider


class EmbeddingManifest(BaseModel):
    schema_version: str = "hudoc-embeddings/v1"
    provider: str
    model: str
    model_revision: str | None = None
    dimensions: int
    normalized: bool = True
    query_prompt: str = ""
    document_prompt: str = ""
    count: int
    database_sha256: str
    paragraph_schema_version: str
    source_table_sha256: str | None = None
    corpus_manifest_sha256: str | None = None
    created_at: str
    hudoc_py_version: str
    parquet_sha256: str | None = None
    accelerator: str | None = None
    accelerator_sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise(values: Any) -> Any:
    import numpy as np

    array = np.asarray(values, dtype="float32")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


def _sqlite_arrow_type(declared_type: str, pa: Any) -> Any:
    """Map SQLite type affinity to a stable nullable Arrow field type."""

    upper = declared_type.upper()
    if "INT" in upper:
        return pa.int64()
    if any(marker in upper for marker in ("REAL", "FLOA", "DOUB")):
        return pa.float64()
    if "BLOB" in upper or not upper:
        return pa.binary()
    return pa.string()


def build_embedding_index(
    database: str | Path,
    output_dir: str | Path,
    *,
    provider_name: str,
    model: str,
    model_revision: str | None = None,
    provider: EmbeddingProvider | None = None,
    batch_size: int = 128,
    query_prompt: str = "",
    document_prompt: str = "",
    accelerator: str = "exact",
    sections: list[str] | tuple[str, ...] | None = None,
    include_footnotes: bool = False,
) -> EmbeddingManifest:
    """Embed every source-addressed paragraph into bounded-memory Parquet."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    database = Path(database).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    provider = provider or get_embedding_provider(
        provider_name,
        model=model,
        model_revision=model_revision,
    )
    parquet = output / "embeddings.parquet"
    temporary = output / "embeddings.parquet.tmp"
    temporary.unlink(missing_ok=True)
    if accelerator not in {"exact", "faiss"}:
        raise ValueError("embedding accelerator must be exact or faiss")
    faiss = None
    if accelerator == "faiss":
        try:
            import faiss as faiss_module
        except ImportError as exc:
            raise ImportError("FAISS acceleration requires echr-py[embeddings-faiss]") from exc
        faiss = faiss_module
    writer: pq.ParquetWriter | None = None
    schema: pa.Schema | None = None
    dimensions: int | None = None
    count = 0
    index: Any = None
    selected_sections = tuple(dict.fromkeys(sections or ()))
    try:
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            source_fields = [
                pa.field(
                    str(column[1]),
                    _sqlite_arrow_type(str(column[2]), pa),
                    nullable=not bool(column[3]),
                )
                for column in connection.execute("PRAGMA table_info(paragraphs)")
            ]
            source_columns = {field.name for field in source_fields}
            if include_footnotes and "footnote_id" not in source_columns:
                raise ValueError(
                    "including footnotes requires a hudoc-paragraphs.v3 index"
                )
            if selected_sections:
                placeholders = ",".join("?" for _ in selected_sections)
                footnote_clause = " OR footnote_id IS NOT NULL" if include_footnotes else ""
                cursor = connection.execute(
                    f"SELECT * FROM paragraphs WHERE (section IN ({placeholders})"
                    f"{footnote_clause}) ORDER BY rowid",
                    selected_sections,
                )
            elif include_footnotes:
                cursor = connection.execute(
                    "SELECT * FROM paragraphs WHERE footnote_id IS NOT NULL ORDER BY rowid"
                )
            else:
                cursor = connection.execute("SELECT * FROM paragraphs ORDER BY rowid")
            while batch := cursor.fetchmany(batch_size):
                rows = [dict(row) for row in batch]
                texts = [document_prompt + str(row["text"]) for row in rows]
                matrix = _normalise(provider.embed_documents(texts))
                if dimensions is None:
                    dimensions = int(matrix.shape[1])
                    schema = pa.schema(
                        [*source_fields, pa.field("embedding", pa.list_(pa.float32(), dimensions))]
                    )
                    if faiss is not None:
                        index = faiss.IndexFlatIP(dimensions)
                elif int(matrix.shape[1]) != dimensions:
                    raise ValueError("embedding provider returned inconsistent dimensions")
                assert schema is not None
                for row, vector in zip(rows, matrix, strict=True):
                    row["embedding"] = vector.tolist()
                table = pa.Table.from_pylist(rows, schema=schema)
                if writer is None:
                    writer = pq.ParquetWriter(temporary, schema, compression="snappy")
                writer.write_table(table)
                if index is not None:
                    index.add(matrix)
                count += len(rows)
    except Exception:
        if writer is not None:
            writer.close()
        temporary.unlink(missing_ok=True)
        raise
    if writer is None or dimensions is None:
        raise ValueError("paragraph database is empty")
    writer.close()
    temporary.replace(parquet)
    metadata = paragraph_index_metadata(database)
    accelerator_sha256 = None
    if accelerator == "faiss":
        assert faiss is not None and index is not None
        faiss_path = output / "faiss.index"
        faiss.write_index(index, str(faiss_path))
        accelerator_sha256 = file_sha256(faiss_path)
    manifest = EmbeddingManifest(
        provider=provider_name,
        model=model,
        model_revision=model_revision,
        dimensions=dimensions,
        count=count,
        database_sha256=file_sha256(database),
        paragraph_schema_version=metadata.get("schema_version", "hudoc-paragraphs.v1"),
        source_table_sha256=metadata.get("source_table_sha256"),
        corpus_manifest_sha256=metadata.get("corpus_manifest_sha256"),
        query_prompt=query_prompt,
        document_prompt=document_prompt,
        created_at=dt.datetime.now(dt.UTC).isoformat(),
        hudoc_py_version=__version__,
        parquet_sha256=file_sha256(parquet),
        accelerator="faiss" if accelerator == "faiss" else None,
        accelerator_sha256=accelerator_sha256,
        metadata={
            "section_filter": list(selected_sections),
            "include_footnotes": include_footnotes,
        }
        if selected_sections or include_footnotes
        else {},
    )
    (output / "embedding-manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return manifest


def load_embedding_manifest(path: str | Path) -> tuple[Path, EmbeddingManifest]:
    root = Path(path).resolve()
    if root.is_file() and root.name == "embedding-manifest.json":
        manifest_path, root = root, root.parent
    else:
        manifest_path = root / "embedding-manifest.json"
    return root, EmbeddingManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def verify_embedding_index(
    path: str | Path, *, database: str | Path | None = None
) -> dict[str, Any]:
    root, manifest = load_embedding_manifest(path)
    parquet = root / "embeddings.parquet"
    errors = []
    if not parquet.exists() or file_sha256(parquet) != manifest.parquet_sha256:
        errors.append("embeddings parquet checksum mismatch")
    if database and file_sha256(database) != manifest.database_sha256:
        errors.append("paragraph database checksum mismatch")
    if manifest.accelerator == "faiss":
        accelerator = root / "faiss.index"
        if (
            not accelerator.exists()
            or file_sha256(accelerator) != manifest.accelerator_sha256
        ):
            errors.append("FAISS accelerator checksum mismatch")
    return {"valid": not errors, "errors": errors, "manifest": manifest.model_dump(mode="json")}


class EmbeddingIndex:
    def __init__(
        self,
        path: str | Path,
        *,
        database: str | Path,
        provider: EmbeddingProvider | None = None,
        chunk_size: int = 50_000,
    ):
        self.root, self.manifest = load_embedding_manifest(path)
        self.database = Path(database).resolve()
        verification = verify_embedding_index(self.root, database=self.database)
        if not verification["valid"]:
            raise ValueError("; ".join(verification["errors"]))
        self.provider = provider or get_embedding_provider(
            self.manifest.provider,
            model=self.manifest.model,
            model_revision=self.manifest.model_revision,
        )
        self.chunk_size = chunk_size

    def search(
        self,
        query: str,
        *,
        top_k: int = 25,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        import numpy as np
        import pyarrow.parquet as pq

        vector = _normalise(self.provider.embed_queries([self.manifest.query_prompt + query]))[0]
        if self.manifest.accelerator == "faiss" and not filters:
            try:
                import faiss
                import pandas as pd
            except ImportError:
                pass
            else:
                index = faiss.read_index(str(self.root / "faiss.index"))
                scores, indexes = index.search(vector.reshape(1, -1), top_k)
                frame = pd.read_parquet(self.root / "embeddings.parquet")
                output = []
                for rank, (score, position) in enumerate(
                    zip(scores[0], indexes[0], strict=False), 1
                ):
                    if position < 0:
                        continue
                    row = frame.iloc[int(position)].drop(labels=["embedding"]).to_dict()
                    row.update(
                        dense_score=float(score),
                        dense_rank=rank,
                        lexical_score=None,
                        lexical_rank=None,
                        fused_score=None,
                        fused_rank=None,
                    )
                    output.append(row)
                return output
        candidates: list[tuple[float, dict[str, Any]]] = []
        parquet = pq.ParquetFile(self.root / "embeddings.parquet")
        for batch in parquet.iter_batches(batch_size=self.chunk_size):
            frame = batch.to_pandas()
            if filters:
                for key, value in filters.items():
                    if value is None:
                        continue
                    if key == "date_from" and "document_date" in frame.columns:
                        frame = frame[frame["document_date"] >= value]
                    elif key == "date_to" and "document_date" in frame.columns:
                        frame = frame[frame["document_date"] <= value]
                    elif key in frame.columns:
                        frame = frame[frame[key] == value]
            if frame.empty:
                continue
            matrix = np.asarray(frame["embedding"].tolist(), dtype="float32")
            scores = matrix @ vector
            for score, (_, row) in zip(scores, frame.iterrows(), strict=False):
                value = row.drop(labels=["embedding"]).to_dict()
                candidates.append((float(score), value))
            if len(candidates) > top_k * 8:
                candidates = sorted(
                    candidates, key=lambda item: (-item[0], str(item[1].get("paragraph_id")))
                )[: top_k * 4]
        ranked = sorted(candidates, key=lambda item: (-item[0], str(item[1].get("paragraph_id"))))[
            :top_k
        ]
        output = []
        for rank, (score, row) in enumerate(ranked, 1):
            row.update(
                dense_score=score,
                dense_rank=rank,
                lexical_score=None,
                lexical_rank=None,
                fused_score=None,
                fused_rank=None,
            )
            output.append(row)
        return output


__all__ = [
    "EmbeddingIndex",
    "EmbeddingManifest",
    "build_embedding_index",
    "file_sha256",
    "load_embedding_manifest",
    "verify_embedding_index",
]
