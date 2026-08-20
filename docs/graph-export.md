# Unified Graph Export

`hudoc-graph/v1` is a neutral graph bundle with `meta`, `nodes`, and `links`.
JSON preserves typed attributes; GEXF applies documented scalar conversions;
HTML is a self-contained offline viewer with vendored D3 and its licence.

```bash
echr-py graph export --kind citation-scl --format json \
  --in corpus/citations/ --out scl-graph.json
echr-py graph export --kind citation-inclusive --format gexf \
  --in corpus/citations/ --out inclusive.gexf
echr-py graph export --kind citation-paragraph --format html \
  --in corpus/citations/ --out paragraph-citations.html --max-nodes 1000

# Focus a real demonstration without allowing global pruning to hide it
echr-py graph export --kind citation-paragraph --format html \
  --in corpus/citations/ --out opinion-footnotes.html \
  --source-component opinion --footnotes-only
```

Adapters cover authoritative SCL decision graphs, additive inclusive citation
graphs, source-paragraph-to-target-paragraph graphs, and custom NetworkX
graphs. The SCL graph remains the non-paragraph-aware
compatibility baseline: inclusive and paragraph occurrences cannot change its
edges or `citation_count` semantics.

Paragraph edges preserve source majority/opinion identity. A majority
paragraph and a separate opinion may both cite the same target paragraph; they
are distinct source edges, not a collision.

Directory inputs are enriched from occurrence and node artifacts with case
titles, ECLIs, exact citation text, context, opinion metadata, target sections,
and footnote/invoking-paragraph provenance. Repeat `--source-item`,
`--source-component`, or `--opinion-id` to focus a graph; `--footnotes-only`
retains physical footnote occurrences and their invoking paragraphs.

The browser viewer offers search, attribute filters, component isolation,
direction and minimum-weight controls, legends, node sizing/colouring, detail
panels, scale warnings, and explicit top-N pruning. It is a portable artifact,
not a hosted dashboard, and makes no CDN request.
