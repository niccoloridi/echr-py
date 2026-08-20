# Source-Aware Text Segmentation

`echr-py` treats a judgment as an ordered document before treating it as a
string. This matters because HUDOC HTML is generated from Word documents: its
paragraph elements, classes, font sizes, bold text, alignment, and order carry
structural evidence that is lost by flattening the page and applying regexes to
the result.

## The document spine

With `rich_sections=True`, the parser first builds a versioned
`DocumentSpine` (`hudoc-spine.v1`). Every source block records:

- a stable local block ID and source-order character span;
- block type, such as paragraph, heading, list item, block quotation, or
  footnote body;
- literal text and, for HTML, source tag, CSS classes, and relevant styles;
- an exact paragraph number when the block starts with `N. `;
- a stable local paragraph ID;
- heading level, role, and the evidence supporting that classification;
- the canonical section assigned to the block, if any.

Numbered paragraphs use their printed number. Repeated printed numbers receive
deterministic suffixes such as `12-2`. Unnumbered material before the first
numbered paragraph uses `pre-001`, while later unnumbered material uses
`u-001`. These are local identifiers for one source record and parser version,
not substitutes for an ECLI or HUDOC item ID.

HUDOC's Word conversion stores footnote references inline (for example
`#_ftn1`) and bodies at the physical end of the document. The spine preserves
that relationship explicitly. A logical `DocumentFootnote` can own several
body blocks, records every invoking block/paragraph, and gives body blocks
addresses such as `fn-ftnN` and `fn-ftnN-2` rather than treating their printed
label as a judgment or opinion paragraph number. Footnote IDs remain outside
canonical section-span paragraph lists. When every reference agrees, the body inherits the
invoking section and opinion identity; cross-context, missing-body, and orphan
body cases remain diagnostics. A note invoked from conflicting components is
kept neutral rather than being attributed to its physical terminal location.
`html_to_md()` renders the same structure as
`[^N]` and `[^N]: ...`, with indented continuation paragraphs.

Canonical sections are derived from the spine. The backwards-compatible flat
fields remain available:

```python
case = fetch_case(
    itemid="001-57574",
    with_text=True,
    rich_sections=True,
)

sections = case.sections
print(sections.status, sections.confidence)
print(sections.facts)

for span in sections.spans:
    print(span.section, span.start_block, span.end_block, span.paragraph_ids)

for block in sections.spine.blocks:
    print(block.block_id, block.para_id, block.type, block.section)
```

`SectionSpan.end_block` is exclusive. This makes
`spine.blocks[span.start_block:span.end_block]` the exact block view of the
section.

## Boundary acceptance

For structured HTML, a canonical phrase is eligible as a top-level boundary
only when the source block is independently supported as a heading and the
whole block matches a recognised English or French heading grammar. Ordinary
body text containing “the facts of the case” or “for these reasons” cannot
become a boundary.

The parser handles modern judgments, Committee templates, communicated cases,
and historical Commission decisions separately. Historical sources are not
forced into one modern order. For example, Commission material can use
`FACTS`, `COMPLAINTS`, `PROCEDURE`, `LAW`, while a communicated case can place
an annex before `QUESTIONS TO THE PARTIES`. The recorded spans always retain
physical source order.

Plain text receives a best-effort spine based on blank-line blocks. It uses the
same whole-block grammar, including verified historical forms such as singular
`GRIEF`, `PROCÉDURE ET FAITS`, and an `ANNEX` or `APPENDIX` joined to its
subtitle. HTML remains preferable because it provides stronger source
evidence.

## Failure semantics

Segmentation is never silently represented as success:

| Status | Meaning |
| --- | --- |
| `complete` | All required slots for the detected document template were found |
| `partial` | At least one boundary was found, but a required template slot is missing |
| `unsegmented` | No accepted canonical boundary was found |
| `not_applicable` | The record is intentionally outside the judgment templates, such as a press release or information note |

`confidence` is a deterministic quality score based on template coverage,
section breadth, source format, and suspiciously short slices. It is not a
calibrated probability of correctness. Use `status`, `diagnostics`, `spans`,
and the source blocks for review rather than applying a confidence threshold in
isolation.

Diagnostics identify missing expected sections, duplicate printed paragraph
numbers, short sections, table-of-contents candidates, and complete failure.
The original text and spine are retained even when the status is
`unsegmented`.

## Command line

Segment one downloaded HUDOC HTML record:

```bash
echr-py segment --in judgment.html --doctype HEJUD \
  --document-id 001-57574 --out judgment.segments.json
```

Segment a table of records. Field names are configurable:

```bash
echr-py segment --in texts.parquet --out segments.parquet \
  --text-field text --document-id-field itemid \
  --doctype-field doctype --doctype-branch-field doctype_branch
```

For live retrieval, use `--rich-sections` with `fetch-case` or `smart-fetch`.

## Separate opinions

The accepted `separate_opinion` span is passed to the individual-opinion
parser. Opinion objects retain the complete heading-plus-body text and expose
an exact `body` accessor. Header normalization therefore never needs to be
reverse-engineered with `text[len(raw_header):]`. A judgment with no opinion
block is a successful no-op with opinion confidence `1.0`; a non-empty block
whose opinion headings cannot be parsed returns a diagnostic failure.

## Validation and limits

The regression suite includes source-style HTML, table-of-contents duplicates,
body-phrase traps, duplicated paragraph numbers, historical Commission order,
communicated-case annex order, English and French opinion headings, and real
HUDOC opinion fixtures.

The parser has also been exercised against the 100 real-document text fixtures
shipped with `echr-extractor` 1.3.1. They are useful regression samples, not an
independent gold standard.

Known limits remain:

- plain text lacks the styling and element evidence available in HUDOC HTML;
- historical scans or OCR can merge headings with body text;
- paragraph IDs are document-local and depend on the selected language record;
- confidence is diagnostic, not probabilistic;
- no parser can infer missing source structure with certainty, so partial and
  unsegmented records must remain visible in corpus QA.
