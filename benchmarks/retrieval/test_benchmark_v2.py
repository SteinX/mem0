import json
import sys
from pathlib import Path

import analyze_benchmark_v2
import httpx
import pytest
import run_benchmark_v2


@pytest.mark.parametrize("url", ["http://fixture", "http://fixture/", "http://fixture///"])
def test_seed_accepts_base_url_trailing_slashes(url: str) -> None:
    corpus = run_benchmark_v2.Corpus.model_validate({
        "cases": [{"id": "q", "category": "path", "query": "config/app.yaml", "split": "dev",
                   "origin": "fixture", "documents": ["answer", "other1", "other2", "other3"]}],
        "negative_queries": [],
    })
    posted = []

    def wire(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/memories":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"id": str(len(posted))}]})
        if request.url.path == "/v1/memories/scan":
            return httpx.Response(200, json={"total": len(posted)})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(wire)) as client:
        run_benchmark_v2.seed(client, url, corpus)
    assert {row["metadata"]["fixture_id"] for row in posted} == {f"q-{i}" for i in range(4)}
    assert all(row["infer"] is False for row in posted)


@pytest.mark.parametrize("seed", [False, True])
def test_empty_negatives_and_warmups_are_scored_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: bool,
) -> None:
    (tmp_path / "key.env").write_text("P0_API_KEY=fixture-key\n")
    (tmp_path / "corpus.json").write_text(json.dumps({
        "cases": [{"id": "q", "category": "path", "query": "/srv/config.yaml", "split": "test",
                   "origin": "fixture", "documents": ["answer", "other1", "other2", "other3"]}],
        "negative_queries": [{"id": "negative", "category": "negative", "query": "birthday", "split": "test"}],
    }))
    (tmp_path / "groups.json").write_text(json.dumps([
        {"name": name, "mcp_url": "http://fixture/mcp",
         "sidecar_url": "http://fixture/" if rerank else "http://fixture", "rerank": rerank}
        for name, rerank in (("plain", False), ("rerank", True))
    ]))

    seeded = []

    def wire(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/memories":
            seeded.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"id": str(len(seeded))}]})
        if request.url.path == "/v1/memories/scan":
            return httpx.Response(200, json={"total": len(seeded)})
        assert request.headers["authorization"] == "Bearer fixture-key"
        arguments = json.loads(request.content)["params"]["arguments"]
        hits = [] if arguments["query"] == "birthday" else [{
            "metadata": {"fixture_id": "q-0"}, "score_details": {}, "rerank_score": 0.9,
        }]
        return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": json.dumps({"results": hits})}]}})

    transport = httpx.MockTransport(wire)
    monkeypatch.setattr(httpx.Client, "_transport_for_url", lambda self, url: transport)
    monkeypatch.setattr(sys, "argv", [
        "run_benchmark_v2.py", "--groups-file", str(tmp_path / "groups.json"),
        "--key-file", str(tmp_path / "key.env"), "--corpus", str(tmp_path / "corpus.json"),
        "--output", str(tmp_path / "out"), "--split", "test",
        *(["--seed"] if seed else []),
    ])
    run_benchmark_v2.main()
    summary = json.loads((tmp_path / "out/summary.json").read_text())
    for group in summary["groups"].values():
        assert group["overall"]["requests"] == 1
        assert group["overall"]["recall@1"] == 1
        assert group["negative"]["requests"] == 1
        assert group["negative"]["nonempty_rate"] == 0
    raw = (tmp_path / "out/raw.jsonl").read_text()
    assert len(raw.splitlines()) == 6
    assert "fixture-key" not in raw
    assert len(seeded) == (4 if seed else 0)


@pytest.mark.parametrize(("custom_groups", "dev_only"), [(False, False), (True, False), (True, True)])
def test_analysis_separates_splits_counts_and_paired_latency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, custom_groups: bool, dev_only: bool,
) -> None:
    from hashlib import sha256

    corpus = {
        "cases": [
            {"id": name, "category": "path", "query": name, "split": split,
             "origin": "fixture", "documents": ["answer", "other1", "other2", "other3"]}
            for name, split in (("d", "dev"), ("t", "test"))
        ],
        "negative_queries": [
            {"id": "n" + split, "category": "negative", "query": "unknown", "split": split}
            for split in ("dev", "test")
        ],
    }
    data = json.dumps(corpus).encode()
    (tmp_path / "corpus.json").write_bytes(data)
    (tmp_path / "summary.json").write_text(json.dumps({"corpus_sha256": sha256(data).hexdigest()}))
    rows = []
    group_names = {
        name: f"group{index}" if custom_groups and index else name
        for index, name in enumerate(("baseline", "baseline_rerank", "candidate", "candidate_rerank"))
    }
    for group, elapsed in (("baseline", 10), ("baseline_rerank", 30), ("candidate", 12), ("candidate_rerank", 20)):
        for repetition in (-1, 0, 1):
            for case, split, category in (("d", "dev", "path"), ("t", "test", "path"),
                                          ("ndev", "dev", "negative"), ("ntest", "test", "negative")):
                if repetition == -1 and case != "d":
                    continue
                if dev_only and split != "dev":
                    continue
                labels = ["d-1"] if category == "negative" else [case + "-0"]
                if category == "negative" and group.startswith("candidate"):
                    labels = []
                if group == "baseline_rerank" and case == "t" and repetition == 0:
                    labels.insert(0, "t-1")
                hits = [{"metadata": {"fixture_id": label}, "score_details": {}, "memory": label,
                         "score": 0.8, "rerank_score": 0.9} for label in labels]
                body = {"result": {"content": [{"type": "text", "text": json.dumps({"results": hits})}]}}
                rows.append({"group": group_names[group], "case_id": case, "category": category, "split": split,
                             "round": repetition, "warmup": repetition == -1, "latency_ms": elapsed,
                             "status": 200, "response": json.dumps(body)})
    (tmp_path / "raw.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(sys, "argv", ["analyze_benchmark_v2.py", str(tmp_path), str(tmp_path / "corpus.json")])
    analyze_benchmark_v2.main()
    result = json.loads((tmp_path / "analysis.json").read_text())
    assert result["measured_requests"] == (16 if dev_only else 32)
    assert len(result["warmups_excluded"]) == 4
    test = result["splits"]["dev" if dev_only else "test"]
    assert test[group_names["candidate_rerank"]]["positive"]["recall@1"] == 1
    assert test[group_names["candidate_rerank"]]["independent_positive_queries"] == 1
    assert test[group_names["candidate_rerank"]]["negative_nonempty_rate"] == 0
    assert test[group_names["baseline_rerank"]]["positive"]["recall@1"] == (1 if dev_only else 0.5)
    assert len(result["changed_answer_ranks_between_repetitions"]) == (0 if dev_only else 1)
    if dev_only:
        assert "test" not in result["splits"]
    if custom_groups:
        assert result["paired_latency_differences_ms"] == {}
    else:
        assert result["paired_latency_differences_ms"]["baseline_rerank -> candidate_rerank"]["p95"] == -10
