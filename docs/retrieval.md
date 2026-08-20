# Portable Paragraph Retrieval

The paragraph index is user-built and source-addressable. `echr-py` does not
host a corpus or vector service.

## Lexical, Dense, And Hybrid

```bash
echr-py local index-paragraphs --data-dir corpus/ \
  --database corpus/paragraphs.sqlite

echr-py embeddings build --database corpus/paragraphs.sqlite \
  --provider sentence-transformers --model EXACT_MODEL_ID \
  --model-revision EXACT_MODEL_COMMIT \
  --section facts --section the_law --section separate_opinion \
  --include-footnotes \
  --out corpus/embeddings/

echr-py local paragraphs "effective investigation" \
  --database corpus/paragraphs.sqlite --mode hybrid \
  --embeddings corpus/embeddings/ --limit 25
```

Lexical mode uses SQLite FTS5. Semantic mode performs chunked exact cosine
search over normalized float32 vectors. Hybrid mode applies deterministic
reciprocal-rank fusion and reports lexical, dense, and fused scores and ranks
separately. An optional FAISS index can accelerate unfiltered dense search but
is rebuildable and never authoritative.

Install local Sentence Transformers with `echr-py[embeddings-local]`; add the
rebuildable FAISS accelerator with `echr-py[embeddings-faiss]`.

The embedding manifest records provider, exact model and local model revision,
dimensions, prompts,
normalization, paragraph/source hashes, corpus identity, package version, and
checksums. Dense search refuses a stale or configuration-mismatched artifact.

Section selection is optional and repeatable. With no `--section`, every
paragraph is embedded for backwards compatibility. `--include-footnotes`
retains linked footnote bodies even when their inherited section is outside the
selected body sections and requires a `hudoc-paragraphs.v3` index. When a rich
`paragraphs.parquet` table is present, index construction preserves footnote
IDs and individual-opinion ordinal, type, authors, and joiners directly in each
retrieval row; otherwise it falls back to segmenting the available plain text.

Filters include language, section, document, majority/opinion/appendix source
component, opinion identity, and date. Python, CLI, and MCP results use the
same stable paragraph IDs and source metadata.

Use `echr-py embeddings verify` before reuse and
`echr-py study benchmark-retrieval` with labelled bilingual qrels to report
Recall@k, MRR, nDCG, latency, and the complete index/model configuration.
