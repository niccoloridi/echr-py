# CLI Reference

Install the released package to get the `echr-py` command:

```bash
python -m pip install "echr-py[all]"
echr-py --help
```

Output paths ending in `.parquet` are written as Parquet. Output paths ending
in `.jsonl` are written as JSON Lines. Other output paths receive formatted
JSON. If `--out` is omitted, commands print JSON or plain counts to stdout.

## Main HUDOC Commands

### `search`

Search HUDOC main metadata.

```bash
echr-py search --article 3 --respondent ITA --date-from 2020-01-01 \
  --language ENG --language FRE \
  --sort date-desc --page-size 500 --limit 500 --out italy_article_3.parquet
```

Searches include both Court languages by default. Repeat `--language` to make
the requested HUDOC `languageisocode` flags explicit, or provide it once to
select one stream, for example `--language FRE`. HUDOC item IDs identify
language-specific records, while records sharing an ECLI represent language
versions of the same document.

Useful flags:

- `--article`
- `--respondent`
- `--appno`
- `--date-from`
- `--date-to`
- `--importance`
- `--conclusion`
- `--thesaurus`
- `--concept`
- `--docname`
- `--body grand-chamber|chamber|committee`
- `--doctype-branch`
- `--ecli`
- `--language ENG|FRE` (repeatable; accepts any three-letter HUDOC language flag)
- `--doctype HEJUD` (repeatable raw HUDOC doctype)
- `--collection JUDGMENTS` (repeatable `documentcollectionid`)
- `--hudoc-url URL` (browser, exact-item, or direct API URL)
- `--separate-opinion true|false`
- `--text`
- `--sort relevance|date-desc|date-asc`
- `--limit`
- `--all` (retrieve every match; overrides the default `--limit 100`)
- `--page-size 1..500`
- `--out`

Search output supports JSON, JSONL, CSV, and Parquet by extension. A search
whose total exceeds HUDOC's 10,000-row pagination window is automatically
split into checked date partitions. The command fails without returning a
partial collection if partition counts do not reproduce the original total.
For such a partitioned search, `--sort relevance` cannot be made globally
comparable across date windows. Use `--sort date-asc` or `--sort date-desc`
when output order matters; completeness is unaffected.

```bash
echr-py search \
  --hudoc-url 'https://hudoc.echr.coe.int/eng?i=001-57574' \
  --out exact-document.json

echr-py search --article 8 --language ENG --language FRE \
  --all --page-size 500 --sort date-asc --out article-8.parquet
```

### `count`

Count matching HUDOC rows without fetching them.

```bash
echr-py count --article 8 --respondent FRA --text '"positive obligations"'
echr-py count --respondent ITA --conclusion "Violation of Article 3"
echr-py count --respondent ITA --thesaurus torture   # keyword text, not just IDs
```

### `keypoints`

Look up ECHR keyword (kpthesaurus) IDs by text. This is useful before filtering with
`--thesaurus`.

```bash
echr-py keypoints torture
# 350   (Art. 3) Prohibition of torture
# 492   (Art. 3) Torture
```

### `smart-fetch`

Search, keep the top N cases, and fetch their text.

```bash
echr-py smart-fetch --text '"positive obligations"' --article 8 \
  --top 5 --section the_law --out positive_obligations.jsonl
```

Additional flags:

- `--top`
- `--format text|md|html`
- `--section full|the_law|dispositif`
- `--no-text`
- `--rich-sections` (retain the source-aware block spine and rich sections)

### `fetch-case`

Fetch one case by application number or HUDOC item ID.

```bash
echr-py fetch-case --appno 46221/99 --with-text --section dispositif
echr-py fetch-case --appno 46221/99 --language FRE --with-text
echr-py fetch-case --itemid 001-94054 --with-text --format md --out case.json
echr-py fetch-case --itemid 001-57574 --with-text --rich-sections \
  --out case-with-spine.json

# Also save the raw Word document.
echr-py fetch-case --appno 46221/99 --docx case.docx

# If the English row has no downloadable text, find its French sibling live.
echr-py fetch-case --itemid 001-99999 --with-text --rescue-french
```

`smart-fetch` accepts `--docx-dir DIR` to save each returned case's DOCX.
For `fetch-case --appno`, `--language` restricts the selected HUDOC language
record. With `--itemid`, the item ID itself already fixes the language record.
An application number can belong to several procedural documents, so use an
item ID when a particular judgment or decision must be selected exactly.

### `segment`

Build the source-aware document spine and rich sections for local HUDOC HTML,
plain text, or a table of records:

```bash
echr-py segment --in judgment.html --doctype HEJUD \
  --document-id 001-57574 --out judgment.segments.json

echr-py segment --in texts.parquet --out segments.parquet \
  --text-field text --document-id-field itemid \
  --doctype-field doctype --doctype-branch-field doctype_branch
```

`--format auto` uses the input extension and content; use `--format html` or
`--format text` to override it. JSON, JSONL, CSV, and Parquet inputs support
configurable field names. Missing text is emitted as a visible error record
rather than silently skipped. Output records include status, diagnostics,
section spans, and the versioned spine. See
[text-segmentation.md](text-segmentation.md).

## Bilingual Corpus Commands

HUDOC publishes many English "placeholder" cases whose judgment text exists
only in the French sibling document. These commands reconcile the two language
streams and recover the missing siblings.

### `reconcile`

Collapse a mixed ENG/FRE cases file to one primary per ECLI, attaching each
English case's `french_itemid` and back-filling representatives from the French
row. Extra French rows are dropped by default (`--keep-extra-fre` preserves them
for parity audits).

```bash
echr-py reconcile --in raw.parquet --out reconciled.parquet \
  --duplicates dupes.parquet --stats stats.json
```

### `rescue-french`

For placeholder / textless cases, search HUDOC by application number to find the
French sibling. Resumable via a JSONL checkpoint.

```bash
echr-py rescue-french --in cases.parquet --checkpoint rescue.jsonl \
  --csv rescue_mapping.csv --out cases_rescued.parquet
```

### `corpus build`

The whole pipeline in one command: search both languages, reconcile, rescue, and
hydrate texts (with French fallback) into a directory the local tools can read.

```bash
echr-py corpus build --article 3 --respondent ITA --limit 500 --out corpus/
```

Produces `corpus/{raw.jsonl, cases.parquet, duplicates.parquet, texts.jsonl,
rescue.jsonl, rescue_mapping.csv, report.json, corpus-manifest.json}`. Add
`--docx` to also download raw Word documents, or `--no-rescue`/`--no-texts`
to skip stages.

Build the exact item IDs or ECLIs recorded in a JSON, JSONL, CSV, or
one-address-per-line text selection. `--rich-sections` retains the complete
source spine and writes portable paragraph, section, opinion, bench, judge,
footnote, and dispositive Parquet tables:

```bash
echr-py corpus build --selection selection.json --rich-sections --out corpus/
```

Selection objects may declare an explicit `primary_itemid` and
`language_itemids` keyed by `ENG` and `FRE`. Every declared official language
version is then preserved in `language-versions.parquet`, hydrated in
`language-texts.jsonl`, and, for rich builds, segmented in
`language-sections.jsonl`. Canonical `cases.parquet`, `texts.jsonl`, and
`sections.jsonl` remain available for existing consumers.

Missing metadata or source text is retained in `acquisition-failures.jsonl`.
Validate the neutral `hudoc-corpus/v1` contract and create a deterministic ZIP:

```bash
echr-py corpus validate --in corpus/ --out validation-report.json
echr-py corpus package --in corpus/ --out dist/
```

## Local Corpus Tools

Offline search, browse, and export over a built corpus (no network).

```bash
echr-py local search text "positive obligations" --data-dir corpus/
echr-py local search party "McCann" --data-dir corpus/ --format json
echr-py local search list respondent --data-dir corpus/

echr-py local browse --data-dir corpus/            # list tables
echr-py local browse cases --data-dir corpus/ --stats

echr-py local export --data-dir corpus/ --out export/ --format xlsx
echr-py codebook --out corpus/CODEBOOK.md
```

XLSX export requires `python -m pip install "echr-py[convenience]"`.

Build and query the source-addressable paragraph index:

```bash
echr-py local index-paragraphs --data-dir corpus/ \
  --database corpus/paragraphs.sqlite
echr-py local paragraphs '"positive obligations"' \
  --database corpus/paragraphs.sqlite --section the_law
echr-py local paragraphs --database corpus/paragraphs.sqlite \
  --itemid 001-69022 --para-id 42
```

## Optional Local GUI

Install and launch the local Streamlit corpus browser with:

```bash
python -m pip install "echr-py[gui]"
echr-py gui
```

This is a local convenience interface over user-built artifacts, not a hosted
service or a bundled corpus.

## Language Versions And Dispositive Rulings

```bash
echr-py versions list --appno 46221/99
echr-py versions list --ecli ECLI:CE:ECHR:2005:0512JUD004622199
echr-py versions download --ecli ECLI:CE:ECHR:2005:0512JUD004622199 \
  --formats html,txt,md,docx --out acquisition/
echr-py dispositive --itemid 001-69022
```

Use an ECLI to enumerate translations of one exact document. Application
number mode deliberately returns every related procedural record. Downloads
include a checksummed `hudoc-acquisition.v2` manifest. Every requested format
has an explicit `downloaded`, `derived`, `missing`, or `error` outcome with
HTTP evidence. Records are also classified independently by underlying
document kind and rendition kind, so a translated legal summary is not
misrepresented as the complete translated judgment.

## HUDOC-EXEC Commands

### `exec search`

Search execution cases.

```bash
echr-py exec search --state ITA --supervision enhanced --closed false \
  --limit 50 --out exec_ita.parquet
```

Flags:

- `--state`
- `--supervision standard|enhanced`
- `--closed true|false`
- `--case-type`
- `--limit`
- `--out`

### `exec count`

```bash
echr-py exec count --state ITA --supervision enhanced
```

### `exec search-documents`

Search the official document collections directly. The output is suitable as
input to `exec download-raw`.

```bash
echr-py exec search-documents --collection acp --appno 46221/99 \
  --language ENG --limit 100 --out documents.jsonl
```

Common collection codes include `acp` (action plans), `acr` (action reports),
`CMDEC` (Committee decisions), `ngo`, `nhri`, `gvo`, and `EXECUTION`.

### `exec fetch-case`

```bash
echr-py exec fetch-case 46221/99 --out exec_case.json
echr-py exec fetch-case 46221/99 --no-documents
```

### Raw documents and local text

Raw downloads use each document's `execcontentstoreid`, write a JSONL
manifest, and resume successful identifiers by default.

```bash
echr-py exec download-raw --in documents.jsonl --out raw/ \
  --manifest raw/manifest.jsonl --concurrency 10 --extract-text

echr-py exec extract-text --in raw/pdf/DH-DD-2021-522E.pdf \
  --out action-plan.md --markdown
```

Add `--ocr` only for image-only PDFs; it needs the `ocr` extra and a Mistral
key. Local PDF/DOCX conversion needs `exec-docs`.

### Related document browser fallback

API discovery is preferred. When a case's Case Documents navigator must be
inspected directly, install `scrape`, run `python -m playwright install
chromium`, and pass HUDOC-EXEC internal identifiers:

```bash
echr-py exec scrape-related 004-47097 --out related.jsonl
```

The output is resumable JSONL. Use `--headed` for interactive troubleshooting.

The `exec` commands build a traceable local source collection: official record
discovery, download, text conversion, OCR, and provenance. They return source
material rather than a pre-coded research dataset, leaving substantive coding
choices to the downstream project.

## Citation Graph Commands

Citation resolution operates on a prior cases file from `echr-py search` or
`corpus build`. An application number identifies an application, not one exact
procedural document, so graph metrics consume audited resolution artifacts.

```bash
echr-py search --article 8 --respondent ITA --limit 500 --out cases.parquet

# Maintainer step: import a locally downloaded edition of the Court's official
# exact-reference master list.
echr-py citations authority import \
  --pdf Case_law_references_ENG.pdf --language eng --out authority-eng/
echr-py citations authority import \
  --pdf Case_law_references_FRA.pdf --language fra --out authority-fra/
echr-py citations authority merge \
  --authority authority-eng/ --authority authority-fra/ --out authority/

echr-py citations resolve \
  --in cases.parquet --authority authority/ \
  --overrides citation-overrides.csv --out corpus/citations/

# Locate each resolved authority in the citing judgment's source paragraphs.
echr-py citations locate \
  --in cases.parquet --resolution-dir corpus/citations/ \
  --out corpus/citations/

echr-py citations review \
  --resolution-dir corpus/citations/ \
  --out citation-review.html --csv citation-overrides.csv

echr-py graph metrics --citations corpus/citations/ \
  --weight citation_count --out metrics.parquet
echr-py graph html --citations corpus/citations/ \
  --out network.html --max-nodes 500
echr-py graph gexf --citations corpus/citations/ \
  --weight citation_count --out network.gexf
```

Download the PDF from the
[Court's official master-list link](https://www.echr.coe.int/documents/d/echr/case_law_references_eng).
The import is local and deterministic. It uses no LLM, token charges, or model API key.
It writes `citation-authority.json` for runtime use and a human-readable
`citation-authority.csv` mirror. The packaged bilingual JSON/CSV contain the
English and French editions updated through 27 July 2026 (42,161
language-specific official rows plus six reviewed supplements). Download and
import again only when an official link has a newer edition or when reproducing
the import from source. Pin each recorded edition date, retrieval timestamp,
and SHA-256 for reproducibility. The `§ ...` template slot is used to
validate PDF rows but is excluded from document-identity matching.

`citations resolve` writes `mentions.parquet`, `targets.parquet`,
`candidates.parquet`, `edges.parquet`, `nodes.parquet`, a resumable lookup
cache, and `resolution-report.json`. It resolves one citation hop by default.
Unresolved references remain in the mention audit but never enter accepted
edges.

`citations locate` consumes those resolution artifacts without changing them.
It reuses `<itemid>.html` from `--html-dir` or downloads and caches public
HUDOC HTML. `--offline` forbids downloads; missing cached documents are
reported in partial artifacts and make the command return non-zero. The
command defaults to `--scope inclusive`; `--scope scl` preserves the original
SCL-only behavior. Owned citation pinpoints are resolved against cited-document
HTML by default and cached under `<out>/target-html`; use
`--target-html-dir` or `--no-resolve-paragraphs` to control that stage. It
writes `occurrences.parquet`, `occurrences.jsonl`,
`mentions-inclusive.parquet`/JSONL, `edges-inclusive.parquet`,
`paragraph-edges-inclusive.parquet`/JSONL,
`occurrence-report.json`, and `source-html-manifest.jsonl`. See the
[occurrence compatibility contract](citation-occurrences.md).
It also writes `historical-catalog-manifest.json` with the exact packaged
catalog checksum and public-source provenance.

External reference sets remain outside the package. Fetching pins the upstream
revision, URL, retrieval date, archive checksum, and declared licence status:

```bash
echr-py citations benchmark fetch mumford --out benchmarks/mumford/
echr-py citations benchmark fetch ecthr-pcr --out benchmarks/ecthr-pcr/
echr-py citations benchmark import --kind mumford \
  --source benchmarks/mumford/source/ --out benchmarks/mumford/imported/
echr-py citations benchmark compare --kind mumford \
  --reference benchmarks/mumford/imported/annotations.jsonl \
  --documents benchmarks/mumford/imported/documents.jsonl \
  --local corpus/citations/occurrences.parquet \
  --reference-scope echr \
  --projected-out benchmarks/mumford/occurrences-projected.jsonl \
  --labels runs/citation-use/records-latest.jsonl \
  --out benchmarks/mumford/comparison.json
```

Pinned built-in revisions are checked against recorded SHA-256 values. A custom
`--revision` must be accompanied by `--sha256`; `--timeout` and `--max-bytes`
bound network and archive use.

The Mumford importer preserves source text, exact spans, curator labels,
individual annotators, judicial-consideration labels, Articles/Protocols, and
unmatched/ambiguous alignments. For full rich-HUDOC occurrences,
`--documents` maps each majority citation from its current paragraph context
into the historical XMI Sofa. The mapping must be unique, retains both offset
systems, and abstains on opinions, footnotes, text outside the reference, and
ambiguous context. Alignment is one-to-one and requires compatible citation
identity even when spans overlap. `--reference-scope echr` excludes labels for
other authority families from the detector's denominator. ECtHR-PCR is
application-number based, so its comparison is reported separately at
application and exact-document scope. Neither dataset is called exhaustive
gold data, and absent annotations are not used to manufacture precision claims.

Build or verify the reproducible offline historical reporter index with:

```bash
echr-py citations catalog build --in hudoc-metadata.parquet --out catalog.json
echr-py citations catalog verify --in catalog.json
```

Graph metrics and HTML fail closed while the report is incomplete. The
`--allow-incomplete` switch is diagnostic and labels output provisional.
`--use-extracted-appno` is deprecated: HUDOC's `extractedappno` field contains
unrelated body mentions and is not a target identifier. The old `--in` graph
path remains only as a strict offline convenience and does not have the
authority provenance required for a published measurement-grade network.

Add authoritative resolution directly to a corpus build with:

```bash
echr-py corpus build --article 8 --respondent ITA --citations \
  --citation-authority authority/ \
  --citation-overrides citation-overrides.csv \
  --out corpus/
```

See [citation-resolution.md](citation-resolution.md) for reporter grammar,
evidence and override rules, historical HUDOC gaps, and the meaning of 100%
resolution.

## Bounded Study Commands

Model use on `main` is optional and is driven by an explicit versioned study,
not a domain-specific `extract` command:

```bash
echr-py study init citation-use --source occurrences.parquet \
  --provider openai --model YOUR_EXPLICIT_MODEL \
  --taxonomy multiaxial --out citation-use.yaml
echr-py study validate citation-use.yaml
echr-py study plan citation-use.yaml --out runs/citation-use/
echr-py study run citation-use.yaml --out runs/citation-use/
echr-py study status runs/citation-use/
echr-py study resume runs/citation-use/
echr-py study resume runs/citation-use/ --wait --max-polls 20
echr-py study cancel runs/citation-use/
echr-py study export runs/citation-use/ --format parquet --out labels.parquet
```

Native `batch: true` stages are supported only for Gemini and official OpenAI.
The initial run persists the provider job and returns `waiting`; resume polls
or retrieves it. Anthropic and `openai-compat` are realtime-only. Stage
settings include `temperature`, `max_output_tokens`, `concurrency`, provider
options, record-level `required_evidence`, and field-level `evidence_fields`.

Use `--schema custom.json` instead of `--taxonomy` for a user-owned JSON
Schema. Provider and exact model are mandatory. Domain-specific pipelines are
not part of the public commands.

## Embeddings And Unified Graph Export

```bash
echr-py embeddings build --database corpus/paragraphs.sqlite \
  --provider sentence-transformers --model EXACT_MODEL_ID \
  --model-revision EXACT_MODEL_COMMIT \
  --out corpus/embeddings/
echr-py embeddings verify --in corpus/embeddings/
echr-py local paragraphs "margin of appreciation" \
  --database corpus/paragraphs.sqlite --mode hybrid \
  --embeddings corpus/embeddings/

echr-py graph export --kind citation-scl --format json \
  --in corpus/citations/ --out scl.json
echr-py graph export --kind citation-paragraph --format html \
  --in corpus/citations/ --out paragraph-network.html --max-nodes 1000
```

The HTML graph is a fully offline artifact, not a hosted dashboard.

## MCP

```bash
echr-py mcp
```

This starts the MCP server on stdio. It is normally launched by an MCP client,
not by a human-operated terminal.
