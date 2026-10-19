import json
from pathlib import Path

import httpx
import pytest
import run_benchmark
from pydantic import ValidationError
from run_benchmark import BenchmarkError, JsonObject, Sample, metrics, parse_rpc


def test_metrics_use_known_rank_and_inclusive_latency_percentiles() -> None:
    # Given three fixed retrieval outcomes, when their metrics are summarized.
    summary = metrics([Sample(1, 10.0, True), Sample(None, 20.0, False), Sample(3, 30.0, True)])

    # Then missing labels count as misses, and latency uses inclusive interpolation.
    assert summary == {
        "requests": 3,
        "recall@1": pytest.approx(1 / 3),
        "recall@3": pytest.approx(2 / 3),
        "recall@5": pytest.approx(2 / 3),
        "mrr": pytest.approx(4 / 9),
        "latency_ms": {"p50": 20.0, "p95": 29.0, "mean": 20.0},
    }


@pytest.mark.parametrize(
    ("result", "rerank"),
    [
        ({"isError": True, "content": [{"type": "text", "text": "backend failed"}]}, False),
        ({"content": [{"type": "text", "text": '{"results":[{"metadata":{},"score_details":{}}]}'}]}, False),
        ({"content": [{"type": "text", "text": '{"results":[{"metadata":{"fixture_id":"case-0"}}]}'}]}, False),
        (
            {
                "content": [
                    {"type": "text", "text": '{"results":[{"metadata":{"fixture_id":"case-0"},"score_details":{}}]}'}
                ]
            },
            True,
        ),
        (
            {
                "content": [
                    {
                        "type": "text",
                        "text": '{"results":[{"metadata":{"fixture_id":"case-0"},'
                        '"score_details":{},"rerank_score":0}]}',
                    }
                ]
            },
            True,
        ),
    ],
)
def test_rpc_rejects_errors_missing_labels_or_inactive_reranking(result: JsonObject, rerank: bool) -> None:
    # Given an invalid MCP result, when decoded, then the evaluation rejects it.
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()
    with pytest.raises((BenchmarkError, ValidationError)):
        parse_rpc(raw, rerank=rerank)


def test_seed_and_rpc_use_mutually_exclusive_auth_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given synthetic seed/search HTTP endpoints, when the full CLI runs.
    (tmp_path / "key.env").write_text("P0_API_KEY=fixture-only-key\n")
    (tmp_path / "corpus.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "zh-01",
                        "category": "chinese",
                        "query": "语言？",
                        "documents": ["中文", "英语", "日语", "法语"],
                    }
                ],
                "negative_queries": ["血型？", "门禁？", "生日？", "成立？"],
            }
        )
    )

    def wire(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            assert request.headers["authorization"] == "Bearer fixture-only-key"
            assert "x-api-key" not in request.headers
            body = {"results": [{"metadata": {"fixture_id": "zh-01-0"}, "score_details": {}, "rerank_score": 0.9}]}
            return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": json.dumps(body)}]}})
        assert request.headers["x-api-key"] == "fixture-only-key"
        assert "authorization" not in request.headers
        if request.url.path == "/v1/memories/scan":
            return httpx.Response(200, json={"total": 0})
        if request.method == "POST":
            return httpx.Response(200, json={"results": [{"id": "seed-id"}]})
        patch = json.loads(request.content)
        assert set(patch) == {"text", "metadata"}
        assert patch["text"] in {"中文", "英语", "日语", "法语"}
        assert dict(request.url.params) == {"project_id": "p0-eval", "app_id": "p0-eval"}
        return httpx.Response(200, json={"message": "updated"})

    transport = httpx.MockTransport(wire)
    monkeypatch.setattr(httpx.Client, "_transport_for_url", lambda self, url: transport)
    monkeypatch.setattr(
        run_benchmark.sys,
        "argv",
        [
            "run_benchmark.py",
            "--baseline-mcp-url",
            "http://baseline.test/mcp",
            "--nlp-mcp-url",
            "http://nlp.test/mcp",
            "--baseline-sidecar-url",
            "http://baseline-sidecar.test",
            "--nlp-sidecar-url",
            "http://nlp-sidecar.test",
            "--key-file",
            str(tmp_path / "key.env"),
            "--corpus",
            str(tmp_path / "corpus.json"),
            "--output",
            str(tmp_path / "output"),
            "--repetitions",
            "1",
            "--seed",
        ],
    )
    run_benchmark.main()

    # Then the wire never combines authentication modes or logs the key.
    raw = (tmp_path / "output/raw.jsonl").read_text()
    assert len(raw.splitlines()) == 24
    assert "fixture-only-key" not in raw
    summary = json.loads((tmp_path / "output/summary.json").read_text())
    assert summary["groups"]["baseline"]["overall"]["requests"] == 1
    assert summary["groups"]["baseline"]["negative"]["requests"] == 4
