# Self-hosted NLP and reranking

`retrieval.Dockerfile` builds an optional CPU retrieval image from an existing
Core image. It adds spaCy 3.8.11 with `en_core_web_sm` 3.8.0 and the official
HuggingFace reranker provider, using CPU PyTorch 2.14.1 and Transformers 5.20.1.

The model is `BAAI/bge-reranker-base`, pinned to revision
`2cfc18c9415c912f9d8155881c133215df768a70`. Its weights are approximately 1.1 GB.
The image downloads the model at build time and sets `HF_HUB_OFFLINE=1` at runtime.

Build from the repository root, choosing the exact existing Core image:

```sh
docker build --target runtime --build-arg CORE_IMAGE=<pinned-core-image> \
  -f server/retrieval.Dockerfile -t mem0-core:retrieval .
```

Configure the existing Core `/configure` endpoint with this additional section:

```json
{
  "reranker": {
    "provider": "huggingface",
    "config": {
      "model": "/opt/models/bge-reranker-base",
      "device": "cpu",
      "batch_size": 8,
      "max_length": 512,
      "normalize": true
    }
  }
}
```

Reranking remains opt-in per search. REST and the MCP `search_memories` tool
accept `rerank: true` or `false`. Omitting it preserves the SDK default. REST
returns 503 if reranking is requested without a configured provider. Use
`explain: true` to inspect semantic, keyword and entity scores; active reranking
adds a separate `rerank_score`. A normal response without actual rerank scores
does not prove reranking worked.

The default NLP model is English. The reranker supports Chinese and English,
but Chinese entity extraction and PostgreSQL tokenization still require their
own quality checks. Existing memories are not automatically reindexed when NLP
is installed. The SDK reranks the retrieved candidates; retrieve a consistent
candidate budget when comparing Recall@k or MRR.

`MEM0_NLP_ENABLED=false` disables both entity extraction and lemmatization for
the process, retaining the original-text fallback. Omission or `true` keeps
NLP enabled. Set the variable before starting Core and recreate the process
after changing it. This permits a measured rollout without rebuilding the image
or rewriting existing memories. The initial Chinese/identifier evaluation found
NLP regressions, so the local production rollout explicitly disables it.

The fixed synthetic evaluation corpus and HTTP runner live in
`benchmarks/retrieval/`. Its baseline disables NLP only in isolated benchmark
processes. It never changes production NLP settings or existing memory records.
