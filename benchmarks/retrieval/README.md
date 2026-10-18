# Retrieval quality benchmark

Use two isolated Core → Sidecar → MCP stacks with separate databases and the
same embedding service, SDK source, model configuration and resource limits.
The baseline process disables NLP; the second enables it. Each exposes the same
configured reranker. Never seed a production database.

`corpus.json` contains 32 synthetic questions, eight each for Chinese, versions,
paths and error codes, with four documents per question. Document zero is the
single labeled answer. Four additional questions have no answer in the corpus.
Labels and corpus bytes must be frozen before comparing variants.

Run `uv run --script benchmarks/retrieval/run_benchmark.py --help` for required
URLs and file arguments. The owner-only key file contains one `P0_API_KEY=...`
assignment. MCP uses Bearer authentication; Sidecar bootstrap uses X-API-Key.
`--seed` requires both scopes to be empty. It writes a placeholder with
`infer=false`, then changes its text through the update API to exercise the
normal entity-linking path without LLM extraction. Mutations are not retried.

The four groups are baseline, NLP, reranker and combined. Searches use
`top_k=20`, `threshold=0.1`, `explain=true` and an explicit rerank boolean. The
bridge overfetches 60; Core retrieves up to 240 semantic candidates, scores and
retains 60, optionally reranks those 60, then the bridge returns at most 20.
This budget is identical across groups. A smaller requested `top_k` changes the
candidate budget and is a different experiment.

One warmup per group is excluded. Each full repetition rotates the group order.
Repeated queries improve the latency sample; they do not create more than 32
independent quality cases. Raw HTTP responses are retained in `raw.jsonl` before
validation. Tool errors, unknown/missing labels and missing/all-zero reranker
scores stop the run rather than being counted as a successful search.

`summary.json` records the corpus hash, parameters, per-category and overall
Recall@1/3/5, MRR and end-to-end mean/p50/p95 latency. With one labeled answer,
Recall@k is the fraction whose labeled document appears in the first k hits.
Negative nonempty rate is reported separately: it describes retrieval results,
not an assistant's final answer or refusal behavior. Hybrid `threshold` is
applied before reranking and is not a calibrated abstention threshold.

`test_benchmark.py` checks metric arithmetic, failure handling and the HTTP wire
contract. New-index synthetic results do not establish gains for historical
records, unrelated corpora, concurrent traffic or another reranker model.
