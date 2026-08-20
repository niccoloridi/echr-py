# Release data and feature boundary

This page records what the `echr-py` public distribution and source repository
contain. It is both a release checklist and a safeguard against accidentally
publishing domain-specific research implementations.

## Wheel and source-distribution data

| Material | Purpose | Status |
| --- | --- | --- |
| `citation_authority.csv` and `.json` | Current official English/French citation forms and equivalence links used by deterministic resolution | Packaged default |
| `citation_authority_eng.csv` and `.json` | Legacy English-only v1 snapshot retained for file compatibility | Packaged |
| `citation_authority_supplements.json` | Small, documented resolver supplements | Packaged |
| `historical_citation_catalog.json` | Checksummed public-HUDOC metadata for historical resolution | Packaged |
| `kpthesaurus_eng.json` | English HUDOC keyword thesaurus | Packaged |
| citation-use JSON Schemas | Optional, generic user-defined studies over already located citations | Packaged |
| D3 JavaScript and licence | Fully offline graph viewer | Packaged |

No raw HUDOC/HUDOC-EXEC corpus, separately staged corpus snapshot, embedding matrix,
competitor dataset, model response, API credential, or private study dataset is
included in the Python distribution.

## Repository-only research fixtures

The source repository additionally carries:

- short synthetic English/French opinion-boundary fixtures and expectations
  under `tests/data/opinions/`;
- small citation-edge, reviewed-resolution, and benchmark fixtures;
- generated citation/acquisition/MCP demonstrations with adjacent provenance;
- an offline citation-network example; and
- scripts that reproduce the demonstrations.

The opinion fixtures contain invented body text and only the heading forms
required to test deterministic author and joiner parsing. See the
fixture-specific [notice](../tests/data/opinions/README.md).

## Public HUDOC-EXEC boundary

The supported HUDOC-EXEC surface stops at public-source acquisition and neutral
conversion:

- official case and document metadata search;
- application-number links between HUDOC and HUDOC-EXEC records;
- resumable source download and manifests;
- text/Markdown conversion and opt-in OCR; and
- neutral import of official CSV/XLSX exports.

Official collection names, themes, statuses, precedents, payments, and linked
document identifiers remain source metadata. The package does not ship a
substantive implementation taxonomy, domain classification, relationship model,
consolidation prompt, or execution-report generator.

## Optional model-backed studies

The study runner is general infrastructure: users must provide the corpus,
provider, exact model, prompts, schema, evidence rules, and budgets. The two
included starter schemas label how a *citation* is used. One schema can record
whether the source voice is the Court or a party submission and whether a
citation concerns a remedy; these are generic citation-context fields rather
than an extraction method for a separate empirical domain.

Model-derived labels can never alter deterministic occurrence identities,
resolution decisions, SCL counts, or graph edges.

CI checks both the wheel and source distribution against an explicit allowlist
of every packaged file, applies additional boundary checks, and then installs
the wheel outside the checkout.
