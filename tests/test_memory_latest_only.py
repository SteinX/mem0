from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mem0 import AsyncMemory, Memory
from mem0.configs.base import MemoryConfig


@pytest.fixture
def memory_factory(tmp_path):
    with (
        patch("mem0.utils.factory.EmbedderFactory.create") as embedder,
        patch("mem0.utils.factory.VectorStoreFactory.create") as vector_store,
        patch("mem0.utils.factory.LlmFactory.create"),
        patch("mem0.memory.main.extract_entities", return_value=[]),
    ):
        embedder.return_value.embed.return_value = [0.1, 0.2]
        vector_store.return_value.keyword_search.return_value = []
        vector_store.return_value.search.return_value = [
            SimpleNamespace(id="old", score=0.99, payload={"data": "Old state", "superseded_by": "new"}),
            SimpleNamespace(id="new", score=0.8, payload={"data": "Verified state"}),
            SimpleNamespace(id="unknown", score=0.7, payload={"data": "Unclassified fact"}),
        ]
        config = MemoryConfig(history_db_path=str(tmp_path / "history.sqlite3"))
        yield config


@pytest.mark.parametrize("latest_only,expected", [(True, ["new", "unknown"]), (False, ["old", "new"])])
def test_sync_search_filters_explicit_supersession_before_top_k(memory_factory, latest_only, expected):
    memory = Memory(memory_factory)

    result = memory.search("state", filters={"user_id": "test"}, top_k=2, latest_only=latest_only)

    assert [item["id"] for item in result["results"]] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("latest_only,expected", [(True, ["new", "unknown"]), (False, ["old", "new"])])
async def test_async_search_filters_explicit_supersession_before_top_k(memory_factory, latest_only, expected):
    memory = AsyncMemory(memory_factory)

    result = await memory.search("state", filters={"user_id": "test"}, top_k=2, latest_only=latest_only)

    assert [item["id"] for item in result["results"]] == expected
