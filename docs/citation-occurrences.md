# Paragraph-Aware Citation Occurrences

The occurrence layer answers a different question from SCL resolution. The
Court-supplied SCL field is useful, selective bibliographic evidence rather
than an exhaustive inventory of citations, including in majority text:

- an SCL mention identifies a bibliographic authority listed for a document;
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
| Identity | `schema_version`, target-independent `locus_id`, authority-specific `occurrence_id`, `mention_id` |
| Compound citation | `citation_group_id`, `group_ordinal`, `group_size` |
| Source document | `source_itemid`, `source_language`, `source_section`, `source_component` |
| Source address | `source_block_id`, `source_para_id`, `source_para_num`, `source_footnote_id`, invoking block/paragraph IDs, structured `source_invocations` |
| Exact span | `block_start`, `block_end`, `document_start`, `document_end`, `raw_text`, `source_context` |
| Typography | `italic`, `bold` |
| Audit | `finder`, `evidence` |
| Opinion | `source_opinion_id`, ordinal, type, authors, joined-by |
| Provenance | `scl_coverage`, `scl_mention_ids`, `discovery_methods`, `resolution_scope` |
| Target | `target_node_id`, `target_ecli`, `target_itemid`, `target_appnos` |
| Pincite | `target_paragraphs`, `target_paragraph_resolutions`, `paragraph_resolution_status` |

Offsets are half-open. Block offsets address `DocumentBlock.text`; document
offsets address the plain-text spine reconstructed with a blank line between
blocks. `source_block_id` always identifies the physical source block.
`source_para_id` identifies its owning legal paragraph when HUDOC has split
one printed paragraph across adjacent HTML blocks; continuation blocks can
therefore share that address while retaining distinct block IDs and offsets.
Repeated printed paragraph numbers still receive stable suffixed legal IDs.
`target_paragraphs` preserves printed labels and bounded ranges such as
`10-12`; it is not silently treated as a source-paragraph address.

`citation-occurrence/v3` counts printed loci and authority uses separately.
`locus_id` identifies one physical citation envelope independently of target
resolution. A compound citation can therefore produce several occurrence rows
that share `locus_id` and `citation_group_id`, while each row identifies its
own procedural document and owns its own pinpoint. Count unique `locus_id`
values for printed loci; count occurrence rows for authority-specific uses.
Readers continue to accept v1 and v2 rows, whose `locus_id` may be absent.

A citation printed inside a footnote keeps the footnote body as its exact
source address and also records `source_invoking_block_ids` and
`source_invoking_para_ids`. The structured `source_invocations` list also
retains each invoking paragraph's component, opinion identity, ordinal, type,
authors, and joiners. It therefore cannot collide with a same-numbered majority
or opinion paragraph, while downstream studies can join the footnote text back
to every paragraph that invoked it.

`paragraph-edges-inclusive.parquet`/JSONL is the additive source-paragraph to
target-paragraph graph. It contains one row per mapped target paragraph, with
the printed pinpoint, source component/opinion identity, exact target block
and paragraph IDs, source footnote/invoking addresses, target section/text,
mapping status, language, and target-HTML checksum.
`target_block_id` is the first physical block for compatibility;
`target_block_ids` lists every physical block belonging to the mapped legal
paragraph, and `target_text` joins their text in source order.
One physical footnote occurrence fans out into one edge per structured
invocation and mapped target paragraph. `citation_source_block_id` continues
to identify the physical footnote body.
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

Historical European Commission reports and admissibility decisions are also
retained when the printed source supplies a Commission-specific date or an
application number with a complete `D.R.` volume-and-page locator. These rows
carry the `echr_commission` namespace and remain explicitly unresolved: a bare
`D.R.` volume, a nearby date, or a Court judgment for the same application can
never promote them into a Court-document edge. Nested application-number spans
are folded into the complete Commission envelope, and compatible overlapping
SCL provenance is merged into one physical locus.

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

The citing document's own application numbers and bare party/title aliases do
not establish an external authority. A genuinely distinct earlier procedural
document from the same application is considered only when the source and
candidate dates differ and the printed envelope supplies a compatible
document-kind or phase cue.

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

Dated external-project descriptions are maintained in the claim audit. Their
retrieval metrics are not comparable to citation extraction or resolution.
`echr-py` documents a different citation contract: multilingual public
acquisition, name-and-namespace gates, exact procedural-document resolution,
separate majority/opinion/footnote provenance, independently owned pinpoints,
and source-paragraph-to-*cited-paragraph* edges. A superiority claim still
requires an exhaustive identical-scope benchmark; architectural breadth is not
a substitute for measured precision and recall.

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
