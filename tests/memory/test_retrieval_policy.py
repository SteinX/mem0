from dataclasses import dataclass

import pytest

from mem0.configs.base import MemoryConfig
from mem0.memory import main as memory_main
from mem0.memory.main import AsyncMemory, Memory


@dataclass
class Row:
    id: str
    score: float
    payload: dict


class Store:
    def __init__(self, rows):
        self.rows = rows
        self.filters = None

    def search(self, *, query, vectors, top_k, filters):
        self.filters = filters
        return self.rows[:top_k]

    def keyword_search(self, **kwargs):
        return []


class Embedder:
    def embed(self, query, action):
        return [1.0]


class Ranker:
    def __init__(self, score=0.9):
        self.score = score
        self.seen = []

    def rerank(self, query, documents, top_k):
        self.seen = documents
        return [{**row, "rerank_score": self.score} for row in documents[:top_k]]


@pytest.fixture
def build_memory(monkeypatch, tmp_path):
    def build(asynchronous, settings, texts):
        store = Store([
            Row(str(index), 0.95 - index * 0.1, {"data": text, "user_id": "fixture"})
            for index, text in enumerate(texts)
        ])
        monkeypatch.setattr(memory_main, "MEM0_TELEMETRY", False)
        monkeypatch.setattr(memory_main, "capture_event", lambda *args, **kwargs: None)
        monkeypatch.setattr(memory_main, "extract_entities", lambda query: [])
        monkeypatch.setattr(memory_main, "lemmatize_for_bm25", lambda query: query)
        monkeypatch.setattr(memory_main.EmbedderFactory, "create", lambda *args: Embedder())
        monkeypatch.setattr(memory_main.VectorStoreFactory, "create", lambda *args: store)
        monkeypatch.setattr(memory_main.LlmFactory, "create", lambda *args: None)
        config = MemoryConfig(history_db_path=str(tmp_path / "history.db"), retrieval=settings)
        memory = AsyncMemory(config) if asynchronous else Memory(config)
        return memory, store
    return build


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(("query", "texts"), [
    ("读取 /srv/nebula/config.yaml", ["/srv/nebula/config.yml 配置", "/srv/nebula/config.yaml 配置"]),
    ("读取 deploy/.env", ["/workspace/demo/.env 配置", "/workspace/demo/deploy/.env 配置"]),
    ("版本 v1.2.3 的变更", ["版本v1.2.30增加缓存", "版本1.2.3增加缓存"]),
    ("版本 3.0.0-rc.2 的变更", ["版本3.0.0-rc.20增加缓存", "版本3.0.0-rc.2增加缓存"]),
])
async def test_exact_identifier_rejects_adjacent_value(asynchronous, query, texts, build_memory):
    memory, store = build_memory(asynchronous, {"exact_identifiers": True}, texts)
    result = (
        await memory.search(query, filters={"user_id": "fixture"})
        if asynchronous else memory.search(query, filters={"user_id": "fixture"})
    )
    assert [row["id"] for row in result["results"]] == ["1"]
    assert store.filters == {"user_id": "fixture"}


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_missing_identifier_returns_empty(asynchronous, build_memory):
    memory, _ = build_memory(asynchronous, {"exact_identifiers": True}, ["运行版本1.2.3"])
    result = (
        await memory.search("版本1.2.4的改进", filters={"user_id": "fixture"})
        if asynchronous else memory.search("版本1.2.4的改进", filters={"user_id": "fixture"})
    )
    assert result == {"results": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_rerank_minimum_rejects_low_relevance(asynchronous, build_memory):
    memory, _ = build_memory(asynchronous, {"rerank_min_score": 0.5}, ["项目使用中文"])
    memory.reranker = Ranker(score=0.01)
    result = (
        await memory.search("用户生日", filters={"user_id": "fixture"}, rerank=True)
        if asynchronous else memory.search("用户生日", filters={"user_id": "fixture"}, rerank=True)
    )
    assert result == {"results": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_rerank_candidate_limit_bounds_provider_work(asynchronous, build_memory):
    memory, _ = build_memory(asynchronous, {"rerank_candidate_limit": 1}, ["中文偏好", "中文界面"])
    ranker = Ranker()
    memory.reranker = ranker
    result = (
        await memory.search("中文", filters={"user_id": "fixture"}, rerank=True)
        if asynchronous else memory.search("中文", filters={"user_id": "fixture"}, rerank=True)
    )
    assert len(ranker.seen) == 1
    assert len(result["results"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_default_policy_keeps_existing_results(asynchronous, build_memory):
    memory, _ = build_memory(asynchronous, {}, ["/srv/demo/config.yml", "/srv/demo/config.yaml"])
    result = (
        await memory.search("/srv/demo/config.yaml", filters={"user_id": "fixture"})
        if asynchronous else memory.search("/srv/demo/config.yaml", filters={"user_id": "fixture"})
    )
    assert [row["id"] for row in result["results"]] == ["0", "1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_configured_score_gate_propagates_reranker_errors(asynchronous, build_memory):
    memory, _ = build_memory(asynchronous, {"rerank_min_score": 0.5}, ["中文偏好"])

    class BrokenRanker:
        def rerank(self, query, documents, top_k):
            raise RuntimeError("provider unavailable")

    memory.reranker = BrokenRanker()
    with pytest.raises(RuntimeError, match="provider unavailable"):
        if asynchronous:
            await memory.search("中文", filters={"user_id": "fixture"}, rerank=True)
        else:
            memory.search("中文", filters={"user_id": "fixture"}, rerank=True)
