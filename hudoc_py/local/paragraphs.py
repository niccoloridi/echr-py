"""SQLite/FTS5 paragraph index for portable HUDOC corpora."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from ..text import segment_full
from ..utils.jsonl import iter_jsonl
from .registry import find_table

SCHEMA_VERSION = "hudoc-paragraphs.v3"


def _connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    database = Path(path).expanduser()
    if read_only:
        if not database.is_file():
            raise FileNotFoundError(f"Paragraph index does not exist: {database}")
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def build_paragraph_index(data_dir: str | Path, database: str | Path) -> dict[str, int]:
    """Build FTS5 from all language texts when present, else canonical texts."""
    rich_found = find_table("paragraphs", data_dir)
    found = rich_found or find_table("language_texts", data_dir) or find_table("texts", data_dir)
    if found is None:
        raise FileNotFoundError(f"No texts table under {data_dir}")
    text_path, _ = found
    db_path = Path(database)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS paragraphs_fts;
            DROP TABLE IF EXISTS paragraphs;
            DROP TABLE IF EXISTS metadata;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE paragraphs (
                paragraph_id TEXT PRIMARY KEY,
                itemid TEXT NOT NULL,
                para_id TEXT NOT NULL,
                block_id TEXT NOT NULL,
                para_num INTEGER,
                seq_order INTEGER,
                section TEXT,
                source_component TEXT,
                opinion_id TEXT,
                opinion_ordinal INTEGER,
                opinion_type TEXT,
                opinion_authors TEXT,
                opinion_joined_by TEXT,
                footnote_id TEXT,
                language TEXT,
                document_date TEXT,
                source_sha256 TEXT NOT NULL,
                text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE paragraphs_fts USING fts5(
                text, content='paragraphs', content_rowid='rowid', tokenize='unicode61'
            );
            """
        )
        connection.execute("INSERT INTO metadata VALUES (?, ?)", ("schema_version", SCHEMA_VERSION))
        connection.execute(
            "INSERT INTO metadata VALUES (?, ?)",
            ("source_table_sha256", hashlib.sha256(text_path.read_bytes()).hexdigest()),
        )
        corpus_manifest = Path(data_dir) / "manifest.json"
        if corpus_manifest.exists():
            connection.execute(
                "INSERT INTO metadata VALUES (?, ?)",
                (
                    "corpus_manifest_sha256",
                    hashlib.sha256(corpus_manifest.read_bytes()).hexdigest(),
                ),
            )
        documents = paragraphs = 0
        if rich_found is not None:
            import pyarrow.parquet as pq

            text_found = find_table("language_texts", data_dir) or find_table("texts", data_dir)
            document_metadata: dict[str, tuple[str | None, str]] = {}
            if text_found is not None:
                for record in iter_jsonl(text_found[0]):
                    itemid = str(record.get("itemid") or record.get("source_itemid") or "")
                    text = str(record.get("text") or "")
                    if itemid:
                        document_metadata[itemid] = (
                            record.get("kp_date") or record.get("date"),
                            hashlib.sha256(text.encode()).hexdigest(),
                        )
            seen_documents: set[str] = set()
            sequence: dict[str, int] = {}
            for batch in pq.ParquetFile(rich_found[0]).iter_batches(batch_size=10_000):
                for row in batch.to_pylist():
                    text = str(row.get("text") or "")
                    itemid = str(row.get("itemid") or "")
                    if not itemid or not text.strip() or row.get("type") == "heading":
                        continue
                    seq_order = sequence.get(itemid, 0)
                    sequence[itemid] = seq_order + 1
                    seen_documents.add(itemid)
                    para_id = str(row.get("para_id") or row.get("block_id"))
                    document_date, source_sha256 = document_metadata.get(
                        itemid, (None, hashlib.sha256(text.encode()).hexdigest())
                    )
                    cursor = connection.execute(
                        """INSERT INTO paragraphs
                        (paragraph_id,itemid,para_id,block_id,para_num,seq_order,section,
                         source_component,opinion_id,opinion_ordinal,opinion_type,
                         opinion_authors,opinion_joined_by,footnote_id,language,
                         document_date,source_sha256,text)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            f"{itemid}:{para_id}",
                            itemid,
                            para_id,
                            row.get("block_id"),
                            row.get("para_num"),
                            seq_order,
                            row.get("section"),
                            "opinion"
                            if row.get("opinion_id")
                            else ("appendix" if row.get("section") == "appendix" else "majority"),
                            row.get("opinion_id"),
                            row.get("opinion_ordinal"),
                            row.get("opinion_type"),
                            _json_array(row.get("opinion_authors")),
                            _json_array(row.get("opinion_joined_by")),
                            row.get("footnote_id"),
                            row.get("language"),
                            document_date,
                            source_sha256,
                            text,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO paragraphs_fts(rowid,text) VALUES (?,?)",
                        (cursor.lastrowid, text),
                    )
                    paragraphs += 1
            documents = len(seen_documents)
            return {"documents": documents, "paragraphs": paragraphs}
        for record in iter_jsonl(text_path):
            text = str(record.get("text") or "")
            itemid = str(record.get("itemid") or record.get("source_itemid") or "")
            if not text or not itemid:
                continue
            source_sha256 = hashlib.sha256(text.encode()).hexdigest()
            sections = segment_full(text, document_id=itemid)
            if sections.spine is None:
                continue
            documents += 1
            for seq_order, block in enumerate(sections.spine.blocks):
                if block.type == "heading" or not block.text.strip():
                    continue
                para_id = block.para_id or block.block_id
                paragraph_id = f"{itemid}:{para_id}"
                cursor = connection.execute(
                    """INSERT INTO paragraphs
                    (paragraph_id,itemid,para_id,block_id,para_num,seq_order,section,
                     source_component,opinion_id,opinion_ordinal,opinion_type,
                     opinion_authors,opinion_joined_by,footnote_id,language,
                     document_date,source_sha256,text)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        paragraph_id,
                        itemid,
                        para_id,
                        block.block_id,
                        block.para_num,
                        seq_order,
                        block.section,
                        "opinion"
                        if block.opinion_id
                        else ("appendix" if block.section == "appendix" else "majority"),
                        block.opinion_id,
                        block.opinion_ordinal,
                        block.opinion_type,
                        json.dumps(block.opinion_authors, ensure_ascii=False),
                        json.dumps(block.opinion_joined_by, ensure_ascii=False),
                        block.footnote_id,
                        record.get("source_language") or record.get("language"),
                        record.get("kp_date") or record.get("date"),
                        source_sha256,
                        block.text,
                    ),
                )
                connection.execute(
                    "INSERT INTO paragraphs_fts(rowid,text) VALUES (?,?)",
                    (cursor.lastrowid, block.text),
                )
                paragraphs += 1
    return {"documents": documents, "paragraphs": paragraphs}


def _json_array(value: Any) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def get_paragraphs(
    database: str | Path,
    itemid: str,
    *,
    para_id: str | None = None,
    section: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Retrieve paragraphs by document, stable local ID, and/or section."""
    clauses, params = ["itemid = ?"], [itemid]
    if para_id:
        clauses.append("para_id = ?")
        params.append(para_id)
    if section:
        clauses.append("section = ?")
        params.append(section)
    params.append(str(limit))
    sql = f"SELECT * FROM paragraphs WHERE {' AND '.join(clauses)} ORDER BY rowid LIMIT ?"
    with _connect(database, read_only=True) as connection:
        return [dict(row) for row in connection.execute(sql, params)]


def search_paragraphs(
    database: str | Path,
    query: str,
    *,
    section: str | None = None,
    language: str | None = None,
    itemid: str | None = None,
    source_component: str | None = None,
    opinion_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Full-text search paragraph bodies with optional metadata filters."""
    clauses, params = ["paragraphs_fts MATCH ?"], [query]
    if section:
        clauses.append("p.section = ?")
        params.append(section)
    if language:
        clauses.append("p.language = ?")
        params.append(language.upper())
    columns = _paragraph_columns(database)
    for column, value in (
        ("itemid", itemid),
        ("source_component", source_component),
        ("opinion_id", opinion_id),
    ):
        if value and column in columns:
            clauses.append(f"p.{column} = ?")
            params.append(value)
    if date_from and "document_date" in columns:
        clauses.append("p.document_date >= ?")
        params.append(date_from)
    if date_to and "document_date" in columns:
        clauses.append("p.document_date <= ?")
        params.append(date_to)
    params.append(str(limit))
    sql = f"""
        SELECT p.*, bm25(paragraphs_fts) AS score,
               snippet(paragraphs_fts, 0, '[', ']', ' … ', 32) AS snippet
        FROM paragraphs_fts JOIN paragraphs p ON p.rowid = paragraphs_fts.rowid
        WHERE {" AND ".join(clauses)} ORDER BY score LIMIT ?
    """
    with _connect(database, read_only=True) as connection:
        rows = [dict(row) for row in connection.execute(sql, params)]
    for rank, row in enumerate(rows, 1):
        row["lexical_score"] = row["score"]
        row["lexical_rank"] = rank
        row.setdefault("dense_score", None)
        row.setdefault("dense_rank", None)
        row.setdefault("fused_score", None)
        row.setdefault("fused_rank", None)
    return rows


def paragraph_index_metadata(database: str | Path) -> dict[str, str]:
    with _connect(database, read_only=True) as connection:
        try:
            return {
                str(row["key"]): str(row["value"])
                for row in connection.execute("SELECT key,value FROM metadata")
            }
        except sqlite3.OperationalError:
            return {"schema_version": "hudoc-paragraphs.v1"}


def _paragraph_columns(database: str | Path) -> set[str]:
    with _connect(database, read_only=True) as connection:
        return {str(row["name"]) for row in connection.execute("PRAGMA table_info(paragraphs)")}
