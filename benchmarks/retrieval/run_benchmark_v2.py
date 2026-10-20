#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.28,<1", "pydantic>=2.7,<3"]
# ///
# Run: uv run --script benchmarks/retrieval/run_benchmark_v2.py --help
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from getopt import getopt
from hashlib import sha256
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Literal

import httpx
from pydantic import Field
from run_benchmark import (
    SCOPE,
    BenchmarkError,
    Case,
    FrozenModel,
    Sample,
    SeedResult,
    count_scope,
    latency,
    metrics,
    parse_rpc,
)


class V2Case(Case):
    split: Literal["dev", "test"]
    origin: str


class Negative(FrozenModel):
    id: str
    query: str
    category: Literal["negative"]
    split: Literal["dev", "test"]


class Corpus(FrozenModel):
    cases: tuple[V2Case, ...]
    negative_queries: tuple[Negative, ...]


class Group(FrozenModel):
    name: str
    mcp_url: str
    sidecar_url: str
    rerank: bool


class Options(FrozenModel):
    groups_file: Path
    key_file: Path
    corpus: Path
    output: Path
    split: Literal["dev", "test", "all"] = "all"
    repetitions: int = Field(default=1, ge=1)
    seed: bool = False


def seed(client: httpx.Client, url: str, corpus: Corpus) -> None:
    fixtures = [
        (f"{case.id}-{index}", text)
        for case in corpus.cases for index, text in enumerate(case.documents)
    ]
    random.Random(20261020).shuffle(fixtures)
    for number, (fixture_id, text) in enumerate(fixtures, 1):
        response = client.post(f"{url}/memories", json={
            **SCOPE, "infer": False, "messages": [{"role": "user", "content": text}],
            "metadata": {"fixture_id": fixture_id},
        }).raise_for_status()
        SeedResult.model_validate_json(response.content)
        if number % 32 == 0:
            print(json.dumps({"seed_url": url, "documents": number}), flush=True)
    if count_scope(client, url) != len(fixtures):
        raise BenchmarkError("Seed count does not match the frozen corpus")


def main() -> None:
    flags = [name.replace("_", "-") + "=" for name in Options.model_fields if name != "seed"]
    parsed, remaining = getopt(sys.argv[1:], "", [*flags, "seed", "help"])
    if ("--help", "") in parsed:
        print("Usage: run_benchmark_v2.py", *(f"--{name[:-1]} VALUE" for name in flags), "[--seed]")
        return
    if remaining:
        raise BenchmarkError("Unexpected positional CLI arguments")
    opts = Options.model_validate({
        name[2:].replace("-", "_"): True if name == "--seed" else value for name, value in parsed
    })
    groups = tuple(Group.model_validate(row) for row in json.loads(opts.groups_file.read_text()))
    if not groups or len({group.name for group in groups}) != len(groups):
        raise BenchmarkError("Groups must have distinct names")
    name, _, key = opts.key_file.read_text().strip().partition("=")
    if name != "P0_API_KEY" or not key or "\n" in key:
        raise BenchmarkError("Expected one P0_API_KEY assignment")
    corpus_bytes = opts.corpus.read_bytes()
    corpus = Corpus.model_validate_json(corpus_bytes)
    queries = [*corpus.cases, *corpus.negative_queries]
    queries = [row for row in queries if opts.split == "all" or row.split == opts.split]
    fixtures = {f"{case.id}-{index}" for case in corpus.cases for index in range(4)}
    observations = defaultdict(lambda: defaultdict(list))
    opts.output.mkdir(parents=True, exist_ok=True)
    with (
        httpx.Client(headers={"Authorization": "Bearer " + key}, timeout=180, trust_env=False) as client,
        (opts.output / "raw.jsonl").open("x") as raw,
    ):
        if opts.seed:
            urls = sorted({group.sidecar_url for group in groups})
            with httpx.Client(headers={"X-API-Key": key}, timeout=180, trust_env=False) as writer:
                if any(count_scope(writer, url) for url in urls):
                    raise BenchmarkError("Seed refused: an isolated scope is already populated")
                for url in urls:
                    seed(writer, url, corpus)
        for repetition in range(-1, opts.repetitions):
            for index, query in enumerate(queries[:1] if repetition == -1 else queries):
                offset = (repetition + index) % len(groups)
                for group in groups[offset:] + groups[:offset]:
                    request = {
                        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                            "name": "search_memories", "arguments": {
                                **SCOPE, "query": query.query, "top_k": 20,
                                "threshold": 0.1, "explain": True, "rerank": group.rerank,
                            },
                        },
                    }
                    start = perf_counter()
                    response = client.post(group.mcp_url, json=request)
                    elapsed = (perf_counter() - start) * 1000
                    raw.write(json.dumps({
                        "group": group.name, "case_id": query.id, "category": query.category,
                        "split": query.split, "round": repetition, "warmup": repetition == -1,
                        "latency_ms": elapsed, "status": response.status_code,
                        "request": request, "response": response.text,
                    }, ensure_ascii=False) + "\n")
                    raw.flush()
                    response.raise_for_status()
                    hits = parse_rpc(response.content, rerank=group.rerank)
                    if set(hits) - fixtures:
                        raise BenchmarkError("Response contains unknown fixture labels")
                    target = query.id + "-0"
                    rank = hits.index(target) + 1 if target in hits else None
                    if repetition >= 0:
                        observations[group.name][query.category].append(Sample(rank, elapsed, bool(hits)))
                if repetition >= 0 and index % 8 == 0:
                    print(json.dumps({"round": repetition, "completed_queries": index + 1,
                                      "total_queries": len(queries)}), flush=True)
    results = {}
    for group in groups:
        categories = observations[group.name]
        positive = [sample for category, samples in categories.items() if category != "negative" for sample in samples]
        negatives = categories.get("negative", [])
        results[group.name] = {
            "overall": metrics(positive),
            "answerable_empty_rate": mean(not sample.nonempty for sample in positive),
            "categories": {category: metrics(samples) for category, samples in categories.items() if category != "negative"},
            "negative": {"requests": len(negatives), "nonempty_rate": mean(s.nonempty for s in negatives),
                         "latency_ms": latency(negatives)},
            "all_query_latency_ms": latency([*positive, *negatives]),
        }
    summary = {
        "corpus_sha256": sha256(corpus_bytes).hexdigest(), "split": opts.split,
        "repetitions": opts.repetitions, "independent_positive_cases": sum(q.category != "negative" for q in queries),
        "independent_negative_cases": sum(q.category == "negative" for q in queries),
        "parameters": {"top_k": 20, "threshold": 0.1, "explain": True, "nlp": False},
        "group_definitions": [g.model_dump() for g in groups], "groups": results,
    }
    (opts.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"complete": str(opts.output), "groups": list(results)}), flush=True)


if __name__ == "__main__":
    main()
