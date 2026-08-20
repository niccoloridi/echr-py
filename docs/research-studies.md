# Bounded Research Studies

`echr-py` studies turn user-acquired HUDOC artifacts into reproducible,
schema-validated datasets. They are optional and are kept separate from all
deterministic legal-data parsing.

## Design Rules

- A YAML `StudySpec` fixes the corpus, unit, ordered stages, prompts, schemas,
  exact provider/model IDs, budgets, and outputs.
- The stage list is finite. A model cannot add stages or start an autonomous
  tool loop.
- Every source unit has a stable address. Citation occurrence units preserve
  source paragraph, section, majority/opinion identity, target identity, and
  pinpoints; document selections also retain native metadata such as
  `represented_by`.
- Required evidence quotations are checked exactly against the addressed
  source. Failed evidence is `invalid`, never silently accepted.
- Stable task IDs identify the same research question over the same source;
  attempt IDs distinguish provider, model, settings, or parameter changes.
- Runs are resumable and retain requests, responses, validation, diagnostics,
  usage, costs, and source checksums.
- Resume refuses changed specifications or sources. `--no-resume` refuses a
  non-empty run directory; it never appends duplicate work.
- Realtime stages use explicitly bounded `concurrency` and reserve request,
  token, and dollar budgets before dispatch.

Study hooks may be installed through the `hudoc_py.studies` entry-point group.
An MCP request can select only an installed and allowlisted hook; it cannot
provide an arbitrary Python path.

## Commands

```bash
echr-py study validate study.yaml
echr-py study plan study.yaml --out runs/study/
echr-py study run study.yaml --out runs/study/
echr-py study status runs/study/
echr-py study resume runs/study/
echr-py study resume runs/study/ --wait --max-polls 20
echr-py study cancel runs/study/
echr-py study export runs/study/ --format jsonl --out dataset.jsonl
echr-py study benchmark-retrieval \
  --database corpus/paragraphs.sqlite --qrels qrels.jsonl \
  --mode hybrid --embeddings corpus/embeddings/ --out benchmark.json
```

## Ordered stages and context

`select`, `acquire`, and `export` are recorded in stage manifests. Retrieval
candidates and validated upstream records can be inserted into later prompts
with `{{context}}`, `{{previous_output_json}}`, and
`{{stage_outputs_json}}`. Deterministic verification consumes prior records;
synthesis is unit-level unless `options.scope: corpus` is explicit. A rerank
stage must name a provider/model or an installed study hook.

`required_evidence: true` retains the v1 rule that a record needs at least one
verified quotation. `evidence_fields` adds JSON Pointer paths whose substantive
values each need their own evidence reference. Supplied offsets must select the
exact quoted source slice. An offset-free quote is accepted only when it occurs
once; repeated text is invalid until disambiguated. Evidence addresses retain
item, paragraph, section, opinion, and character coordinates.

## Native batches

Native batch execution is available only for `provider: gemini` and the
official `provider: openai` endpoint. Set `batch: true` on the stage. Gemini
and OpenAI-compatible third-party endpoints, Ollama, and Anthropic are
realtime-only; unsupported batch specifications fail validation.

The first ready batch stage is submitted atomically only if its estimated
requests, tokens, and cost fit the remaining budget. The run then becomes
`waiting`. `study resume` polls and retrieves it; `--wait` enables bounded
in-process polling. Each batch has a checksummed request file and versioned
manifest containing the remote job/file IDs, task IDs, timestamps, state,
usage, cost, result checksum, and diagnostics. Actual usage is reconciled when
results arrive. Credentials and provider API keys are never written to the run
bundle.

Example stage fields:

```yaml
- id: classify
  kind: extract
  provider: openai
  model: YOUR_EXPLICIT_MODEL
  prompt: "Classify this citation. {{context}}"
  temperature: 0
  max_output_tokens: 800
  concurrency: 1
  batch: true
  evidence_fields: [/proposition, /treatment]
```

## Citation-Use Profiles

The `citation-use` template labels already discovered citation occurrences.
It never discovers or resolves citations itself.

```bash
echr-py study init citation-use --source occurrences.parquet \
  --provider anthropic --model YOUR_EXPLICIT_MODEL \
  --taxonomy multiaxial --out citation-use.yaml
```

The packaged schemas are starting points:

| Profile | Intended use |
| --- | --- |
| `minimal` | Small study of voice, complaint Article, and positive/negative/neutral/mixed treatment with evidence and uncertainty |
| `multiaxial` | Separates source voice, degree of engagement, citation functions, treatment, proposition, confidence, and review status |

Pass `--schema` for a user-owned taxonomy. This is the appropriate place to
implement and compare alternative scholarly classifications: the taxonomy is
versioned study input, not package-wide ontology. Published labels should
report the exact schema, prompt, model, validation sample, and adjudication
procedure.

The starter profiles take inspiration from contextual-citation work and the
multi-axis approach implemented in sibling legal-research projects, but do not
claim to reproduce any named scholarly taxonomy verbatim.

## MCP Jobs

The ordinary MCP server is read-only. Study tools appear only with
`echr-py mcp --enable-jobs` and require an output root, permitted input roots,
provider and study allowlists, an explicit pricing profile, and a maximum
per-job dollar budget. Job state is persisted for inspection, cancellation,
and resume. Credentials and unrestricted filesystem paths are never returned.
