# README claim audit

Checked on **20 August 2026**. This audit separates facts verified from this
repository from mutable descriptions of external projects. It is not a legal
opinion or a substitute for corpus-scale comparative evaluation.

## Internal capability claims

| Claim family | Verification |
| --- | --- |
| Python 3.11–3.14, package 0.2.0 | `pyproject.toml`, runtime version, CI matrix, wheel build, and isolated installation |
| HUDOC/HUDOC-EXEC typed acquisition | CLI/API tests and public case/execution fixtures |
| Native `represented_by` metadata | `Case.represented_by` with `representedby` alias and export/search regression tests |
| Public package boundary | The wheel and source distribution must match an exact packaged-file manifest, pass additional boundary checks, and install outside the checkout |
| Deciding bench independent of dissents | Segmentation/bench tests; separate-opinion authors are stored independently |
| SCL compatibility graph | Regression tests require unchanged SCL edges/count semantics when inclusive and paragraph layers are enabled |
| Inclusive and paragraph citation artifacts | Occurrence, opinion, pinpoint ownership, target mapping, CLI/MCP parity, and offline tests |
| Optional model studies | Fake-provider runner/budget/evidence tests; Gemini realtime and native-batch canaries passed on 21 July 2026; OpenAI realtime and native-batch canaries passed on 12 August 2026 using the pinned `gpt-4.1-mini-2025-04-14` snapshot |
| Hybrid retrieval | Lexical/dense/RRF, stale-manifest, filter, deterministic-score, and parity tests |
| Offline graph HTML | Vendored D3 licence/content and no-CDN tests; JSON/GEXF/HTML adapter tests |
| Corpus release contract | Frozen item/ECLI selections, all declared ENG/FRE versions, rich tables, typed failures, checksum/FK validation, and deterministic ZIP tests |

The current release-preparation pass contains 642 passing offline tests and six
expected skips, with Ruff, mypy, wheel-content, and isolated-install checks
passing. Counts can grow after this dated audit; CI is the live source of truth.

## External claims checked

| Project/fact | Current evidence and qualification |
| --- | --- |
| HUDOC Researcher | [Zenodo v1.0.0](https://zenodo.org/records/21319704) remains a 2.9 MB software archive. The current [repository](https://github.com/lszoszk/ECHR-Dashboard) at inspected revision `ab55ff97e622eb3eb2f9f8068a7d9c3a840807d6` reports 20,010 judgments and 3.30 million segmented rows through 23 July 2026, plus Voyage/FAISS/reranking over 1.32 million paragraphs. It says the corpus DB, ~1.3 GB index, and 5 GB embeddings are not in Git. Its paragraph-level citation table resolves raw application-number matches to an in-corpus case and retains a source paragraph row and excerpt; it does not document citation-owned target pinpoints or verified source-to-target paragraph edges. |
| `echr-extractor` | [PyPI](https://pypi.org/project/echr-extractor/) currently lists 1.3.1 (7 July 2026), Apache-2.0, multilingual metadata/full-text extraction, SCL network creation, missing references, and optional external resolution. |
| Mumford annotations | Pinned revision `166b15c31276bfa8c8775e7e7def575e916aced7`; [repository](https://github.com/jamumford/ECHR-citation-context-v1) declares CC0-1.0. Local import found 115 curated XMI documents, 5,481 curated annotations, and 8,899 individual-annotator annotations. The historical 78.2% figure is an occurrence-reusing identity/context proxy, not strict span recovery. The identity-gated text-only v2 run aligns 2,684/5,481 annotations (49.0%); the final full-inclusive run aligns 4,444/5,425 selected ECHR labels (81.9%) after unique XMI-Sofa projection while abstaining on procedural-document ambiguity. These are selected-annotation recovery measures, not detector precision. The [methodology audit](mumford-methodology-audit.md) explains the scope, abstentions, and a source-document grouping defect in the published repetition analysis. |
| ECtHR-PCR | Pinned revision `8c95c9aa537f9acbd475b9c34f79e1de46285d0c`; 15,729 application-keyed records in the external dataset inspected. No licence was declared in the pinned repository/dataset card, so the README warns rather than assumes permission. |
| ECHR Open Data | The current [download site](https://echr-opendata.eu/download/) lists version 2.0.0 with processed data in several formats, and its [licence page](https://echr-opendata.eu/license) identifies the data licence as ODbL. Those terms are not copied transitively into `echr-py` artifacts. |
| Court text reuse | The [Court notice](https://www.echr.coe.int/en/copyright-and-disclaimer) permits attributed, free private/informational/educational reproduction and requires permission for other, especially commercial, uses. Translation rights can differ. |

## Wording policy

The README makes a scoped feature statement: `echr-py` combines exact citation
occurrences, procedural-document identity, citation-owned pinpoints, verified
target paragraphs, component provenance, and portable graph exports in one
contract. It does not claim universally higher precision, retrieval accuracy,
adoption, corpus scale, or UI quality than every alternative.

Before the next release:

1. refresh both official English and French Court citation lists;
2. rerun the full test/wheel matrix and both live-provider canaries;
3. rerun citation resolution and the Mumford/full inclusive benchmarks;
4. recheck rolling PyPI, GitHub, ECHR, and data-licence pages;
5. replace this audit date and record exact source revisions/checksums.
