from datetime import datetime, timezone
from inspect import isawaitable

import pytest
from pydantic import ValidationError

from mem0 import AsyncMemory, Memory
from mem0.configs.base import MemoryConfig
from mem0.configs.extraction import ExtractionCategory, ExtractionPolicy
from mem0.exceptions import LLMError

NOW = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
POLICY = ExtractionPolicy(
    version=3,
    categories=[
        ExtractionCategory(name="alpha", description="Durable user preferences"),
        ExtractionCategory(name="beta", description="Temporary work items", ttl_days=2),
    ],
)


@pytest.mark.parametrize(
    ("metadata", "category", "explicit", "expected_categories", "expiry"),
    [
        ({}, "beta", False, ["beta"], "2026-10-21"),
        ({}, "alpha", False, ["alpha"], None),
        ({"categories": ["alpha"]}, "beta", False, ["alpha"], None),
        ({"categories": []}, "beta", False, [], None),
        ({"expiration_date": "2026-12-01"}, "beta", True, ["beta"], "2026-12-01"),
        ({}, "beta", True, ["beta"], None),
    ],
)
def test_policy_preserves_explicit_values(
    metadata, category, explicit, expected_categories, expiry
):
    result = POLICY.classified_metadata(
        metadata, category, expiration_explicit=explicit, now=NOW
    )
    assert result.get("categories") == expected_categories
    assert result.get("expiration_date") == expiry
    assert result["extraction_policy_version"] == 3
    assert result["memory_kind"] == "user_fact"


def test_unknown_model_category_is_rejected():
    with pytest.raises(LLMError):
        POLICY.classified_metadata({}, "unknown", expiration_explicit=False, now=NOW)


@pytest.mark.parametrize("type_value", ["document", "alpha"])
def test_generic_metadata_type_keeps_inferred_category_and_ttl(type_value):
    result = POLICY.classified_metadata(
        {"type": type_value}, "beta", expiration_explicit=False, now=NOW
    )
    assert result["type"] == type_value
    assert result.get("categories") == ["beta"]
    assert result.get("expiration_date") == "2026-10-21"


@pytest.mark.parametrize("ttl", [0, -1, True, "2", 3651])
def test_invalid_ttl_is_rejected(ttl):
    with pytest.raises(ValidationError):
        ExtractionCategory(name="temp", description="Temporary facts", ttl_days=ttl)


def test_duplicate_categories_are_rejected():
    with pytest.raises(ValidationError):
        ExtractionPolicy(version=1, categories=[POLICY.categories[0]] * 2)


@pytest.fixture(params=[Memory, AsyncMemory])
def memory(request, mocker, tmp_path):
    embedder = mocker.MagicMock()
    embedder.embed.return_value = [0.1, 0.2]
    embedder.embed_batch.return_value = [[0.1, 0.2]]
    store = mocker.MagicMock()
    store.search.return_value = []
    llm = mocker.MagicMock()
    mocker.patch("mem0.utils.factory.EmbedderFactory.create", return_value=embedder)
    mocker.patch("mem0.utils.factory.VectorStoreFactory.create", return_value=store)
    mocker.patch("mem0.utils.factory.LlmFactory.create", return_value=llm)
    instance = request.param(MemoryConfig(history_db_path=str(tmp_path / "history.db")))
    return instance


@pytest.mark.asyncio
async def test_inferred_category_is_persisted(memory):
    memory.llm.generate_response.return_value = (
        '{"memory":[{"text":"The user prefers Chinese replies","category":"alpha"}]}'
    )
    result = memory.add("请用中文回复", user_id="user-a", extraction_policy=POLICY)
    if isawaitable(result):
        result = await result
    assert len(result["results"]) == 1
    payload = memory.vector_store.insert.call_args.kwargs["payloads"][0]
    assert payload["categories"] == ["alpha"]
    assert payload["extraction_policy_version"] == 3
    assert "expiration_date" not in payload


@pytest.mark.asyncio
async def test_bad_classification_cannot_insert(memory):
    memory.llm.generate_response.return_value = (
        '{"memory":[{"text":"A user preference","category":"foreign"}]}'
    )
    with pytest.raises(LLMError):
        result = memory.add("Preference", user_id="user-a", extraction_policy=POLICY)
        if isawaitable(result):
            await result
    memory.vector_store.insert.assert_not_called()


@pytest.mark.asyncio
async def test_generic_type_classification_is_persisted(memory, mocker):
    mocker.patch("mem0.memory.main.datetime").now.return_value = NOW
    memory.llm.generate_response.return_value = (
        '{"memory":[{"text":"Submit the QA report","category":"beta"}]}'
    )
    result = memory.add(
        "Submit the QA report", user_id="user-a", metadata={"type": "document"},
        extraction_policy=POLICY,
    )
    if isawaitable(result):
        result = await result
    assert len(result["results"]) == 1
    payload = memory.vector_store.insert.call_args.kwargs["payloads"][0]
    assert payload["type"] == "document"
    assert payload.get("categories") == ["beta"]
    assert payload.get("expiration_date") == "2026-10-21"
