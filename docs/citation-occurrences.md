# Paragraph-Aware Citation Occurrences

The occurrence layer answers a different question from SCL resolution:

- an SCL mention identifies a bibliographic authority cited by a document;
- a citation occurrence identifies where that authority appears in the source text.

Occurrence finding is deterministic. Inclusive mode combines the document's
SCL entries with strong citation envelopes discovered directly in paragraph-aware
HUDOC HTML, inline typography, application numbers, ECLIs, reporters, dates,
case names, and conservative short-form rules. It has no LLM fallback.

## Workflow

```bash
echr-py citations resolve \
  --in cases.parquet --out corpus/citations/

echr-py citations locate \
  --in cases.parquet \
  --resolution-dir corpus/citations/ \
  --out corpus/citations/
```

`locate` defaults to `--scope inclusive`. Use `--scope scl` to reproduce the
SCL-only compatibility layer. Inclusive application-number discovery requires
a case name in the same citation envelope and validates the identity through
HUDOC; EU, domestic, UN, inter-American, self-reference and bare-number forms
remain typed diagnostics.

By default the locator reuses or creates
`corpus/citations/source-html/<itemid>.html`. Every source is recorded with a
SHA-256 digest in `source-html-manifest.jsonl`. Pass `--html-dir` to use a
different cache. Pass `--offline` to prohibit network retrieval; missing HTML
is reported, partial occurrence artifacts are still written, and the command
returns non-zero.

The locator also resolves each independently owned `§`/`§§`/`para.` pinpoint
against the exact cited document by default. Cited HTML is cached under
`target-html/` (override with `--target-html-dir`) and checksummed in
`target-html-manifest.jsonl`. Use `--no-resolve-paragraphs` to skip this stage.
Two adjacent citations are bounded separately: either, both, or neither may
carry a pinpoint, and no pinpoint is borrowed from a neighbouring authority.

Each run also writes `historical-catalog-manifest.json`, tying the result to
the packaged catalog checksum, public source URLs, retrieval date, and
historical coverage dates.

## Portable contract

`occurrences.parquet` and `occurrences.jsonl` contain the same logical rows.
The JSONL representation is the neutral interchange format for downstream
systems and project-specific converters.

| Field group | Fields |
| --- | --- |
| Identity | `schema_version`, `occurrence_id`, `mention_id` |
| Source document | `source_itemid`, `source_language`, `source_section`, `source_component` |
| Source address | `source_block_id`, `source_para_id`, `source_para_num`, `source_footnote_id`, invoking block/paragraph IDs |
| Exact span | `block_start`, `block_end`, `document_start`, `document_end`, `raw_text`, `source_context` |
| Typography | `italic`, `bold` |
| Audit | `finder`, `evidence` |
| Opinion | `source_opinion_id`, ordinal, type, authors, joined-by |
| Provenance | `scl_coverage`, `scl_mention_ids`, `discovery_methods`, `resolution_scope` |
| Target | `target_node_id`, `target_ecli`, `target_itemid`, `target_appnos` |
| Pincite | `target_paragraphs`, `target_paragraph_resolutions`, `paragraph_resolution_status` |

Offsets are half-open. Block offsets address `DocumentBlock.text`; document
offsets address the plain-text spine reconstructed with a blank line between
blocks. `source_para_id` is the stable local paragraph address and may be
suffixed when the printed number repeats. `target_paragraphs` preserves
printed labels and bounded ranges such as `10-12`; it is not silently treated
as a source-paragraph address.

A citation printed inside a footnote keeps the footnote body as its exact
source address and also records `source_invoking_block_ids` and
`source_invoking_para_ids`. It therefore cannot collide with a same-numbered
majority or opinion paragraph, while downstream studies can join the footnote
text back to the paragraph(s) that invoked it. Its majority/opinion component
comes from that invoking context when unambiguous.

`paragraph-edges-inclusive.parquet`/JSONL is the additive source-paragraph to
target-paragraph graph. It contains one row per mapped target paragraph, with
the printed pinpoint, source component/opinion identity, exact target block
and paragraph IDs, source footnote/invoking addresses, target section/text,
mapping status, language, and target-HTML checksum.
Application-level targets never create paragraph edges. Both a source
majority and any source opinion may independently cite the same target
paragraph. Mapping statuses make
`resolved`, `partial`, `ambiguous`, `missing`, and `unavailable` outcomes
explicit. When numbering restarts inside the *target document*, repeated
numbers in its appended opinions and appendices are excluded from ordinary
judgment-pinpoint lookup unless citation evidence explicitly identifies those
target components.

`mentions-inclusive.parquet`/JSONL retain both SCL and text-derived mentions.
`edges-inclusive.parquet` contains only exact document-level targets and keeps
separate SCL-covered and text-only counts. Application-level ambiguity never
becomes a document edge.

`occurrence-report.json` records document and SCL counts, located and unlocated
mentions, missing HTML, ambiguous hits, unmatched strong-anchor candidates,
finder-method counts, and detailed diagnostics. Unmatched application numbers,
ECLIs, full party names and reporter forms are retained for audit or later
resolution, but are never assigned to an SCL target by guesswork.

The Python `discover_citation_mentions(...)` result contains strong mentions,
preliminary paragraph occurrences, and rejected candidates. A raw application
number is promoted only when a compatible case name occurs in the same
citation envelope; otherwise it remains a typed diagnostic.

## Precision rules

Full citation envelopes, SCL forms, application numbers corroborated by a case
name, ECLIs, and unique Series A locators are strong anchors. Applicant-only
forms are accepted only when unique within the local
gazetteer and supported by italics, an adjacent citation cue/pinpoint, or an
earlier strong anchor to the same target. Respondent-state and generic-word
aliases are never standalone identifiers. Front matter, headings, running
headers and all-caps title repetitions are excluded.

Typography is corroborating evidence rather than an independent finder.
Plain-text input can therefore locate strong forms, but weak short forms fail
conservatively when formatting evidence is unavailable.

## Optional Citation-Use Labels

Occurrence rows may be inputs to a bounded, model-backed study of source
voice, engagement, function, treatment, complaint Articles, or another
user-defined taxonomy. This is an optional derivative dataset, never a third
citation finder:

```bash
echr-py study init citation-use --source occurrences.parquet \
  --provider openai --model YOUR_EXPLICIT_MODEL \
  --taxonomy multiaxial --out citation-use.yaml
```

Each substantive label requires an exact quotation that is verified against
`source_context`. The chosen schema, prompt, model, attempts, invalid evidence,
cost, and review status remain in the study bundle. Custom JSON Schema is
supported, so researchers can compare published taxonomies without making one
of them the package ontology. Labels cannot change occurrence identities,
resolution scope, SCL coverage, or graph weights.

## Scope and comparison

English/French regression documents cover majority reasoning, appendices,
individual opinions, multiple citations in one paragraph, and owned
pinpoints. The suite verifies exact identities and conservative abstention; it
does not turn that deliberately selected regression set into a corpus-level
accuracy estimate.

HUDOC Researcher/ECHR Dashboard v1.0.0 reports a much larger deployed English
snapshot (19,822 judgments and about 1.31 million paragraphs). Its archived
`p29` citation table scans citing paragraphs for raw application-number forms
and maps them to a case identifier. The archive also contains ECtHR-PCR and
SCL/free-text name-resolution paths, so the project should not be described as
having used only appnos throughout its history; it also retains the citing
paragraph. The displayed application/case graph does not document exact
procedural-document or cited-target-paragraph resolution. Its separate
Case-Law-Guide retrieval evaluation (409 queries) reports document-hit@10 88%
and paragraph-hit@10 73%; those search metrics are not comparable to the
citation audit above. This package is stricter and broader in the citation model:
multilingual public acquisition, name-and-namespace gates, exact
procedural-document resolution, separate majority/opinion provenance, and
source-paragraph-to-*cited-paragraph* edges. A superiority claim still requires
the planned manually labelled, corpus-scale benchmark; architectural breadth
is not a substitute for published precision/recall.

The pinned Mumford comparison is useful for testing selected citation-use spans,
but it is not a head-to-head detector benchmark. Its annotations deliberately
cover selected uses in `THE LAW`, excluding submissions, general-principles
material, facts, procedure, operative text, appendices, individual opinions,
and footnotes. The stripped text-only baseline also excludes SCL gazetteers and
target lookup. The [methodology audit](mumford-methodology-audit.md) records the
alignment results, scope, annotation limitations, and a reproducible
source-document grouping defect in the published repetition analysis. The
[five-case audit](benchmarks/five-case-curated-audit.md) separately shows that
all 24 sampled automatic occurrences outside the curator pairing were
citation-related in context.

Party-name anchors use Unicode capitalisation rather than a Western-European
character range, covering forms such as *Šilih* while retaining lowercase-prose
rejection. Declared `hereinafter` aliases, verified distinctive-token subsets,
and same-paragraph name/reporter reconciliation remain candidates for labelled
evaluation rather than being enabled speculatively.

## Graph compatibility

The locator never rewrites `mentions.parquet`, `edges.parquet`,
`nodes.parquet`, or `resolution-report.json`. Existing `citation_count` remains
the count of accepted SCL mentions. Inclusive artifacts are additive and
versioned separately for downstream converters.
