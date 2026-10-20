#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.28,<1", "pydantic>=2.7,<3"]
# ///
# Run: uv run --script benchmarks/retrieval/analyze_benchmark_v2.py RESULTS CORPUS
from __future__ import annotations

import json
import sys
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import FiniteFloat
from run_benchmark import (
    BenchmarkError,
    FrozenModel,
    Hit,
    JsonObject,
    Rpc,
    Sample,
    latency,
    metrics,
)
from run_benchmark_v2 import Corpus


class Record(FrozenModel):
    group: str
    case_id: str
    category: str
    split: Literal["dev", "test"]
    round: int
    warmup: bool
    latency_ms: FiniteFloat
    status: int
    response: str


class DetailedHit(Hit):
    memory: str
    score: FiniteFloat


class SearchResult(FrozenModel):
    results: tuple[DetailedHit, ...]


class Observation(FrozenModel):
    record: Record
    hits: tuple[DetailedHit, ...]

    @property
    def sample(self) -> Sample:
        labels = [hit.metadata.fixture_id for hit in self.hits]
        target = self.record.case_id + "-0"
        rank = labels.index(target) + 1 if target in labels else None
        return Sample(rank, self.record.latency_ms, bool(labels))


def summarize(rows: list[Observation]) -> JsonObject:
    positives = [row.sample for row in rows if row.record.category != "negative"]
    negatives = [row.sample for row in rows if row.record.category == "negative"]
    categories = sorted({row.record.category for row in rows} - {"negative"})
    return {
        "positive": metrics(positives),
        "answerable_empty_rate": mean(not sample.nonempty for sample in positives),
        "negative_nonempty_rate": mean(sample.nonempty for sample in negatives),
        "independent_positive_queries": len({row.record.case_id for row in rows if row.record.category != "negative"}),
        "independent_negative_queries": len({row.record.case_id for row in rows if row.record.category == "negative"}),
        "categories": {
            category: metrics([row.sample for row in rows if row.record.category == category])
            for category in categories
        },
        "all_latency_ms": latency([row.sample for row in rows]),
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Expected completed RESULTS directory and CORPUS.json")
    root, corpus_path = map(Path, sys.argv[1:])
    completed = json.loads((root / "summary.json").read_text())
    corpus_bytes = corpus_path.read_bytes()
    if sha256(corpus_bytes).hexdigest() != completed["corpus_sha256"]:
        raise BenchmarkError("Analysis corpus hash does not match the completed run")
    corpus = Corpus.model_validate_json(corpus_bytes)
    queries = {q.id: q.query for q in (*corpus.cases, *corpus.negative_queries)}
    observations = []
    warmups = []
    for line in (root / "raw.jsonl").read_text().splitlines():
        record = Record.model_validate_json(line)
        if record.status != 200:
            raise BenchmarkError("Analysis encountered a failed HTTP request")
        rpc = Rpc.model_validate_json(record.response)
        if rpc.error is not None or rpc.result is None or rpc.result.isError:
            raise BenchmarkError("Analysis encountered a failed MCP result")
        hits = SearchResult.model_validate_json(rpc.result.content[0].text).results
        row = Observation(record=record, hits=hits)
        if record.warmup:
            warmups.append({"group": record.group, "latency_ms": record.latency_ms})
        else:
            observations.append(row)
    groups = sorted({row.record.group for row in observations})
    split_results = {
        split: {
            group: summarize([
                row for row in observations
                if row.record.group == group and (split == "all" or row.record.split == split)
            ]) for group in groups
        } for split in (*sorted({row.record.split for row in observations}), "all")
    }
    repeated = defaultdict(list)
    for row in observations:
        repeated[(row.record.group, row.record.case_id)].append(
            (row.sample.rank, tuple(hit.metadata.fixture_id for hit in row.hits))
        )
    changed_ranks = [
        {"group": group, "case_id": case, "ranks": [rank for rank, _ in values]}
        for (group, case), values in repeated.items() if len({rank for rank, _ in values}) > 1
    ]
    failures = [
        {"group": row.record.group, "case_id": row.record.case_id, "split": row.record.split,
         "query": queries[row.record.case_id], "answer_rank": row.sample.rank,
         "top": [hit.model_dump() for hit in row.hits[:3]]}
        for row in observations
        if row.record.round == 0 and (
            (row.record.category == "negative" and row.hits)
            or (row.record.category != "negative" and row.sample.rank != 1)
        )
    ]
    paired = {}
    by_request = {(row.record.group, row.record.case_id, row.record.round): row for row in observations}
    for left, right in (("baseline", "baseline_rerank"), ("candidate", "candidate_rerank"),
                        ("baseline_rerank", "candidate_rerank")):
        if left not in groups or right not in groups:
            continue
        deltas = []
        for (group, case, repetition), row in by_request.items():
            if group == left:
                other = by_request[(right, case, repetition)]
                deltas.append(Sample(None, other.record.latency_ms - row.record.latency_ms, False))
        paired[left + " -> " + right] = latency(deltas)
    analysis = {
        "corpus_sha256": completed["corpus_sha256"], "measured_requests": len(observations),
        "warmups_excluded": warmups, "splits": split_results,
        "changed_answer_ranks_between_repetitions": changed_ranks,
        "paired_latency_differences_ms": paired,
        "rounds": {
            str(repetition): {group: summarize([
                row for row in observations if row.record.group == group and row.record.round == repetition
            ]) for group in groups}
            for repetition in sorted({row.record.round for row in observations})
        },
    }
    (root / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n")
    (root / "failure-cases.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"requests": len(observations), "changed_answer_ranks": len(changed_ranks),
                      "test": split_results.get("test", {})}, ensure_ascii=False))


if __name__ == "__main__":
    main()
