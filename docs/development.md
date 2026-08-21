# Development Guide

## Environment

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[all,dev]"
```

The package supports Python 3.11–3.14. The local project and mypy default are
Python 3.14. Ruff targets Python 3.11 syntax, and CI imports and tests the full
package on every supported minor version so the compatibility floor remains
enforced independently of third-party stub syntax.

## Tests

```bash
pytest
pytest -v
```

The tests are intended to be offline and CI-safe. Network behavior is mocked
with inline fixtures.

Useful focused runs:

```bash
pytest tests/test_cli.py
pytest tests/test_query_builder.py tests/test_query_dsl.py
pytest tests/test_search_client.py tests/test_smart_fetch.py
pytest tests/test_mcp.py
pytest tests/test_citations.py tests/test_citations_metrics.py tests/test_citation_resolution.py
pytest tests/test_exec_query.py tests/test_exec_models.py
pytest tests/test_exec_imports.py tests/test_exec_pipeline_completion.py
```

Live network, browser, LLM, and OCR checks are isolated behind a marker:

```bash
HUDOC_EXEC_LIVE_TESTS=1 pytest -q -m live tests/test_exec_live.py
HUDOC_CITATION_LIVE_TESTS=1 pytest -q -m live tests/test_citation_live.py
```

The public default fixture is `57818/09` / `004-47097`. Override it with
`HUDOC_EXEC_LIVE_APPNO` and `HUDOC_EXEC_LIVE_CASE_ID`. OCR also requires
`HUDOC_EXEC_LIVE_SCANNED_PDF` and `MISTRAL_API_KEY`.

## Linting And Types

```bash
ruff check .
mypy hudoc_py
```

Ruff settings and mypy settings live in [pyproject.toml](../pyproject.toml).

## Package Structure

```text
hudoc_py/
  _aio.py       async high-level HUDOC main API
  _sync.py      synchronous facade over the async API
  cli.py        argparse command line interface
  config.py     endpoints, rate limits, cache paths, provider defaults
  main/         HUDOC main query construction, client, downloader, DSL
  execution/    HUDOC-EXEC discovery, download, conversion, OCR, and manifests
  models/       Pydantic data models
  text/         conversion, segmentation, opinion parsing
  citations/    reporter parsing, authority import, resolution/review, metrics, HTML view
  extractors/   generic structured-extractor registry (no domain prompts)
  studies/      bounded study specifications, runners, jobs, and schemas
  retrieval/    portable embeddings, hybrid search, and benchmarks
  graphs/       shared graph contract, adapters, and offline exports
  llm/          Gemini/OpenAI-compatible providers and batch helpers
  pipeline/     checkpointed concurrent runner and validation export
  mcp/          MCP server and Claude Desktop installer
  cache/        local Parquet helper layer
  utils/        JSONL, retry, and run-log utilities
```

## Documentation Checklist

When changing public behavior, update the relevant docs:

- README for changed installation, major workflows, or public positioning.
- `docs/python-api.md` for import surfaces, filters, models, or examples.
- `docs/cli.md` for argparse commands or flags.
- `docs/mcp.md` for MCP tool changes.
- `docs/llm-extraction.md` for extraction schemas, provider behavior, or
  batch workflow changes.
- `docs/citation-resolution.md` for SCL parsing, authority, acceptance rules,
  review artifacts, or graph completeness changes.

## Release Notes

This package is alpha. Before tagging a release:

1. Run the full test suite.
2. Check `echr-py --help` and command-group help under the project venv.
3. Verify README examples against the current API.
4. Confirm `pyproject.toml` version and project URLs.
5. Build from a clean `build/` directory and run `python
   scripts/check_wheel.py dist/echr_py-*.whl`.
6. Install that wheel into a fresh environment outside the repository and
   exercise the API and CLI.
7. Avoid committing local files from `Resources/` or generated caches.
8. Refresh both official English and French citation PDFs, import/merge them,
   and record edition dates, checksums, parser version, row counts, and stable
   bilingual-equivalence counts. The PDFs themselves stay untracked.
9. Optionally run `scripts/live_study_canary.py` locally for three records in
   realtime and native batch mode against explicit Gemini and official OpenAI
   model IDs. Supply current pricing explicitly; do not store credentials or
   infer a model. This is a local maintainer check: the release workflow holds
   no provider credentials and does not call any model provider.
10. Confirm the compatibility gates for native `represented_by`, deciding
    benches, separate-opinion provenance, HUDOC-EXEC acquisition,
    unchanged SCL graph counts, and the public package-module allowlists.
11. Publish through `.github/workflows/publish.yml`. Its protected
    `testpypi` and `pypi` environments use OIDC trusted
    publishing, build provenance attestations, wheel-content validation, and
    isolated installation. Verify TestPyPI before creating/publishing the
    GitHub release and final tag.
