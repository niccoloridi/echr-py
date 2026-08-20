# Python API Guide

This guide covers the public Python workflows exposed by `echr-py`.

## Import Surfaces

The top-level package exposes the synchronous HUDOC main API:

```python
from hudoc_py import Q, count, fetch_case, fetch_text, search, smart_fetch
```

Async users should import from `hudoc_py.aio` or `from hudoc_py import aio`:

```python
from hudoc_py import aio

cases = await aio.search(article="8", limit=100)
```

HUDOC-EXEC has a parallel namespace:

```python
from hudoc_py.execution import count, fetch_case, fetch_document, fetch_text
from hudoc_py.execution import search, search_documents
```

## Search Cases

```python
from hudoc_py import search

cases = search(
    article="3",
    respondent="ITA",
    date_from="2020-01-01",
    date_to="2025-12-31",
    importance=[1, 2],
    languages=("ENG", "FRE"),
    limit=500,
    sort="date-desc",
)

print(len(cases))
print(cases.result_count)
df = cases.to_dataframe()
cases.to_jsonl("cases.jsonl")
```

Supported friendly sort values are `relevance`, `date-desc`, and `date-asc`.
`relevance` uses HUDOC's ranking model and is most useful with a full-text
query clause.

Common filters:

| Filter | Example | Notes |
| --- | --- | --- |
| `article` | `"3"` | Convention article |
| `respondent` | `"ITA"` | HUDOC country code |
| `appno` | `"46221/99"` | application number |
| `date_from`, `date_to` | `"2020-01-01"` | keypoint date range |
| `importance` | `1` or `[1, 2]` | HUDOC importance level |
| `conclusion` | `"Violation of Article 3"` | phrase-matched text |
| `kpthesaurus` | `"350"` | numeric thesaurus ID |
| `concepts` | `"..."` | ECHR concepts field |
| `docname` | `"McCann"` | case-title fragment |
| `body` | `"grand-chamber"` | also `chamber`, `committee` |
| `doctypebranch` | `"CHAMBER"` | raw HUDOC branch |
| `separate_opinion` | `True` | separate opinion flag |
| `ecli` | `"ECLI:CE:ECHR:..."` | ECLI identifier |
| `collection` | `"JUDGMENTS"` | HUDOC document collection |
| `text` | `'"positive obligations"'` | raw full-text fragment |

Multi-value filters are OR-ed internally. Different filters are AND-ed.

`languages` accepts HUDOC's three-letter `languageisocode` values. The default
is `("ENG", "FRE")`. `doctypes` and `collection` can select communicated
cases, Commission material, or other HUDOC collections explicitly.

Shareable HUDOC URLs can be used without manually translating the browser hash:

```python
cases = search(
    hudoc_url=(
        "https://hudoc.echr.coe.int/eng"
        "#{%22article%22:[%223%22],%22respondent%22:[%22ITA%22]}"
    ),
    limit=250,
)
```

Direct API `?query=...`, exact `?i=001-...`, and JSON-fragment links are
supported. Extra keyword filters are AND-ed onto the URL query. Invalid hosts
or malformed fragments raise `ValueError` rather than falling back to a broad
search.

HUDOC refuses pagination beyond a 10,000-row query window. When a requested
result exceeds it, complete retrieval recursively partitions the date interval
and verifies that partition counts exactly reproduce the original total. A
mismatch raises `HudocResultWindowError`; no partial collection is returned.
Partitioning preserves the complete set of records, not one global relevance
ranking across windows. Use `sort="date-asc"` or `sort="date-desc"` when saved
row order must remain meaningful for a result set above the limit.

For low-level streaming and selective fields:

```python
from hudoc_py.main import AsyncHudocClient

async with AsyncHudocClient() as client:
    async for row in client.iter_results(
        article="3",
        select="itemid,ecli,languageisocode,docname,kpdate",
        page_size=500,
    ):
        process(row)
```

## Count Before Fetching

```python
from hudoc_py import count

n = count(article="8", respondent="FRA", text='"positive obligations"')
print(n)
```

`count()` asks HUDOC for the server-side match count without materializing
rows. Use it before large searches.

## Fetch One Case

```python
from hudoc_py import fetch_case

case = fetch_case(appno="46221/99", with_text=True, text_format="md")
print(case.docname)
print(case.text[:1000])
print(case.represented_by)  # native HUDOC representedby metadata, when present
```

You can fetch by `appno` or exact HUDOC `itemid`.

`text_format` accepts:

- `text`: plain text
- `md`: Markdown converted from the source HTML
- `html`: raw converted HTML body

## Portable Corpus Bundles

The asynchronous corpus builder accepts either a HUDOC query or a frozen
item-ID/ECLI selection. Rich builds retain the source-aware spine and emit
neutral research tables. A selection can include `primary_itemid` plus
`language_itemids: {"ENG": [...], "FRE": [...]}` to acquire and retain every
declared official language version without changing the canonical compatibility
tables:

```python
import asyncio

from hudoc_py.bilingual import build_corpus, package_corpus, validate_corpus

asyncio.run(
    build_corpus(
        "corpus",
        selection="selection.json",
        rich_sections=True,
    )
)
report = validate_corpus("corpus", out="validation-report.json")
assert report.valid
archive = package_corpus("corpus", "dist")
print(archive.sha256)
```

`generate_corpus_manifest()` is also public when a caller needs to inventory
an already-built directory without packaging it.

## Smart Fetch

```python
from hudoc_py import smart_fetch

cases = smart_fetch(
    text='"positive obligations"',
    article="8",
    top=5,
    sort="relevance",
    with_text=True,
)

for case in cases:
    print(case.rank, case.docname)
    print(case.sections.the_law[:500])
```

`smart_fetch()` runs a search, keeps the top N rows, and downloads their text
concurrently. It is the shortest path for "give me the most relevant cases and
their reasoning".

## Query DSL

Use `Q` when a query needs explicit boolean structure:

```python
from hudoc_py import Q, search

q = (Q.article("3") | Q.article("8")) & Q.respondent("ITA")
q = q & ~Q.body("committee") & Q.phrase("positive obligations")

cases = search(query=q, limit=100, sort="relevance")
```

Useful helpers include:

- `Q.article(value)`
- `Q.respondent(value)`
- `Q.appno(value)`
- `Q.itemid(value)`
- `Q.importance(value)`
- `Q.conclusion(value)`
- `Q.thesaurus(value)`
- `Q.docname(value)`
- `Q.body(value)`
- `Q.ecli(value)`
- `Q.doctype(value)`
- `Q.separate_opinion(value=True)`
- `Q.date_range(date_from, date_to)`
- `Q.phrase(text)`
- `Q.text(terms)`
- `Q.text_near(phrase, distance=5)`
- `Q.raw(fragment)`

## Sections

`segment=True` is enabled by default when text is fetched through
`fetch_case()` or `smart_fetch()`. The default splitter extracts the main
`the_law` and `dispositif`/operative parts.

Pass `rich_sections=True` for a fuller section model:

```python
case = fetch_case(appno="46221/99", with_text=True, rich_sections=True)

sections = case.sections
print(sections.found)
print(sections.status)
print(sections.confidence)
print(sections.facts)
print(sections.operative)

for span in sections.spans:
    print(span.section, span.start_block, span.end_block, span.paragraph_ids)
```

The richer splitter supports `procedure`, `facts`, `subject_matter`,
`complaints`, `the_law`, `court_assessment`, `operative`,
`separate_opinion`, and `appendix`, plus metadata such as `doctype_mode`.

The rich path builds `sections.spine`, a `hudoc-spine.v1` ordered block model
that retains HUDOC HTML tags, CSS-derived heading evidence, source-order
character spans, strict printed paragraph numbers, and deterministic local
paragraph IDs. `sections.status` is `complete`, `partial`, `unsegmented`, or
`not_applicable`; `sections.diagnostics` explains missing boundaries and other
quality conditions. The confidence value is a deterministic quality score,
not a calibrated probability. See [text-segmentation.md](text-segmentation.md)
for the block and boundary contracts.

`sections.bench` is independently parsed from the deciding composition in the
front matter, not inferred from separate opinions:

```python
if sections.bench:
    for member in sections.bench.members:
        print(member.name, member.role, member.country, member.is_ad_hoc)
    print(sections.bench.confidence, sections.bench.diagnostics)
```

### Individual Opinions

The rich splitter also breaks the separate-opinions block into individual
`Opinion` objects (`sections.opinions`), each with `opinion_type`
(`dissenting`, `concurring`, `partly_dissenting`, `partly_concurring`,
`partly_concurring_partly_dissenting`, `separate`, or `declaration`),
`joint`, `joint_heading`, literal `authors`, `joined_by`, their combined
backwards-compatible `judges` list, the `raw_header`, and the opinion `text`.
English and French heading grammars are recognised.

```python
case = fetch_case(itemid="001-69022", with_text=True, rich_sections=True)
for opinion in case.sections.opinions:
    print(opinion.opinion_type, opinion.authors, opinion.joined_by)
    print(len(opinion.body))

print(case.sections.opinions_confidence)
print(case.sections.opinion_diagnostics)
```

To split any separate-opinions text directly, use
`hudoc_py.text.split_opinions(text)`. For the confidence score and diagnostics,
use `hudoc_py.text.split_opinions_report(text)`.

`Opinion.text` includes the heading for backwards compatibility. Use the
explicit `Opinion.body` field for body lengths and content analysis; do not
slice by `len(raw_header)`, because multiline headings are normalized in
`raw_header`. An absent opinion block returns confidence `1.0` with no
diagnostics, while a non-empty unparseable block returns confidence `0.0` and
`no_headings_in_block`.

Judge names in `opinions` are canonicalized against a curated 188-name roster
(168 elected + 20 ad hoc, drawn from the official "Judges of the Court since
1959" list): spelling and diacritic variants fold to one form
(`ZUPANCIC` → `ZUPANČIČ`) and partial names expand (`BRATZA` →
`SIR NICOLAS BRATZA`). Roster lookups are available directly:

```python
from hudoc_py.text import judge_country, judge_region, judge_years, is_ad_hoc_judge

judge_country("TULKENS")   # "Belgium" – the state the judge was elected for
judge_region("TULKENS")    # "Western Europe"
judge_years("COSTA")       # (1998, 2011)
is_ad_hoc_judge("REED")    # True
```

See [judge-roster.md](judge-roster.md) for roster scope, normalization rules,
and direct lookup examples.

## HUDOC-EXEC

```python
from hudoc_py.execution import fetch_case, fetch_text, search, search_documents

cases = search(state="ITA", supervision="enhanced", is_closed=False, limit=50)
case = fetch_case("46221/99", with_documents=True)

documents = search_documents(collection="acp", state="ITA", limit=50)
for doc in documents:
    if doc.content_store_id:
        text = fetch_text(doc.content_store_id, format="text")
```

`fetch_document(content_store_id, with_text=True)` returns a typed execution
document and can preserve raw HTML or convert it to plain text or Markdown.
`AsyncExecRawDownloader` adds concurrent, resumable PDF acquisition with a
JSONL manifest and optional local conversion/OCR.

The API returns an inspectable source collection rather than a pre-coded
implementation dataset. Downstream projects define their own research
categories and interpretation. See
[execution-acquisition.md](execution-acquisition.md) for collections, bulk
downloads, provenance, and scope.

## Citations

```python
from hudoc_py.citations import (
    CitationGraph,
    load_authority,
    load_overrides,
    resolve_citations,
)

result = await resolve_citations(
    source_cases,
    authority=load_authority("authority/"),
    catalog=local_target_catalog,
    client=hudoc_client,
    overrides=load_overrides("citation-overrides.csv"),
)
result.require_complete()

graph = CitationGraph.from_resolution(result)
metrics = graph.metrics_dataframe(weight="citation_count")
graph.to_html("network.html")
```

Locate the resolved authorities in a source judgment without an LLM:

```python
from hudoc_py.citations import (
    discover_citation_mentions,
    extract_citation_occurrences,
    resolve_occurrence_paragraphs,
)

discovery = discover_citation_mentions(source_case, html=source_html)
for mention in discovery.mentions:
    print(mention.origin, mention.cited_name, mention.source_opinion_id)

occurrences = extract_citation_occurrences(
    source_case,
    result.resolutions,
    html=source_html,
    scope="inclusive",
)
for occurrence in occurrences.occurrences:
    print(occurrence.source_para_id, occurrence.raw_text, occurrence.target_node_id)

# Target spines come from structured HTML for each exact cited document.
occurrences = resolve_occurrence_paragraphs(
    occurrences, {target_itemid: target_spine}
)
print(occurrences.paragraph_edges)
```

The inclusive occurrence layer is additive: existing SCL artifacts remain the
compatibility baseline, while text discoveries carry separate provenance and
only exact document resolutions enter `edges-inclusive.parquet`. Occurrences
enumerate source section/opinion, exact span, typography, candidate resolution
scope, and cited-paragraph labels. See
[citation-occurrences.md](citation-occurrences.md).

Third-party exports can be compared without bundling their data:

```python
from hudoc_py.citations import (
    benchmark_citation_annotations,
    compare_citation_exports,
    import_mumford,
    load_benchmark_rows,
    load_competitor_citations,
)

competitor = load_competitor_citations("ecthr-pcr.jsonl")
comparison = compare_citation_exports(local_rows, competitor)

import_mumford("benchmarks/mumford/source", "benchmarks/mumford/imported")
reference = load_benchmark_rows("benchmarks/mumford/imported/annotations.jsonl")
report = benchmark_citation_annotations(reference, local_rows, labels=optional_labels)
```

Pinned fetch manifests record repository revision, URL, retrieval date,
checksum, and declared licence status; third-party data stays outside the
wheel. Mumford alignment preserves unmatched and ambiguous references and does
not infer precision from annotations that are absent. ECtHR-PCR remains an
application-level comparison unless separate exact-document evidence is
available.

Application numbers and `sclappnos` generate candidates; they do not identify
one procedural document. The resolver accepts only explicit ECLI/item IDs,
official-authority matches, unique corroborated metadata matches, or reviewed
overrides. See [citation-resolution.md](citation-resolution.md) for the data
model, evidence hierarchy, artifacts, and fail-closed graph contract.

## Studies, Retrieval, And Graph Bundles

Optional studies are explicit and bounded:

```python
from hudoc_py.studies import StudyRunner, load_study_spec

spec = load_study_spec("study.yaml")
run = StudyRunner(spec, output_dir="runs/example").run()
```

Portable dense/hybrid retrieval is available without a vector service:

```python
from hudoc_py.retrieval import HybridRetriever, verify_embedding_index

verify_embedding_index("corpus/embeddings")
retriever = HybridRetriever(
    database="corpus/paragraphs.sqlite",
    mode="hybrid",
    embeddings="corpus/embeddings",
    top_k=25,
)
results = retriever.search("effective investigation")
```

Unified graph adapters export the versioned neutral contract:

```python
from hudoc_py.graphs import export_graph, from_citation_graph

bundle = from_citation_graph(graph)
export_graph(bundle, "network.html", fmt="html")
```

See [research-studies.md](research-studies.md), [retrieval.md](retrieval.md),
and [graph-export.md](graph-export.md).

## Output Models

Records are Pydantic v2 models. Common models include:

| Model | Meaning |
| --- | --- |
| `Case` | one HUDOC main search row |
| `CaseCollection` | list-like collection with DataFrame and JSONL helpers |
| `Sections` | segmented judgment text |
| `BenchComposition` | deciding bench parsed from composition front matter |
| `Citation` | one parsed case-law citation |
| `CitationOccurrence` | one authority-specific, source-addressed citation occurrence; compound rows may share a printed locus |
| `ExecutionCase` | one HUDOC-EXEC case |
| `ExecutionDocument` | one action plan, report, CM decision, communication, or resolution |
