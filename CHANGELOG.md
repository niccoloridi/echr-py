# Changelog

All notable user-facing changes are recorded here. The project follows
semantic versioning during its alpha series.

## 0.2.1 – 2026-08-21

### Documentation

- Replace the lineage figure with an equivalent table. The diagram's labels
  overflowed their borders at common rendering widths; the table states the same
  stage-by-stage contract and stays legible at any width.
- Record the King's Digital Futures Institute Fellowship in the funding
  statement, the Zenodo software metadata, and `CITATION.cff`.

### Release engineering

- Remove the live provider canaries from the release workflow. The published
  pipeline holds no provider credentials and calls no model provider; the
  minimum-version CI job already proves the advertised provider contract.
- Strip the content-provenance manifest embedded in the project logo.

No library behaviour, artifact schema, citation identity, or benchmark figure
changes in this release.

## 0.2.0 – 2026-08-21

### Acquisition and structure

- Typed synchronous and asynchronous clients for HUDOC and HUDOC-EXEC.
- Cap-safe HUDOC search, direct URL ingestion, multilingual rendition
  discovery, concurrent document acquisition, frozen selections, checksums,
  typed failures, and deterministic corpus packaging.
- Source-aware document spines with stable paragraph addresses, canonical
  sections, deciding benches, individual opinions, linked footnotes,
  represented-by metadata, and individual dispositive rulings.
- Physical block addresses remain distinct when HUDOC splits one numbered
  legal paragraph across adjacent HTML elements; citation and target-pinpoint
  outputs retain the shared legal paragraph identity and all constituent
  blocks.
- HUDOC-EXEC official case/document discovery, resumable source downloads,
  local text/Markdown conversion, opt-in OCR, browser fallback, and provenance
  manifests. Results remain unlabelled source records for downstream research.

### Citations and graphs

- Deterministic SCL resolution and a separately versioned full-text citation
  occurrence layer covering majority text, appendices, individual opinions,
  and linked footnotes.
- Exact printed spans, source paragraphs, procedural target documents,
  citation-owned pinpoints, and deterministic target-paragraph verification.
- `citation-occurrence/v3` separates resolution-independent printed loci from
  authority-specific rows while retaining v1/v2 readers.
- Grouped preliminary-objections/merits citations retain one shared locus,
  target ordinals, procedural identities, and independently owned pinpoints.
- Footnote citations retain structured multi-invocation paragraph/opinion
  addresses and expand into distinct paragraph-graph edges where required.
- Conservative discovery diagnostics for ambiguous, unresolved, external,
  self-referential, and rejected candidates without forced graph promotion.
- Historical text discovery covers parenthesised judgment dates, anonymised
  single-letter applicants, comma-separated multi-party case names,
  French and corporate party forms, accent-insensitive aliases, four-letter
  established short forms, and name/date/reporter forms.
- The citing case's own application numbers and bare party aliases are no
  longer promoted as citations; a genuinely earlier document in the same
  application requires a name-bearing envelope with date and phase evidence.
- Exact application-number, title, date, document-kind, and procedural-phase
  conflicts now fail closed before automatic document promotion.
- Cached HUDOC lookup metadata is now authoritative input to offline reruns,
  so paragraph hydration cannot demote otherwise identical document targets.
- English/French official-authority import support, with the current bilingual
  v2 authority packaged by default, plus a checksummed historical catalogue,
  reviewed overrides, and reproducible benchmark import/compare commands.
- `hudoc-graph/v1` JSON, GEXF, and fully offline D3 HTML for SCL, inclusive,
  paragraph-aware, and custom NetworkX graphs.

### Retrieval and optional studies

- Portable SQLite FTS5 paragraph search plus checksummed float32 embedding
  artifacts, exact cosine search, deterministic reciprocal-rank hybrid fusion,
  filters, and retrieval benchmarking.
- Bounded YAML studies with explicit providers/models, stable task and attempt
  identities, verified evidence quotations, budgets, resumable artifacts, and
  structured JSONL/Parquet outputs.
- Realtime Gemini, OpenAI-compatible, and Anthropic providers; persisted native
  batch stages for Gemini and official OpenAI; no implicit model selection.
- Optional citation-use starter schemas whose labels cannot modify
  deterministic citations, resolutions, or graph counts.

### Interfaces and release integrity

- Matching Python, CLI, and read-only-by-default MCP acquisition and research
  surfaces, with path-confined opt-in MCP study jobs.
- Documented direct Claude Code registration and Claude Desktop installation;
  the default local server needs no Anthropic API credential.
- Local paragraph search now requires an existing SQLite index opened in
  read-only mode, matching the default MCP tool's read-only contract.
- Native HUDOC `representedby` metadata remains available as
  `Case.represented_by` in models, exports, local search, and studies.
- Wheel and source-distribution gates enforce total archive-member allowlists,
  exact package discovery, checksummed non-Python payloads, and neutral
  adversarial installation tests.
- Corrected minimum dependency versions for Gemini, OpenAI batch, FastMCP,
  citation-PDF parsing, and the HTTP client, with an explicit minimum-version
  CI job.
- Both the software licence and the Court-data rights notice ship with the
  wheel; release actions are commit-pinned, and Dependabot, CodeQL, and a
  security-reporting policy are configured.
- Repository-only opinion regressions use compact synthetic English and French
  fixtures rather than complete Court documents.
- Supports Python 3.11–3.14 with Ruff, mypy, offline tests, isolated wheel
  installation, trusted-publishing workflow, and provenance attestations.
