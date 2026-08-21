# echr-py

<p align="center">
  <img src="https://github.com/niccoloridi/echr-py/raw/main/Logo/echr_py_logo.png" alt="echr-py" width="260">
</p>

<p align="center">
  Acquire multilingual ECtHR case law, preserve its legal structure, resolve
  citations to exact documents and paragraphs, search locally, build datasets,
  and export publication-ready graphs.
</p>

<p align="center">
  <a href="https://github.com/niccoloridi/echr-py/actions/workflows/ci.yml"><img src="https://github.com/niccoloridi/echr-py/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.11%E2%80%933.14-3776AB.svg" alt="Python 3.11–3.14">
  <a href="https://github.com/niccoloridi/echr-py/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/version-0.2.2-blue.svg" alt="Version 0.2.2">
</p>

`echr-py` is a research engineering toolkit for the case law of the European
Court of Human Rights. It connects public
[HUDOC](https://hudoc.echr.coe.int/) and
[HUDOC-EXEC](https://hudoc.exec.coe.int/) acquisition to typed, source-addressed
research artifacts through Python, a command-line interface, and the Model
Context Protocol (MCP).

The distribution and primary command are now named `echr-py`. The established
`hudoc_py` Python import and the legacy `hudoc-py` command remain available for
backward compatibility.

It is designed for work where provenance matters. A result can retain the exact
HUDOC document, language version, section, paragraph, individual opinion,
footnote, citation span, cited procedural document, cited-paragraph pinpoint,
source checksum, and producing software revision.

The core acquisition, parsing, citation, and graph pipeline is deterministic.

Acquisition is concurrent and bounded. In a recorded July 2026 public-HUDOC
run it hydrated nine documents and wrote 478,151 characters of source-ordered
text in about 1.1 seconds at concurrency twelve, with the exact query,
settings, item IDs, hashes, and elapsed time recorded alongside the figure.

One inspectable chain runs from a HUDOC record to a verified paragraph edge.
Each stage keeps its own identity, so a later stage never overwrites an earlier
one:

| Stage | Identity retained at that stage |
| --- | --- |
| HUDOC record | item ID, ECLI, language, source checksum |
| Legal spine | canonical sections, stable block IDs, paragraph addresses, typography |
| Source identity | majority, individual opinion, footnote, invoking paragraph |
| Citation occurrence | exact printed span, supporting evidence, pinpoint ownership |
| Exact target | cited document, cited application, or an explicit unresolved scope |
| Target paragraph | printed pinpoint mapped to a verified block in the cited document |

That is the central idea: retain enough structure and provenance to move from a
public HUDOC record to a multilingual paragraph, a printed citation occurrence,
an exact target document, and, when the source supplies a pinpoint, a verified
target paragraph. Deterministic identity and graph layers stay separate from
optional, evidence-verified labels.

## Start here

```bash
python -m pip install "echr-py[citations,analysis]"
```

Build a rich case artifact and an offline paragraph-citation viewer:

```bash
echr-py corpus build --appno 46221/99 --rich-sections \
  --citations --out ocalan/

echr-py citations locate --in ocalan/cases.parquet \
  --resolution-dir ocalan/citations --scope inclusive \
  --out ocalan/citations

echr-py graph export --kind citation-paragraph --format html \
  --in ocalan/citations --out ocalan/paragraph-citations.html
```

Metadata page size and full-text download concurrency are separately tunable;
see [acquisition performance and tuning](https://github.com/niccoloridi/echr-py/blob/main/docs/acquisition-performance.md).

### Live bounded acquisition

<p align="center">
  <img src="https://raw.githubusercontent.com/niccoloridi/echr-py/main/docs/images/acquisition-terminal-demo.gif" alt="Live echr-py case and metadata acquisition" width="92%">
</p>

In the recorded July 2026 public-HUDOC run, `echr-py` selected twelve metadata records,
hydrated the nine records for which that response supplied usable official
text, parsed 1,578 source-order blocks, and wrote 478,151 text characters in
about 1.1 seconds with concurrency twelve. This is an observed demonstration,
not a service guarantee; the [provenance record](https://github.com/niccoloridi/echr-py/blob/main/docs/images/acquisition-terminal-demo.provenance.json)
contains the exact query, settings, item IDs, hashes and elapsed time.

Or use the Python API:

```python
from hudoc_py import fetch_case, search

cases = search(
    article="3",
    respondent="ITA",
    date_from="2020-01-01",
    importance=[1, 2],
    languages=("ENG", "FRE"),
    limit=250,
)
cases.to_parquet("italy-article-3.parquet")

ocalan = fetch_case(
    appno="46221/99",
    language="ENG",
    with_text=True,
    rich_sections=True,
)

print(ocalan.sections.the_law[:500])
print(ocalan.sections.bench.judges)
print(ocalan.sections.opinions)
```

The async API mirrors the synchronous surface:

```python
from hudoc_py import aio

cases = await aio.search(article="8", respondent="FRA", limit=50)
```

## What becomes possible

| Research task | What `echr-py` provides |
| --- | --- |
| Build a multilingual case-law corpus | Frozen selections, complete language-version discovery, resumable acquisition, checksums, typed failures, Parquet/JSONL/CSV/XLSX |
| Study precedent at paragraph level | Exact printed citation spans, uniquely corroborated procedural targets, citation-owned pinpoints, verified cited paragraphs |
| Compare majority and individual opinions | Stable opinion identities, type, ordinal, authors, joiners, source spans, and separate citation provenance |
| Analyse footnote citation practice | Linked footnote bodies, invoking paragraphs, majority/opinion context, and footnote-owned citation edges |
| Search concepts across English and French | SQLite FTS5, exact dense search, deterministic hybrid fusion, filters, stable paragraph IDs |
| Create a labelled research dataset | Bounded YAML studies, structured outputs, exact evidence quotations, budgets, checkpoints, validation, JSONL/Parquet export |
| Explore citation and custom networks | One graph contract with JSON, GEXF, and fully offline interactive HTML |
| Acquire implementation material | Typed HUDOC-EXEC case and document search, resumable source downloads, text/Markdown conversion, OCR, and manifests |

## The citation contract

Most citation networks stop at an application number or an edge between two
cases. `echr-py` keeps the layers researchers actually need separate:

```text
SCL decision graph
    └── Court-supplied, selective cited-authority baseline

Inclusive occurrence ledger
    └── every occurrence accepted by the deterministic discovery rules
        ├── majority / procedure / facts / operative / appendix
        ├── individual opinion identity and authors
        └── linked footnote identity and invoking paragraph

Paragraph graph
    └── source paragraph → exact cited document → verified cited paragraph
```

This separation matters. One citation in a paragraph may carry `§ 54`, while
the next carries no pinpoint; two opinions may cite the same target for
different propositions; and a footnote may cite an authority that never appears
in the judgment's SCL field. `echr-py` preserves those distinctions instead of
collapsing them into one count.

SCL is valuable bibliographic evidence, but it is not an exhaustive inventory
of printed citations – including in the majority judgment. Inclusive discovery
therefore starts from SCL without treating its absence as evidence that a
paragraph contains no citation.

Occurrence v3 separates a target-independent printed `locus_id` from
authority-specific `occurrence_id` rows. Compound references share a locus and
`citation_group_id`; each authority row retains its ordinal and independently
owned pinpoint. Footnote occurrences keep the physical footnote address and
structured addresses for every invoking paragraph or opinion.

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/niccoloridi/echr-py/main/docs/images/ocalan-citation-ledger.png" alt="Bilingual Öcalan citation ledger"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/niccoloridi/echr-py/main/docs/images/citation-layers.png" alt="SCL, inclusive, and paragraph citation layers"></td>
  </tr>
  <tr>
    <td>A bilingual source context with independently owned citation pinpoints. <a href="https://github.com/niccoloridi/echr-py/blob/main/docs/images/ocalan-citation-ledger-provenance.json">Provenance</a>.</td>
    <td>The SCL, occurrence, and paragraph layers remain separately versioned. <a href="https://github.com/niccoloridi/echr-py/blob/main/docs/images/citation-layers-provenance.json">Provenance</a>.</td>
  </tr>
</table>

### Resolution

The resolver combines printed SCL evidence, application numbers, ECLIs, HUDOC
item IDs, document dates and types, procedural phases, reporter references, a
packaged bilingual citation authority containing 42,161 official English and
French rows plus six documented supplements, and a checksummed historical
catalogue.
It does not zip the unordered `sclappnos` pool to SCL fragments or promote an
ambiguous application to an arbitrary judgment.

In a fixed July 2026 audit using authority parser 6 and the 10 July English
authority, the then-current resolver assigned a document automatically to
2,479 of 2,492 SCL mentions (99.5%). That percentage measures automatic
resolution coverage of the Court-supplied SCL sample only. It does not measure
whether every assignment was correct, detector recall, or the share of all
citations printed in the judgments. Thirteen references remained outside the
graph pending review or documented exclusion. The sample, denominators,
failure classes, method, and qualifications are in the
[citation-resolution audit](https://github.com/niccoloridi/echr-py/blob/main/docs/citation-resolution.md#release-validation-audit).

### Discovery beyond SCL

Full-text discovery parses bounded English and French citation envelopes,
application numbers with compatible case names, ECLIs, reporters, dates,
procedural phases, and `§`, `§§`, `para.` or `paras.` pinpoints. Once a strong
anchor identifies an authority, a document-local gazetteer can recover later
short forms under conservative uniqueness and context rules.

External, self-referential, ambiguous, and rejected candidates remain typed
diagnostics. Only exact document resolutions enter the inclusive document
graph; only verified target paragraphs enter the paragraph graph.

Commission-era report and admissibility references with Commission-specific
dates or complete `D.R.` locators are retained as classified, unresolved
occurrences. They cannot be silently promoted to a nearby Court judgment.

## Rich legal structure

`rich_sections=True` builds a source-order document spine rather than a bag of
paragraph strings. It preserves:

- procedure, facts, complaints, law, operative text, and appendices;
- stable physical-block and legal-paragraph addresses, including HTML
  continuations and repeated printed numbers;
- inline bold and italic runs with offsets;
- linked footnote anchors and multi-block bodies;
- separate-opinion boundaries, types, authors, joiners, and source spans;
- the deciding bench, independently of dissent authors;
- represented-by metadata supplied by HUDOC;
- individual dispositive rulings and recorded votes.

Plain-text inputs remain supported. HTML/DOCX sources provide the richer
typography and footnote evidence.

```bash
echr-py versions list \
  --ecli ECLI:CE:ECHR:2005:0512JUD004622199

echr-py versions download \
  --ecli ECLI:CE:ECHR:2005:0512JUD004622199 \
  --formats html,txt,md,docx --out ocalan/
```

The acquisition manifest records each language-specific item ID, rendition
type, requested-format outcome, HTTP status, path, byte length, and SHA-256.

## Local lexical, dense, and hybrid retrieval

Build a portable paragraph index from any acquired corpus:

```bash
echr-py local index-paragraphs --data-dir corpus/ \
  --database corpus/paragraphs.sqlite

echr-py embeddings build --database corpus/paragraphs.sqlite \
  --provider sentence-transformers \
  --model intfloat/multilingual-e5-base \
  --model-revision EXACT_COMMIT \
  --section facts --section the_law --section separate_opinion \
  --include-footnotes --out corpus/embeddings/

echr-py local paragraphs '"positive obligations"' \
  --database corpus/paragraphs.sqlite --mode hybrid \
  --embeddings corpus/embeddings/ --limit 25
```

Dense indexes are normalized float32 vectors in Parquet. The manifest records
the provider, exact model revision, dimensions, query and passage prefixes,
section filter, source database hash, package version, and artifact checksum.
Chunked exact cosine search is always available; FAISS is an optional,
rebuildable accelerator. Hybrid mode uses deterministic reciprocal-rank fusion
and returns lexical, dense, and fused scores separately.

Rich `hudoc-paragraphs.v3` rows carry opinion type, authors, joiners, and
footnote identity directly into retrieval results.

## Optional evidence-grounded studies

The study runner is for creating datasets, not for handing control of the
pipeline to an open-ended agent. A versioned YAML specification fixes:

- corpus and unit of analysis;
- ordered stages and retrieval settings;
- provider and exact model for every model-backed stage;
- JSON Schema output;
- evidence requirements;
- request, token, and dollar budgets;
- output and checkpoint locations.

```bash
echr-py study init citation-use \
  --source corpus/citations/occurrences.parquet \
  --provider openai --model YOUR_EXPLICIT_MODEL \
  --taxonomy multiaxial --out citation-use.yaml

echr-py study validate citation-use.yaml
echr-py study plan citation-use.yaml --out runs/citation-use/
echr-py study run citation-use.yaml --out runs/citation-use/
```

Facts can be required to carry exact source quotations. Offsets are checked
against the addressed source text; ambiguous repeated quotations and invalid
evidence are rejected. Runs are resumable and model attempts remain
distinguishable.

Supported structured-generation surfaces include Gemini, official OpenAI,
OpenAI-compatible endpoints, and Anthropic. Native batch execution is explicit
for Gemini and official OpenAI; local Sentence Transformers are available for
embeddings. No provider or model is selected silently.

## Graphs that travel with the research

`hudoc-graph/v1` represents nodes and links once and exports them as:

- typed JSON for downstream code;
- GEXF for Gephi;
- a single offline HTML file with vendored D3 and no CDN request.

Adapters cover the SCL decision graph, inclusive citations, paragraph
citations, and custom NetworkX graphs.

```bash
echr-py graph export --kind citation-scl \
  --format gexf --in corpus/citations --out scl.gexf

echr-py graph export --kind citation-paragraph \
  --format html --in corpus/citations \
  --source-component opinion --out opinions.html
```

The browser viewer provides search, attribute filters, legends, direction and
weight controls, component isolation, node-size and colour mappings, detail
panels, and explicit top-N pruning.

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/niccoloridi/echr-py/main/docs/images/citation-network.png" alt="Article 3 Italy citation network generated by echr-py"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/niccoloridi/echr-py/main/docs/images/most-cited-cases.png" alt="Most-cited authorities in the Article 3 Italy demonstration"></td>
  </tr>
  <tr>
    <td>The complete reviewed SCL one-hop network for ten source documents.</td>
    <td>The same frozen artifacts summarized as the most-cited target authorities.</td>
  </tr>
</table>

The pinned Article 3/Italy demonstration resolves all 1,124 SCL mentions in its
ten source documents: 1,111 automatically and thirteen through documented,
source-specific review overrides. Its
[provenance manifest](https://github.com/niccoloridi/echr-py/blob/main/docs/images/citation-network-provenance.json) records the
query, source IDs, authority edition, counts, hashes, and generation date.

## HUDOC-EXEC

The acquisition client connects merits judgments to the official Committee of
Ministers supervision material while preserving HUDOC-EXEC metadata:

```python
from hudoc_py.execution import fetch_case, search, search_documents

open_cases = search(
    state="ITA",
    supervision="enhanced",
    is_closed=False,
    limit=50,
)
execution = fetch_case("57818/09", with_documents=True)
plans = search_documents(collection="acp", appno="57818/09", limit=20)
```

```bash
echr-py exec search --state ITA --supervision enhanced \
  --out execution.jsonl
echr-py exec search-documents --collection acp --appno 57818/09 \
  --out plans.jsonl
echr-py exec fetch-case 57818/09 --out execution-case.json
echr-py exec download-raw --in plans.jsonl --out sources/ \
  --concurrency 10 --extract-text
```

HUDOC-EXEC support is a source-acquisition interface for building a traceable
local collection. It discovers official cases and documents, downloads their
source files, converts them to text or Markdown, and records the identifiers,
checksums, and resumable manifests needed for reproducible work. Records are
left unlabelled: research categories and substantive interpretation belong to
the downstream project rather than the acquisition client.

## Interfaces and commands

The same typed models and artifact identities are exposed through Python, the
CLI, and MCP.

With `uv` and Claude Code installed, Claude Code can register the
version-pinned, read-only MCP server
without cloning the repository:

```bash
claude mcp add --scope user --transport stdio echr-py -- \
  "$(command -v uvx)" --from 'echr-py[mcp]==0.2.2' echr-py mcp
claude mcp get echr-py
```

The server itself needs no Anthropic API key. A one-click Claude Desktop MCPB
will be attached only after the matching PyPI package and clean-machine bundle
tests are complete; manual and source-checkout setup is documented in the
[MCP guide](https://github.com/niccoloridi/echr-py/blob/main/docs/mcp.md).

<p align="center">
  <img src="https://raw.githubusercontent.com/niccoloridi/echr-py/main/docs/images/mcp-terminal-demo.gif" alt="Live echr-py MCP terminal demo" width="92%">
</p>

The recorded July 2026 workflow below replays a fixed three-tool protocol: it identifies the
exact Öcalan Grand Chamber judgment, recovers its structured spine, bench and
separate opinions, then performs inclusive deterministic citation discovery
and resolution. It uses real public HUDOC responses, but no model chooses the
tools.

<p align="center">
  <img src="https://raw.githubusercontent.com/niccoloridi/echr-py/main/docs/images/mcp-research-demo.gif" alt="Live multi-tool echr-py MCP research workflow" width="92%">
</p>

That run recovered 553 source blocks, seventeen deciding-bench members, three
individual opinions and 139 citation occurrences, including 24 opinion
occurrences and 79 occurrence-owned paragraph pinpoints. Its
[provenance record](https://github.com/niccoloridi/echr-py/blob/main/docs/images/mcp-research-demo.provenance.json) retains the
three tool arguments, timings, exact item ID, package version, and script hashes.

In the retained July 2026 model-driven test, Claude Sonnet 5 made three read-only
`echr-py` calls: it selected the English judgment by exact ECLI, requested
compact structural evidence, and asked `echr-py` to construct an
occurrence-weighted citation network.

<p align="center">
  <img src="https://raw.githubusercontent.com/niccoloridi/echr-py/main/docs/images/claude-mcp-network-demo.gif" alt="Claude Sonnet 5 building an Öcalan citation network through echr-py MCP" width="92%">
</p>

This is recorded output, not a mock terminal. The run found 139 citation
occurrences across 78 resolved target documents: 115 in the majority text and
24 in separately identified opinions, with 79 occurrence-owned pinpoints. The
top-five counts and resolution fraction in the animation are arithmetic over
the tool response. The checked final answer confines itself to arithmetic over
the returned network and makes no doctrinal claim. Inspect the portable
[JSON](https://github.com/niccoloridi/echr-py/blob/main/docs/examples/claude-mcp-ocalan/ocalan-citation-network.json),
[GEXF](https://github.com/niccoloridi/echr-py/blob/main/docs/examples/claude-mcp-ocalan/ocalan-citation-network.gexf), or
[downloadable single-file interactive HTML](https://github.com/niccoloridi/echr-py/blob/main/docs/examples/claude-mcp-ocalan/ocalan-citation-network.html?download=1),
which runs locally without a server or CDN,
and the compact [run provenance](https://github.com/niccoloridi/echr-py/blob/main/docs/examples/claude-mcp-ocalan/provenance.json).
Claude Code reported `claude-sonnet-5` as the reasoning model and a small
Haiku routing call separately.

The MCP surface makes the same source-addressed search, paragraph, case,
citation, and graph operations available to an assistant without introducing a
second data model. Compact structure and graph tools avoid forcing model
clients to ingest entire judgments. The default server is read-only; bounded
study jobs are an explicit opt-in.

| Command group | Purpose |
| --- | --- |
| `search`, `count`, `fetch-case`, `smart-fetch` | HUDOC metadata and document acquisition |
| `versions …`, `corpus …` | Language versions and reproducible corpora |
| `local …`, `embeddings …` | Offline browsing and paragraph retrieval |
| `gui` | Optional local Streamlit corpus browser |
| `citations authority|catalog|resolve|locate|review` | Citation authority, discovery, exact resolution, and review |
| `graph metrics|export|html|gexf` | Network metrics and portable graph output |
| `study validate|plan|run|status|resume|export` | Bounded optional dataset studies |
| `exec …` | HUDOC-EXEC record discovery, download, and source conversion |
| `mcp` | Read-only MCP server by default; bounded jobs are opt-in |

```bash
echr-py --help
echr-py citations --help
echr-py graph export --help
echr-py study --help
echr-py exec --help
```

The default MCP server exposes search, retrieval, case structure, citations,
and graphs without mutating external state. Study jobs require an explicit
`--enable-jobs` launch configuration with permitted roots, provider and hook
allowlists, pricing, and per-job budgets.

## See also

Other projects working on ECtHR material:

- [HUDOC Researcher](https://doi.org/10.5281/zenodo.21319704)
- [`echr-extractor`](https://pypi.org/project/echr-extractor/)
- [ECHR Open Data](https://echr-opendata.eu/)
- [Mumford et al.](https://doi.org/10.3233/FAIA251584)
- [ECtHR-PCR](https://github.com/TUMLegalTech/ECHR-PCR)

## Benchmark

`echr-py` is compared against the curated Mumford annotation set. Those
annotations are useful for studying selected citation treatments; they are not
an exhaustive citation inventory or a target-paragraph graph. The annotation
frame deliberately omits submissions, general-principles material, facts,
procedure, operative text, appendices, individual opinions, and footnotes, so a
full `echr-py` run can return citation occurrences from sections and components
outside that frame. Any recovery figure measures recovery of those selected
annotations, not detector precision or completeness over the judgments.

The importer and the methodological comparison remain available for researchers
who need them, and the measured figures, denominators, hashes and reproduction
records are kept in the
[methodology audit](https://github.com/niccoloridi/echr-py/blob/main/docs/mumford-methodology-audit.md)
and the frozen [benchmark manifest](https://github.com/niccoloridi/echr-py/blob/main/docs/benchmarks/mumford-full-inclusive-audit.json),
where they can carry their conditions, rather than on the project homepage.

The evidence and dates behind external comparisons are maintained in the
[claim audit](https://github.com/niccoloridi/echr-py/blob/main/docs/claim-audit.md).

## Installation options

Install only the components a project needs:

```bash
# Base HUDOC and HUDOC-EXEC client
python -m pip install echr-py

# Citation resolution and graph analysis
python -m pip install "echr-py[citations,analysis]"

# Research studies and a provider
python -m pip install "echr-py[research-agent,llm-openai]"

# Local dense retrieval
python -m pip install "echr-py[embeddings-local]"

# All user-facing extras
python -m pip install "echr-py[all]"

# Development checkout and validation
git clone https://github.com/niccoloridi/echr-py.git
cd echr-py
python -m pip install -e ".[all,dev]"
```

## Documentation

- [Worked examples](https://github.com/niccoloridi/echr-py/blob/main/docs/examples.md)
- [Python API](https://github.com/niccoloridi/echr-py/blob/main/docs/python-api.md)
- [CLI reference](https://github.com/niccoloridi/echr-py/blob/main/docs/cli.md)
- [Source-aware text segmentation](https://github.com/niccoloridi/echr-py/blob/main/docs/text-segmentation.md)
- [Citation resolution](https://github.com/niccoloridi/echr-py/blob/main/docs/citation-resolution.md)
- [Paragraph-aware citation occurrences](https://github.com/niccoloridi/echr-py/blob/main/docs/citation-occurrences.md)
- [Deciding benches, judges, and opinions](https://github.com/niccoloridi/echr-py/blob/main/docs/judge-roster.md)
- [Portable retrieval](https://github.com/niccoloridi/echr-py/blob/main/docs/retrieval.md)
- [Bounded research studies](https://github.com/niccoloridi/echr-py/blob/main/docs/research-studies.md)
- [Graph export](https://github.com/niccoloridi/echr-py/blob/main/docs/graph-export.md)
- [HUDOC-EXEC acquisition](https://github.com/niccoloridi/echr-py/blob/main/docs/execution-acquisition.md)
- [MCP](https://github.com/niccoloridi/echr-py/blob/main/docs/mcp.md)
- [Release data and feature boundary](https://github.com/niccoloridi/echr-py/blob/main/docs/release-data-inventory.md)
- [Development](https://github.com/niccoloridi/echr-py/blob/main/docs/development.md)
- [Changelog](https://github.com/niccoloridi/echr-py/blob/main/CHANGELOG.md)
- [Security policy](https://github.com/niccoloridi/echr-py/blob/main/SECURITY.md)

## Verification

```bash
python -m pip install -e ".[all,dev]"
ruff check .
mypy hudoc_py
pytest -q
```

The offline suite requires no API credentials, model downloads, or network.
Live HUDOC, HUDOC-EXEC, provider, browser, and OCR tests are opt-in. Release CI
builds the wheel and source distribution, checks both against the public
feature boundary, installs the wheel in an isolated environment, verifies
public imports, and produces provenance attestations.

## Funding

This research forms part of the Human Rights Nudge project that has received
funding from the European Research Council (ERC) under the European Union’s
Horizon 2020 research and innovation programme (Grant agreement No. 803981).

This work was also supported by a King’s Digital Futures Institute Fellowship.

## Data responsibility

HUDOC and HUDOC-EXEC are evolving public systems. Preserve item IDs, ECLIs,
languages, retrieval dates, checksums, and the producing package revision.
Verify quotations, procedural status, and authoritative documents before legal
or empirical reliance. Court texts and third-party translations retain their
own source terms.

`echr-py` is an independent research tool, not an official Council of Europe
or European Court of Human Rights product.

## Licence

[MIT](https://github.com/niccoloridi/echr-py/blob/main/LICENSE) for the project's
original software. The licence does not cover upstream Court texts, database
contents, independently published datasets, or vendored components such as D3;
see [Rights and third-party material](https://github.com/niccoloridi/echr-py/blob/main/RIGHTS.md).
