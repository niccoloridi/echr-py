# HUDOC-EXEC Acquisition

`echr-py` provides a provenance-preserving acquisition layer for the public
[HUDOC-EXEC](https://hudoc.exec.coe.int/) service. It can discover execution
cases and official source documents, link them to ECtHR applications, download
document bodies, convert local sources, and record resumable manifests.

## Scope

This is a source-acquisition interface, not a pre-coded implementation
dataset. It is intended for researchers who need an inspectable local copy of
official records with stable identifiers and provenance.

The package includes:

- typed case and document metadata;
- case and document search across official HUDOC-EXEC collections;
- application-number links between HUDOC and HUDOC-EXEC;
- HTML, DOCX, and PDF source acquisition;
- plain-text and Markdown conversion;
- opt-in OCR for image-only PDFs;
- concurrent, resumable downloads with JSONL manifests;
- a browser fallback for the public Related documents navigator; and
- neutral import of official CSV/XLSX exports.

Records are returned as official metadata and source content. The package does
not assign research categories or interpret implementation; downstream
projects remain responsible for their own transparent coding choices.

## Install

The metadata client is part of the base package. Add only the source formats
you need:

```bash
python -m pip install "echr-py[exec-docs]"  # local PDF/DOCX conversion
python -m pip install "echr-py[scrape]"     # browser fallback
python -m pip install "echr-py[ocr]"        # optional scanned-PDF OCR
```

## Python

```python
from hudoc_py.execution import fetch_case, fetch_text, search, search_documents

cases = search(
    state="ITA",
    supervision="enhanced",
    is_closed=False,
    limit=50,
)

case = fetch_case("57818/09", with_documents=True)
plans = search_documents(collection="acp", appno="57818/09", limit=100)

for plan in plans:
    if plan.content_store_id:
        markdown = fetch_text(plan.content_store_id, format="md")
```

The asynchronous equivalents are in `hudoc_py.execution.aio`. Sync functions
raise a clear error inside an existing event loop so applications do not
silently nest event loops.

## CLI

```bash
# Case-level metadata
echr-py exec search --state ITA --supervision enhanced --closed false \
  --limit 100 --out cases.parquet

# Official source-document metadata
echr-py exec search-documents --collection acp --appno 57818/09 \
  --language ENG --out plans.jsonl

# A case record with its linked official documents
echr-py exec fetch-case 57818/09 --out case.json

# Concurrent, resumable raw acquisition and local text conversion
echr-py exec download-raw --in plans.jsonl --out sources/ \
  --manifest sources/manifest.jsonl --concurrency 10 --extract-text

# Convert one downloaded source to Markdown
echr-py exec extract-text --in sources/pdf/document.pdf \
  --out sources/text/document.md --markdown
```

`download-raw` uses each row's internal `execcontentstoreid`; the human-readable
`execidentifier` is not the download key. Successful identifiers are skipped on
resume. Failures stay visible in the manifest.

The `--concurrency` setting is intentionally explicit. Higher values may reduce
elapsed time, but users should select a courteous rate and expect upstream
service variability.

## Official collections

The exported `COLLECTION_CODES` mapping includes cases (`CEC`), action plans
(`acp`), action reports (`acr`), government/applicant/NGO/NHRI and other
communications, Committee decisions and notes, and resolutions. The original
collection fields are retained in every typed record.

## MCP

The default read-only MCP server exposes:

- `search_exec` for case-level metadata;
- `search_exec_documents` for official document collections; and
- `get_exec_document` for a source body by `content_store_id`.

These tools perform no model call and return no substantive classification of
the downloaded material.

## Reproducibility

For a frozen acquisition, retain:

- the query or immutable input rows;
- every source identifier and URL;
- the raw files;
- the downloader JSONL manifest;
- package and Python versions; and
- checksums for the input rows and downloaded artifacts.

HUDOC-EXEC is a live public service. Re-running an unpinned query later can
legitimately return updated metadata or additional documents.
