# Optional Structured Studies

The LLM layer is an optional, general-purpose facility for creating
research datasets. Ordinary HUDOC/HUDOC-EXEC acquisition, text segmentation,
bench and opinion parsing, citation discovery and resolution, pinpoint
mapping, retrieval, and graph export do not require a model or API key.

No domain-specific HUDOC-EXEC study schema or processing methodology is
shipped. Users studying acquired material must define their own explicit
schema and inputs.

## Install A Provider

```bash
pip install -e ".[research-agent,llm]"             # Gemini
pip install -e ".[research-agent,llm-openai]"      # OpenAI-compatible
pip install -e ".[research-agent,llm-anthropic]"   # Anthropic
```

Set the credential required by the provider, for example
`GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`. Never put secrets
in study YAML or result bundles.

Every model-backed stage must explicitly name both `provider` and `model`.
There is no implicit model selection or cross-provider fallback.

## Bounded Study Contract

A versioned YAML `StudySpec` declares:

- the local artifact or public query defining the corpus;
- the unit of analysis: document, section, paragraph, opinion, or citation
  occurrence;
- an ordered, finite set of select, retrieve, rerank, triage, extract, verify,
  synthesize, and export stages;
- exact provider/model IDs, prompts, response schemas, retrieval settings,
  and budgets;
- output and evidence requirements.

The runner does not invent stages or enter an open-ended tool loop. Stable
task IDs include the study version, source address, text hash, prompt/schema
hash, and stage. A changed model or parameters creates a distinct attempt.
Record states are `ok`, `invalid`, `error`, `skipped`, or `interrupted`; run
state also includes `waiting` for a submitted native batch. Resumption rejects
changed study/source checksums and retries errors without discarding attempts.

When evidence is required, each extracted fact must include a source address
and exact quotation. `required_evidence` retains the record-level v1 contract;
`evidence_fields` names JSON Pointer paths requiring their own quotations.
Offsets must select the exact slice. Without offsets, a quotation is accepted
only if unique in the addressed source; ambiguity makes the record invalid.

```bash
echr-py study validate study.yaml
echr-py study plan study.yaml --out runs/example/
echr-py study run study.yaml --out runs/example/
echr-py study status runs/example/
echr-py study resume runs/example/
echr-py study resume runs/example/ --wait --max-polls 20
echr-py study cancel runs/example/
echr-py study export runs/example/ --format parquet --out dataset.parquet
```

The bundle retains the resolved specification, source selection and checksums,
prepared requests, raw responses, validated JSONL/Parquet rows, diagnostics,
usage, costs, and optional report. Request, token, and dollar limits are
enforced; a dollar-capped job needs an explicit price for the exact model.

Stages pass `temperature`, `max_output_tokens`, system instruction, provider
options, and bounded `concurrency` to every request. `batch: true` means a real
persisted Gemini or official OpenAI Batch API job – not merely discounted cost.
The initial command returns a `waiting` run after submission; resume
polls/retrieves it. `openai-compat`, Ollama, and Anthropic are realtime-only and
unsupported batch specifications fail validation.

## Optional Citation-Use Labelling

Deterministic `occurrences.parquet` rows can be labelled without modifying the
citation layer:

```bash
echr-py study init citation-use \
  --source corpus/citations/occurrences.parquet \
  --provider openai --model YOUR_EXPLICIT_MODEL \
  --taxonomy multiaxial --out citation-use.yaml
echr-py study run citation-use.yaml --out runs/citation-use/
```

`minimal` and `multiaxial` are editable starter schemas. The multiaxial profile
separates source voice, engagement, legal function, treatment, complaint
Articles, proposition, confidence, and review status. It does not assume that
a party's criticism is the Court's negative treatment. Both profiles require
evidence and preserve uncertainty.

Use `--schema your-schema.json` to supply another JSON Schema, including a
faithful implementation of a published taxonomy. Profiles are optional study
metadata, not hard-coded citation truth, and labels never alter SCL or
inclusive graph identities or weights.

See [research studies](research-studies.md) for the artifact and hook contract.
