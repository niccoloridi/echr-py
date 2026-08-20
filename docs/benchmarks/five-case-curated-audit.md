# Five-case citation audit

Run on 21 July 2026 against the pinned Mumford curator XMI and current public
HUDOC metadata. This is a purposive difficult-case audit, not a random sample
or a corpus-wide precision estimate.

The original XMI Sofa strings were preserved exactly, including their
`CR/CRLF` separators, so curator and occurrence character offsets remain
comparable. The run supplied each source document's current SCL field when
HUDOC exposed one, then ran deterministic inclusive occurrence extraction.

| HUDOC item | Curated uses | Automatic occurrences | Historical identity/context alignment | Ambiguous | Unmatched | SCL present |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `001-241433` | 6 | 6 | 6 (100%) | 0 | 0 | no |
| `001-211020` | 39 | 7 | 7 (17.9%) | 0 | 32 | no |
| `001-127684` | 122 | 163 | 116 (95.1%) | 6 | 0 | yes |
| `001-207622` | 131 | 86 | 82 (62.6%) | 38 | 11 | yes |
| `001-178082` | 140 | 143 | 113 (80.7%) | 27 | 0 | yes |
| **Total** | **438** | **405** | **324 (74.0%)** | **71** | **43** | – |

The 74.0% column was produced by the pre-v2 alignment routine. It is a
normalized identity/context proxy and permitted reuse of a local occurrence;
it is not strict one-to-one occurrence recall. The frozen audit remains useful
for its case-level error inspection, but its aggregate must be rerun with the
v2 contract before being quoted as an occurrence-recovery metric.

Among the four documents other than the no-SCL short-form stress case, the
unambiguous recovery is 317/399 (79.4%). Ambiguity is an abstention, not an
automatic target promotion.

## What inspection showed

- `001-241433` is a clean success, including later short forms.
- `001-127684` has no outright misses. The six abstentions include short forms
  such as *Blečić* and multi-application envelopes.
- `001-178082` finds the repeated *Ivanov* series well. The remaining
  uncertainty is concentrated in phase-bearing *Broniowski judgment* and
  *Broniowski (merits)* forms, where several procedural documents exist.
- `001-207622` is intentionally difficult: bracketed historical forms,
  inter-State cases, ICJ/PCA material, reporter-only locators, `ibid`, and
  several annotation identity defects. The resolver refuses to turn those
  external or phase-ambiguous forms into ECtHR document edges.
- `001-211020` begins with repeated naked *Catan*, *Mozer*, and *Ilaşcu* names.
  HUDOC supplies no SCL for this record and the supplied `THE LAW` slice lacks
  the earlier full anchors needed to establish those local aliases. The seven
  strong envelopes are found; the system abstains on the remaining 32 rather
  than assigning targets from surnames alone.

A manual review of the first 24 automatic occurrences not paired by the
historical curator alignment in `001-127684` and `001-207622` found all 24 to be
citation-related in context. Five were locator fragments (a Series A locator
or application number) that should ideally be consolidated with the adjacent
name envelope. This is useful engineering evidence, but the purposive sample
must not be presented as a 100% precision estimate.

## Improvements found by the audit

The audit directly produced three corrections:

1. benchmark alignment prioritises overlapping source offsets and, in v2,
   enforces one-to-one reference-to-occurrence assignment instead of treating
   identical repeated names anywhere in the document as interchangeable;
2. plain-text spines preserve offsets across `CRLF` and INCEpTION's `CR/CRLF`
   paragraph separators;
3. a pinpoint such as `§ 32, 14 January 2020` now yields only paragraph `32`,
   not the date's day `14`.

The remaining priorities are phase-aware short-form aliases, adjacent
name/reporter/application-number envelope consolidation, and a separately
labelled exhaustive sample containing majority text, opinions, and footnotes.
