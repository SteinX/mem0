# Synthetic retrieval comparison, 2026-10-18

This fixed experiment contains 32 positive questions, eight each for Chinese,
versions, paths and error codes, 128 documents and four no-answer questions.
All documents are synthetic. The corpus was frozen before model evaluation:
SHA-256 `39b8359662e8ecfe3b868faa28f23be7252c8b07d0976da4e6e3b4a09d675d72`.

Three repetitions produced identical gold ranks: 432 measured requests plus four
excluded warmups, all successful. Repetition increases the latency sample rather
than the number of independent quality cases. See README.md for the common
candidate budget and measurement method.

| Variant | Recall@1 | Recall@3 | Recall@5 | MRR | p50 (s) | p95 (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 81.25% | 100.00% | 100.00% | 0.9010 | 4.03 | 7.10 |
| NLP | 68.75% | 90.62% | 90.62% | 0.8039 | 0.81 | 3.61 |
| Reranker | 87.50% | 96.88% | 100.00% | 0.9297 | 5.05 | 16.75 |
| NLP + reranker | 87.50% | 100.00% | 100.00% | 0.9323 | 8.99 | 17.70 |

| Category, Recall@1 | Baseline | NLP | Reranker | Combined |
| --- | ---: | ---: | ---: | ---: |
| Chinese | 87.5% | 75.0% | 87.5% | 87.5% |
| Versions | 87.5% | 100.0% | 87.5% | 87.5% |
| Paths | 87.5% | 50.0% | 75.0% | 75.0% |
| Error codes | 62.5% | 50.0% | 100.0% | 100.0% |

Each Core had four CPUs and a 6 GiB memory limit; both shared the same bge-m3
embedding service. Reranking used the pinned bge-reranker-base CPU model described
in server/RETRIEVAL.md. Latency includes the whole MCP/Sidecar/Core path, network,
authentication, embedding and storage. The shared host and changing candidates
prevent attributing latency differences to individual stages. No concurrency
load test was performed. The longest rerank request took 34.19 seconds.

Reranking improved error-code ranking but still confused another user's language
preference with the requested user's preference, historical with current versions,
and a repository-root .env with deploy/.env. It also preferred a backup entrypoint
over the active path. Default English NLP degraded several path queries through
entity boosts. This evidence supports opt-in reranking and a process switch for
NLP rather than enabling both indiscriminately.

Every no-answer query returned nonempty results in every group. This is retrieval
behavior, not a measurement of an assistant's final answer. Hybrid thresholding
is applied before reranking and does not establish a calibrated refusal threshold.

An independent English morphology control matched "Engineers deployed containers"
with "engineer deploy container": baseline BM25 score was zero and the NLP score
was 0.01567. This confirms the lexical path works without establishing Chinese
quality. New-index synthetic results do not prove improvements for historical
records, other corpora, another model or concurrent production traffic.
