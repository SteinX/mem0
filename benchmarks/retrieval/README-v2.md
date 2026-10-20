# Retrieval quality v2

The frozen corpus has 128 positive questions, 32 unanswered questions and 512
synthetic facts: 32 positives each for paths, versions, Chinese rules and errors.
It retains the 32 P0 questions except that err-05 was narrowed to EACCES alone to
remove a two-answer ambiguity. Newly authored facts cover adjacent filenames,
path suffixes, Windows paths, SemVer prereleases/builds, current/historical
versions and operational rules.

64 positive/16 negative queries form development data; the remaining 64/16 are
held out until settings are frozen. Positive subjects and negative query
identifiers are held out, while some negative project names are shared.
Templates are shared too. Results establish synthetic regression
behavior, not generalization to real-user conversations.

The measured comparison is documented in [RESULTS-v2.md](RESULTS-v2.md).
[results-v2.json](results-v2.json) contains frozen development settings, source
hashes and per-split/per-round metrics; raw responses stay in the operator workspace.

Run build_corpus_v2.py to reproduce the frozen JSON; it refuses different bytes
once that file exists. All 512 facts are seeded in shuffled order in each isolated
stack using infer=false, without LLM extraction. Labels stay in metadata.

run_benchmark_v2.py reuses v1's MCP decoder, metric arithmetic and error handling.
Provide a JSON array of groups with name, mcp_url, sidecar_url and rerank, plus a
restricted key file. Example:

    uv run --script benchmarks/retrieval/run_benchmark_v2.py \
      --groups-file GROUPS.json --key-file KEY.env \
      --corpus benchmarks/retrieval/corpus-v2.json --output RESULTS \
      --split dev --repetitions 1 --seed

Seed requires empty benchmark scopes. Do not use production URLs. The first query
per group is warmup and remains in raw.jsonl but is excluded from metrics. Group
order rotates per query/repetition. HTTP/tool errors, unknown labels and missing
or all-zero rerank scores stop evaluation. Empty results are valid, including
intentional score-gated negatives.

Report positive Recall@1/3/5 and MRR separately from negative nonempty rate and
answerable-query empty rate. Repetitions add latency samples, not independent
quality examples. End-to-end timings include embedding, database, auth, Sidecar
and MCP, and are compared under identical 4 CPU/6GiB Core limits and shared model
service. Warm p50/p95 and recorded warmups are not an isolated cold-start profile.
