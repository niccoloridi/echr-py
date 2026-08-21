# MCP Server Guide

`echr-py` includes a read-only-by-default MCP stdio server. The ordinary
server fetches public HUDOC/HUDOC-EXEC data and queries user-built local
indexes; it exposes no model-backed or mutating job tools.

Install with:

```bash
python -m pip install "echr-py[mcp]"
```

## Run Manually

```bash
echr-py mcp
```

Equivalent module form:

```bash
python -m hudoc_py.mcp
```

## Claude Code setup

With `uv` and Claude Code installed, the released package can be registered at
user scope without cloning this repository:

```bash
claude mcp add --scope user --transport stdio echr-py -- \
  "$(command -v uvx)" --from 'echr-py[mcp]==0.2.1' echr-py mcp
claude mcp get echr-py
```

PowerShell equivalent:

```powershell
$uvx = (Get-Command uvx).Source
claude mcp add --scope user --transport stdio echr-py -- $uvx `
  --from "echr-py[mcp]==0.2.1" echr-py mcp
claude mcp get echr-py
```

This starts a local stdio process. Claude Code uses the user's Claude login.
The local server needs no Anthropic API key; it queries public HUDOC endpoints
unless a tool is pointed at a local corpus. Model-backed study jobs remain
disabled unless the user starts the separately configured opt-in job surface.

For a source checkout, point Claude Code at the environment directly:

```bash
claude mcp add --scope user --transport stdio echr-py -- \
  /absolute/path/to/echr-py/.venv/bin/python -m hudoc_py.mcp
```

## Claude Desktop setup

Claude Desktop supports local MCP servers as installable `.mcpb` desktop
extensions. A uv-based bundle can resolve the pinned `echr-py[mcp]` release
without a hosted MCP service. The bundle will be attached to a GitHub release
only after the corresponding PyPI artifact is published and the extension has
passed clean-machine tests on the supported Desktop platforms.

For a source checkout today, the included installer merges an `echr-py` server
entry into the Claude Desktop config and backs up the existing file first:

```bash
.venv/bin/python -m hudoc_py.mcp.install
```

On macOS, the target file is:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

Restart Claude Desktop after installation.

Manual configuration:

```json
{
  "mcpServers": {
    "echr-py": {
      "command": "/absolute/path/to/echr-py/.venv/bin/python",
      "args": ["-m", "hudoc_py.mcp"]
    }
  }
}
```

## Verify With Inspector

```bash
npx @modelcontextprotocol/inspector .venv/bin/python -m hudoc_py.mcp
```

## Privacy and trust boundary

The default MCP server has no developer telemetry. Public searches and document
requests go directly from the user's machine to HUDOC or HUDOC-EXEC; local
indexes and artifacts remain local. The ordinary read-only tools do not read
provider API keys. Local cache files, when a command creates them, live under
`$ECHR_PY_DATA_DIR` or `~/.echr-py/data/` and can be deleted by the user.

Court documents are external, untrusted text. A model may encounter quoted
instructions or other prompt-like language inside them. Treat retrieved text as
evidence to inspect, not instructions to execute, and review tool arguments and
citations before relying on a model-produced conclusion. The opt-in study-job
surface has separate path, provider, hook, and budget allowlists and is never
enabled by the Desktop bundle.

## Generate The Live Terminal Demos

The first two repository README animations are produced by real stdio MCP
sessions and read-only public HUDOC queries. Their tool sequences are scripted,
so no model is involved:

```bash
python scripts/mcp_demo_gif.py --live --out docs/images/mcp-terminal-demo.gif

# Chained search → segmentation → inclusive citation-resolution workflow
python scripts/mcp_research_demo_gif.py --live \
  --out docs/images/mcp-research-demo.gif
```

The script records only compact public result metadata. It does not render
environment variables, credentials, or raw protocol payloads.

The Claude network animation is different: it comes from an authenticated,
non-interactive Claude Code run in which Sonnet chose three tools from a
strictly allowlisted, read-only MCP surface. The checked-in packager accepts
Claude's verbose stream JSON, verifies the tool sequence and model identity,
and exports the exact graph returned by MCP:

```bash
python scripts/claude_mcp_network_demo.py /path/to/claude-stream.jsonl
```

The raw protocol stream is deliberately not tracked because it duplicates
large public tool payloads. Its SHA-256, exact tool arguments, reported model
usage, final JSON answer, graph artifacts, and output hashes are retained in
[`provenance.json`](examples/claude-mcp-ocalan/provenance.json). In the pinned
run, Claude Code reported `claude-sonnet-5` for the reasoning turn and a small
`claude-haiku-4-5-20251001` auxiliary routing call. The cost field is Claude
Code's API-style estimate, not evidence of an additional charge to a Claude
subscription.

GitHub's ordinary file view displays HTML source and does not execute it.
Download the original single-file HTML and open it locally for the interactive
viewer.

## Current Tool List

The current server exposes these read-only tools:

| Tool | Purpose |
| --- | --- |
| `search_cases` | Search HUDOC main and return case summaries plus total match count |
| `count_cases` | Count HUDOC main matches without fetching rows |
| `search_and_read` | Search, fetch top-N texts, and return a selected section |
| `get_case_metadata` | Fetch full typed metadata for one case by `appno` or `itemid` |
| `get_case_text` | Fetch full judgment text or `the_law`/`dispositif` |
| `list_document_versions` | List every language record for an application or exact ECLI, with rendition classification and download URLs |
| `get_dispositive_paragraphs` | Return individual operative rulings with stable source addresses and vote formulas |
| `search_local_paragraphs` | Search a user-built lexical, semantic, or hybrid paragraph index without hosted data |
| `list_articles_referenced` | Return Convention articles recorded on a case |
| `get_case_segments` | Fetch source-aware rich sections, spans, diagnostics, and optionally the document spine |
| `get_case_citations` | Parse typed SCL mentions; optionally resolve exact targets and locate occurrences in source paragraphs |
| `get_case_citation_network` | Return a compact occurrence-weighted `hudoc-graph/v1` network with majority/opinion provenance |
| `search_exec` | Search HUDOC-EXEC execution cases |
| `search_exec_documents` | Search action plans, reports, decisions, communications, and resolutions |
| `get_exec_document` | Fetch a HUDOC-EXEC document body by `content_store_id` |
| `search_keypoints` | Find official HUDOC legal-keyword IDs from label text |

## Notes

- `search_cases` returns summaries. Use `get_case_metadata` for the full
  Pydantic model dump.
- `search_and_read` is best for relevance workflows: pass a full-text `text`
  query and keep `sort="relevance"`.
- `get_exec_document` requires the internal `content_store_id`, not the
  human-readable `execidentifier`.
- `get_case_citations(resolve=false)` parses without guessing. Set
  `resolve=true` for exact-document resolution; application numbers alone do
  not select among admissibility, merits, or later procedural documents. For
  measurement-grade corpus graphs, use the CLI workflow and reviewed artifacts
  in [citation-resolution.md](citation-resolution.md).
- `get_case_citations(include_occurrences=true)` fetches structured HUDOC HTML
  and returns deterministic source-paragraph occurrences. It does not invoke
  an LLM. `resolve` independently controls whether those rows include exact
  target-document identities. Set `citation_scope="inclusive"` to discover
  citations absent from SCL across the majority text, appendices, and each
  separately identified opinion. The compatibility default remains `"scl"`.
  Set `include_target_paragraphs=true` together with `resolve=true` to fetch
  exact cited documents and return deterministic source-to-target
  `paragraph_edges`.
- Text responses can be large. Tools that accept `max_chars` or section
  filters should use them when possible.
- `get_case_segments(..., include_text=false, include_spine=false)` returns a
  compact `spine_summary`, bench, opinion identities, lengths, spans, and
  diagnostics without sending full judgment text to the client.
- `get_case_segments(..., include_spine=true)` returns the full versioned block
  spine. Request it only when block-level content is required.
- `get_case_citation_network` performs deterministic inclusive citation
  extraction and resolution, then returns a compact occurrence-weighted graph.
  Its source nodes preserve majority and individual-opinion identities. It
  does not alter the authoritative SCL decision graph. In occurrence v3,
  authority-specific rows can share one printed `locus_id`; count unique loci
  when the research question concerns physical printed citation envelopes.
- `list_document_versions(ecli=...)` is the exact-document route. Application
  number mode can include judgments, decisions, resolutions, and legal
  summaries from different procedural stages.
- `get_case_segments` also returns the independently parsed deciding `bench`.
  It is sourced from the composition front matter, not from opinion authors.
- `search_local_paragraphs` reads an index built with `echr-py local
  index-paragraphs`; semantic/hybrid mode additionally takes a verified
  embeddings directory. The server does not provide or host a corpus snapshot.
  The caller may select an existing local index path; the database is opened in
  SQLite read-only mode and is therefore correctly marked as local-world access.

## Opt-In Study Jobs

Model-backed study jobs are available only when the server operator starts an
explicitly constrained job service:

```bash
echr-py mcp --enable-jobs \
  --job-root /absolute/path/to/runs \
  --allow-input-root /absolute/path/to/corpora \
  --allow-provider openai \
  --allow-study citation-use \
  --pricing-file /absolute/path/to/prices.json \
  --max-job-budget-usd 5
```

This adds `create_study_job`, `get_study_job`, `list_study_jobs`,
`cancel_study_job`, `resume_study_job`, and `list_study_artifacts`. Inputs and
outputs are path-confined, providers and installed study hooks are allowlisted,
pricing and per-job limits are mandatory, and persisted state supports later
inspection or resume. Requests cannot supply arbitrary Python hook paths, read
environment variables, or expose credentials.

If an allowlisted study reaches a native Gemini/OpenAI batch stage, the job is
persisted as `waiting`; `resume_study_job` polls/retrieves it and
`cancel_study_job` calls the provider cancellation endpoint. The same explicit
model, pricing, path, and budget restrictions continue to apply.
