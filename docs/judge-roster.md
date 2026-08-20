# Deciding Benches, Judge Roster, And Separate Opinions

`echr-py` includes a canonical ECHR judge roster and uses it when parsing
both the deciding bench and separate opinions. These are two independent
sources: the bench comes from the judgment's composition front matter, while
opinion authors come from the annexed opinion headings.

The roster lives in `hudoc_py.text.judges` and currently contains 188 curated
names: 168 elected judges and 20 ad hoc judges, drawn from the official
[Judges of the Court since 1959](https://www.echr.coe.int/d/list_judges_since_1959_bil)
list. It is not yet an exhaustive transcription of every historical judge;
unknown names therefore remain visible in parser diagnostics rather than being
fuzzily assigned.

## Deciding Bench

With rich segmentation enabled, English `composed of` and historical
composition formulas, plus French `composée de` and `siégeaient` blocks, are
parsed into `case.sections.bench`:

```python
from hudoc_py import fetch_case

case = fetch_case(itemid="001-69022", with_text=True, rich_sections=True)
bench = case.sections.bench
if bench:
    for member in bench.members:
        print(member.name, member.role, member.is_ad_hoc)
```

Each `BenchMember` carries the literal and normalized name, role, source block
and character offsets, and – when the curated roster has a match – election state,
region, and coarse tenure. `BenchComposition` carries the raw composition
slice, source blocks, language, confidence, and diagnostics. A roster match is
enrichment, not a membership gate: unknown historical names remain visible.

This list is the deciding composition printed in the judgment. It is not
derived from dissents, concurrences, declarations, or who signed an opinion.

## Separate-Opinion Names

Separate-opinion headings in HUDOC texts are not uniform. Names may appear with
missing diacritics, abbreviated surname forms, page-number artifacts, or local
phrasing such as "joined by Judge ...". The roster layer normalizes those
variants so downstream analysis can group opinions by the same judge reliably.

Examples:

| Input | Canonical output |
| --- | --- |
| `ZUPANCIC` | `ZUPANČIČ` |
| `BRATZA` | `SIR NICOLAS BRATZA` |
| `ALBUQUERQUE` | `PINTO DE ALBUQUERQUE` |
| `POWER‑FORDE` | `POWER-FORDE` |
| `LÓPEZ-GUERRA` | `LÓPEZ GUERRA` |
| `VAJIC` | `VAJIĆ` |
| `KJOLBRO` | `KJØLBRO` |

## Opinion Parsing

When rich segmentation is enabled, separate opinions are split into individual
`Opinion` records:

```python
from hudoc_py import fetch_case

case = fetch_case(itemid="001-69022", with_text=True, rich_sections=True)

for opinion in case.sections.opinions:
    print(opinion.opinion_type)
    print(opinion.joint)
    print(opinion.authors)
    print(opinion.joined_by)
    print(opinion.judges)
    print(opinion.raw_header)
```

Each `Opinion` has:

- `opinion_type`: for example `dissenting`, `concurring`, `partly_dissenting`,
  `partly_concurring`, `partly_concurring_partly_dissenting`, `separate`, or
  `declaration`.
- `joint`: `True` when the heading is joint or multiple judges are parsed.
- `joint_heading`: whether the heading literally says `JOINT` or `COMMUNE`.
- `authors`: the canonicalized author or authors named by the opinion heading.
- `joined_by`: judges identified by `joined by`, `approuvée par`, `à laquelle
  se rallie`, and equivalent Court formulations.
- `judges`: the backwards-compatible union of `authors` and `joined_by`.
- `raw_header`: the matched heading.
- `text`: the complete opinion slice, including its heading.
- `body`: the exact body-only text. Use this instead of slicing `text` by the
  normalized `raw_header` length, especially for multiline headings.
- `language`: `EN` or `FR` for the heading grammar that matched.

You can also split a separate-opinions block directly:

```python
from hudoc_py.text import split_opinions, split_opinions_report

opinions = split_opinions(separate_opinion_text)
report = split_opinions_report(separate_opinion_text)
print(report.confidence, report.diagnostics)
```

Rich `Sections` records expose the same quality information as
`opinions_confidence` and `opinion_diagnostics`. Diagnostics identify unknown
roster names, unparsed preambles, low coverage, suspiciously short bodies,
discarded table-of-contents rows, index blocks, and repeated running headers.
Informational discard diagnostics carry no direct penalty; excluded material
may still surface through the independent coverage or preamble checks.
An absent opinion block is not a parser failure and has confidence `1.0`; a
non-empty block with no recognized headings has confidence `0.0` and the
`no_headings_in_block` diagnostic.

The offline fixture corpus contains seven full HUDOC texts representing six
independent cases, plus a Court-PDF excerpt for the wrapped dot-leader ToC
layout. It includes a zero-opinion Committee control, historical French
`APPROUVEE PAR` headings, a lowercase declaration, and a three-opinion Grand
Chamber annex. Exact repeated-author running-header deduplication remains a
synthetic regression because that conversion shape was not present in the
pinned Court HTML/PDF samples.

## Direct Roster Lookups

```python
from hudoc_py.text import (
    is_ad_hoc_judge,
    judge_country,
    judge_region,
    judge_years,
    normalise_judge_name,
)

normalise_judge_name("ZUPANCIC")  # "ZUPANČIČ"
judge_country("TULKENS")         # "Belgium"
judge_region("TULKENS")          # "Western Europe"
judge_years("COSTA")             # (1998, 2011)
is_ad_hoc_judge("REED")          # True
```

`judge_country()` returns the state in respect of which the judge was elected,
not necessarily personal nationality. Ad hoc judges are tracked separately and
return `None` for country and region lookups.

Unknown names are handled conservatively:

```python
normalise_judge_name("NOT A JUDGE")  # "NOT A JUDGE"
judge_country("NOT A JUDGE")         # None
judge_region("NOT A JUDGE")          # None
judge_years("NOT A JUDGE")           # (None, None)
is_ad_hoc_judge("NOT A JUDGE")       # False
```

## Public Imports

The convenience imports are exposed from `hudoc_py.text`:

```python
from hudoc_py.text import normalise_judge_name
from hudoc_py.text import judge_country, judge_region, judge_years
from hudoc_py.text import is_ad_hoc_judge, split_opinions, split_opinions_report
```

The underlying constants are available from `hudoc_py.text.judges` for audit
or analysis workflows:

- `JUDGE_COUNTRY`
- `JUDGE_REGION`
- `JUDGE_YEARS`
- `AD_HOC_JUDGES`

## Caveats

- The roster is a research utility, not a legal authority.
- Country means election state at the Court.
- Tenure years are coarse start/end years, not exact appointment dates.
- Normalization targets composition blocks, separate-opinion headings, and
  roster lookup, not arbitrary natural-language biography extraction.
- Unknown names are preserved and reported; they are never fuzzily coerced to
  the nearest roster member.
