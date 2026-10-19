#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.28,<1", "pydantic>=2.7,<3"]
# ///
# How to run: install uv with https://astral.sh/uv/install.sh, then
# uv run --script benchmarks/retrieval/run_benchmark.py --help
from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from getopt import getopt
from hashlib import sha256
from math import floor
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue

JsonObject = dict[str, JsonValue]
SCOPE_ID: Final = "p0-eval"
SCOPE: Final = dict.fromkeys(("project_id", "app_id", "user_id"), SCOPE_ID)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class Case(FrozenModel):
    id: str
    category: Literal["chinese", "version", "path", "error_code"]
    query: str
    documents: tuple[str, str, str, str]


class Corpus(FrozenModel):
    cases: tuple[Case, ...] = Field(min_length=1)
    negative_queries: tuple[str, str, str, str]


class Options(FrozenModel):
    baseline_mcp_url: str
    nlp_mcp_url: str
    baseline_sidecar_url: str
    nlp_sidecar_url: str
    key_file: Path
    corpus: Path
    output: Path
    repetitions: int = Field(default=3, ge=1)
    seed: bool = False


class Labels(FrozenModel):
    fixture_id: str = Field(min_length=1)


class Hit(FrozenModel):
    metadata: Labels
    score_details: dict[str, JsonValue]
    rerank_score: FiniteFloat | None = None


class SearchResult(FrozenModel):
    results: list[Hit]


class Content(FrozenModel):
    type: Literal["text"]
    text: str


class ToolResult(FrozenModel):
    isError: bool = False
    content: tuple[Content, ...] = Field(min_length=1)


class Rpc(FrozenModel):
    result: ToolResult | None = None
    error: JsonValue = None


class Count(FrozenModel):
    total: int = Field(ge=0)


class Created(FrozenModel):
    id: str = Field(min_length=1)


class SeedResult(FrozenModel):
    results: tuple[Created]


@dataclass(frozen=True, slots=True)
class BenchmarkError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class Sample:
    rank: int | None
    latency_ms: float
    nonempty: bool


@dataclass(frozen=True, slots=True)
class Group:
    name: str
    url: str
    nlp: bool
    rerank: bool


@dataclass(frozen=True, slots=True)
class Query:
    id: str
    category: str
    text: str


def parse_rpc(raw: bytes, *, rerank: bool) -> list[str]:
    rpc = Rpc.model_validate_json(raw)
    if rpc.error is not None or rpc.result is None or rpc.result.isError:
        raise BenchmarkError("MCP returned a JSON-RPC or tool error")
    hits = SearchResult.model_validate_json(rpc.result.content[0].text).results
    if (
        rerank
        and hits
        and (any(hit.rerank_score is None for hit in hits) or not any(hit.rerank_score != 0 for hit in hits))
    ):
        raise BenchmarkError("rerank=true returned missing or entirely zero rerank scores")
    return [hit.metadata.fixture_id for hit in hits]


def latency(samples: Sequence[Sample]) -> JsonObject:
    values = sorted(sample.latency_ms for sample in samples)

    def percentile(fraction: float) -> float:
        position = (len(values) - 1) * fraction
        lower = floor(position)
        return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (position - lower)

    return {"p50": percentile(0.5), "p95": percentile(0.95), "mean": mean(values)}


def metrics(samples: list[Sample]) -> JsonObject:
    return {
        "requests": len(samples),
        **{f"recall@{k}": mean(sample.rank is not None and sample.rank <= k for sample in samples) for k in (1, 3, 5)},
        "mrr": mean(1 / sample.rank if sample.rank else 0 for sample in samples),
        "latency_ms": latency(samples),
    }


def count_scope(client: httpx.Client, url: str) -> int:
    body = dict(project_id=SCOPE_ID, app_id=SCOPE_ID, mode="count", filters={"user_id": SCOPE_ID})
    response = client.post(f"{url.rstrip('/')}/v1/memories/scan", json=body).raise_for_status()
    return Count.model_validate_json(response.content).total


def seed_corpus(client: httpx.Client, url: str, corpus: Corpus) -> None:
    for case in corpus.cases:
        for index, document in enumerate(case.documents):
            fixture_id = f"{case.id}-{index}"
            body = {
                **SCOPE,
                "infer": False,
                "messages": [{"role": "user", "content": f"P0 seed placeholder {fixture_id}"}],
                "metadata": {"fixture_id": fixture_id},
            }
            response = client.post(f"{url.rstrip('/')}/memories", json=body).raise_for_status()
            memory_id = SeedResult.model_validate_json(response.content).results[0].id
            client.put(
                f"{url.rstrip('/')}/memories/{memory_id}",
                params={"project_id": SCOPE_ID, "app_id": SCOPE_ID},
                json={"text": document, "metadata": {"fixture_id": fixture_id}},
            ).raise_for_status()
        print(f"seeded {url}: {case.id}", flush=True)


def main() -> None:
    flags = [name.replace("_", "-") + "=" for name in Options.model_fields if name != "seed"]
    parsed_flags, remaining = getopt(sys.argv[1:], "", [*flags, "seed", "help"])
    if ("--help", "") in parsed_flags:
        print("Usage: uv run --script run_benchmark.py", *(f"--{flag[:-1]} VALUE" for flag in flags), "[--seed]")
        print("repetitions defaults to 3; output is a new result directory.")
        return
    if remaining:
        raise BenchmarkError("Unexpected positional CLI arguments")
    opts = Options.model_validate(
        {name[2:].replace("-", "_"): True if name == "--seed" else value for name, value in parsed_flags}
    )
    name, _, key = opts.key_file.read_text().strip().partition("=")
    if name != "P0_API_KEY" or not key or "\n" in key:
        raise BenchmarkError("key-file must contain exactly one nonempty P0_API_KEY assignment")
    corpus_bytes = opts.corpus.read_bytes()
    corpus = Corpus.model_validate_json(corpus_bytes)
    groups = (
        Group("baseline", opts.baseline_mcp_url, False, False),
        Group("nlp", opts.nlp_mcp_url, True, False),
        Group("reranker", opts.baseline_mcp_url, False, True),
        Group("combined", opts.nlp_mcp_url, True, True),
    )
    queries = [Query(case.id, case.category, case.query) for case in corpus.cases]
    queries += [Query(f"negative-{index}", "negative", text) for index, text in enumerate(corpus.negative_queries)]
    fixtures = {f"{case.id}-{index}" for case in corpus.cases for index in range(4)}
    sidecars = (opts.baseline_sidecar_url, opts.nlp_sidecar_url)
    if len(fixtures) != len(corpus.cases) * 4 or len(set(sidecars)) != 2 or opts.baseline_mcp_url == opts.nlp_mcp_url:
        raise BenchmarkError("Case IDs and the two independent service URLs must be distinct")
    observations: dict[str, dict[str, list[Sample]]] = {group.name: {} for group in groups}
    opts.output.mkdir(parents=True, exist_ok=True)
    with (
        httpx.Client(
            headers={"Authorization": f"Bearer {key}"},
            timeout=180,
            follow_redirects=False,
            trust_env=False,
        ) as client,
        (opts.output / "raw.jsonl").open("x") as raw,
    ):
        if opts.seed:
            with httpx.Client(headers={"X-API-Key": key}, timeout=180, trust_env=False) as sidecar:
                if any(count_scope(sidecar, url) != 0 for url in sidecars):
                    raise BenchmarkError("Seed refused: benchmark scope is already populated")
                for url in sidecars:
                    seed_corpus(sidecar, url, corpus)
        for round_index in range(-1, opts.repetitions):
            for query in queries[:1] if round_index == -1 else queries:
                for group in groups[round_index % 4 :] + groups[: round_index % 4]:
                    arguments = dict(
                        SCOPE, query=query.text, top_k=20, threshold=0.1, explain=True, rerank=group.rerank
                    )
                    request = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "search_memories", "arguments": arguments},
                    }
                    start = perf_counter()
                    response = client.post(group.url, json=request)
                    elapsed = (perf_counter() - start) * 1000
                    record = dict(
                        group=group.name,
                        case_id=query.id,
                        category=query.category,
                        round=round_index,
                        warmup=round_index == -1,
                        latency_ms=elapsed,
                        status=response.status_code,
                        request=request,
                        response=response.text,
                    )
                    raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                    raw.flush()
                    response.raise_for_status()
                    labels = parse_rpc(response.content, rerank=group.rerank)
                    if not set(labels).issubset(fixtures):
                        raise BenchmarkError("Search returned an unknown fixture label")
                    if round_index >= 0:
                        relevant = f"{query.id}-0"
                        rank = labels.index(relevant) + 1 if relevant in labels else None
                        observations[group.name].setdefault(query.category, []).append(
                            Sample(rank, elapsed, bool(labels))
                        )
            print(f"completed round {round_index}", flush=True)
    results: JsonObject = {}
    for group in groups:
        categories = observations[group.name]
        positive = [sample for category, samples in categories.items() if category != "negative" for sample in samples]
        negative = categories["negative"]
        if group.rerank and not any(sample.nonempty for sample in positive):
            raise BenchmarkError("No nonempty reranked positive request was observed")
        results[group.name] = {
            "nlp": group.nlp,
            "rerank": group.rerank,
            "overall": metrics(positive),
            "categories": {
                category: metrics(samples) for category, samples in categories.items() if category != "negative"
            },
            "negative": {
                "requests": len(negative),
                "nonempty_rate": mean(sample.nonempty for sample in negative),
                "latency_ms": latency(negative),
            },
        }
    summary = {
        "corpus_sha256": sha256(corpus_bytes).hexdigest(),
        "parameters": {
            **opts.model_dump(mode="json", exclude={"key_file"}),
            "scope": SCOPE,
            "top_k": 20,
            "threshold": 0.1,
            "explain": True,
            "warmup_requests_per_group": 1,
            "percentile_method": "inclusive linear interpolation",
        },
        "groups": results,
    }
    (opts.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
