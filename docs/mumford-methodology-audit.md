# Mumford citation-context methodology audit

Checked against the CC0 repository at revision
`166b15c31276bfa8c8775e7e7def575e916aced7` and the paper supplied in
[Mumford et al., *An Empirical Study of Citation Practices in the European
Court of Human Rights*](https://doi.org/10.3233/FAIA251584) on 21 July 2026.

## Appropriate use

Mumford et al. provide a useful manually curated English citation-*use*
corpus. Its strongest contributions are repeated short-form links, Article or
Protocol labels, Positive/Neutral/Negative judicial-consideration labels, raw
annotator outputs, and curator uncertainty. It is a useful reference for an
optional `echr-py` study that labels how a citation is used.

It is not an exhaustive citation-detection or citation-resolution gold set.
The annotators received only `THE LAW` and were instructed to select citations
tied to the Court's reasoning while omitting submissions, general-principles
material, and other non-determinative references. Separate opinions,
footnotes, procedure, facts, operative provisions, appendices, and front
matter are consequently outside its measurement frame.

The XMI retains plain source text, character spans, a free-text citation name,
source type, Article/Protocol, and treatment. It does not retain HUDOC HTML,
typography, paragraph or opinion identities, linked footnotes, canonical target
item IDs/ECLIs, document phases, structured application numbers, or structured
cited-paragraph pinpoints.

## Inspected release

The pinned import contains:

- 115 curated documents and 5,481 curated spans;
- 182 raw-annotator documents and 8,899 raw spans;
- 5,425 ECHR, 36 other-international, 19 domestic, and one ICJ curated label;
- 2,871 Neutral, 2,280 Positive, and 330 Negative treatment labels;
- 490 `cited above` spans in 49 documents;
- 2,938 spans printing a section sign and 2,634 printing an application number.

These last two values are textual signals, not resolved target-paragraph or
target-document fields.

## Reproducible descriptive-statistics issue

The released Figure 2 script pools citations across every document in a
judicial-output stratum before grouping on the literal free-text `Citation`
field. It therefore loses the citing-document identity even though the paper
describes repetition “in a given case”. It then occurrence-weights the groups.
That implementation reproduces the published approximately 28% once and 33%
six-or-more shares.

Grouping instead by `(source document, exact stripped Citation)` yields 3,050
authority-document groups in the independently audited import:

- 2,159 (70.8%) occur once in their source document;
- 891 (29.2%) repeat;
- 115 (3.8%) occur six or more times.

A different, valid occurrence-weighted statement is that 60.6% of the 5,481
individual spans belong to a repeated authority-document group. It should not
be described as the share of authorities repeated in a given case.

The same loss of source-document identity affects the released Table 1
analysis. With document identity retained, judicial consideration varies
within 535 of the 891 repeated groups (60.0%). Article/Protocol varies in 40.5%
when every supplied nested label is retained, or 34.2% under the released
script's first-label-only rule. The latter discards additional labels on 789
curated spans.

## Observed annotation limitations

Manual work adds genuine value, but the inspected records also include merged
multi-authority spans, phase-collapsed identities, spelling variants, and some
misclassification. Examples include a span containing both *Ireland v. the
United Kingdom* and *Cyprus v. Turkey* but carrying only the former identity;
combined *Khan*/*N.T.P.*, *Orchowski*/*Norbert Sikorski*, and *Emre*/*Hertel*
spans; `Loizi` and phase variants; and the Belgian Linguistic Case marked as
other-international case law.

Six curated decisions have unnumbered paragraphs. The repository's convenience
XMI-to-JSONL converter requires an `NN.` paragraph prefix and therefore drops
their twelve annotations. The paper reports three annotators, while the release
contains four raw identifiers with uneven document coverage. These facts do
not invalidate the curated corpus, but they matter for reproducibility and
denominators.

## Fair `echr-py` comparison

The historical [text-only baseline](benchmarks/mumford-text-only-baseline.json)
associated 4,286 of 5,481 selected annotations (78.2%) with deterministic
strong occurrences using normalized identity/paragraph context. It allowed
occurrence reuse and was therefore neither strict source-span nor one-to-one
recovery. The identity-gated v2 run aligns 2,684/5,481 annotations one-to-one
(49.0%): 2,626 strict source-span matches, 58 identity/context-only matches,
2,004 ambiguous abstentions, and 793 unmatched annotations. It deliberately excludes
SCL-derived local gazetteers, HUDOC target lookup, and optional model labels.
Because the reference annotations are selective rather than exhaustive
negatives, none of these measures is precision. Printed pinpoints are also not
the same as resolved target paragraphs.

A publication-quality comparison should report separately:

1. strong-envelope and local-short-form recovery on Mumford's selected scope;
2. canonical application- and document-level resolution;
3. printed pinpoint extraction and verified target-paragraph mapping;
4. full-document majority, opinion, and footnote coverage on a new exhaustive
   labelled sample;
5. optional treatment-label agreement, abstention, and curator disagreement.

For full rich-HUDOC outputs, `echr-py` now projects each occurrence into the
historical XMI Sofa using only a uniquely occurring normalized paragraph or
citation window. It retains both coordinate systems, requires compatible
bibliographic identity for every match, and treats opinions, footnotes,
out-of-scope sections, ambiguous projections, and one-to-many alignments as
explicit abstentions. This corrects the coordinate-system mismatch but does
not turn Mumford's selected annotations into exhaustive detector negatives or
canonical target-document and target-paragraph gold data.

On 20 August 2026, the final deterministic inclusive pipeline aligned 4,444 of
the 5,425 selected ECHR-labelled annotations (81.9%) one-to-one after unique
XMI-Sofa projection: 4,436 strict-span and eight identity/context matches. It
abstained on 539 ambiguous annotations and left 442 unmatched. Of 8,139 local
occurrences, 5,614 projected into the reference coordinate system and 2,525
were explicit scope or mapping abstentions. Names shared by distinct
judgments and decisions remain ambiguous unless local evidence identifies the
procedural document; they are not coalesced merely to increase recovery. This
is selected-annotation recovery, not detector precision. The reference
supplies neither canonical target documents nor structured target paragraphs,
so the local resolution and paragraph-mapping values are coverage measures,
not accuracy measures.
The compact [full-inclusive audit](benchmarks/mumford-full-inclusive-audit.json)
records the inputs, hashes, denominators, and qualifications.

The defensible conclusion is therefore specific: `echr-py` exposes a broader,
inspectable citation contract than this annotation release. A detector-accuracy
superiority claim still requires an exhaustive identical-subset benchmark.
