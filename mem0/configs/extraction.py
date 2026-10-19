import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from mem0.exceptions import LLMError


class ExtractionCategory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4096)
    ttl_days: Optional[int] = Field(default=None, ge=1, le=3650, strict=True)


class ExtractionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1, strict=True)
    fact_kind: Literal["user_fact", "agent_experience"] = "user_fact"
    categories: List[ExtractionCategory] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def unique_categories(self) -> "ExtractionPolicy":
        names = [category.name for category in self.categories]
        if len(names) != len(set(names)):
            raise ValueError("Extraction category names must be unique")
        return self

    def instructions(self) -> str:
        rules = {
            "user_fact": (
                "Retain only verified user preferences, decisions and durable facts explicitly supplied by the user. "
                "Do not attribute assistant suggestions, tool output, speculation, greetings or questions to the user."
            ),
            "agent_experience": (
                "Retain only demonstrated agent procedures, verified outcomes and reusable lessons. "
                "Do not invent user preferences or treat an unexecuted plan as a successful experience."
            ),
        }[self.fact_kind]
        if self.categories:
            choices = [{"name": c.name, "description": c.description} for c in self.categories]
            rules += (
                '\nFor every extracted memory include a "category" field containing exactly one configured name. '
                "Classify by these descriptions, not by the category name alone: "
                + json.dumps(choices, ensure_ascii=False)
            )
        return rules + '\nReturn an empty "memory" list when no qualifying fact is present.'

    def classified_metadata(
        self,
        metadata: Mapping[str, JsonValue],
        category: JsonValue,
        *,
        expiration_explicit: bool,
        now: datetime,
    ) -> Dict[str, JsonValue]:
        result = dict(metadata)
        configured = {item.name: item for item in self.categories}
        explicit = any(key in metadata for key in ("categories", "category", "custom_category", "type"))
        if explicit:
            values = [metadata.get(key) for key in ("category", "custom_category", "type")]
            categories = metadata.get("categories")
            if isinstance(categories, list):
                values.extend(categories)
            category = next((value for value in values if isinstance(value, str) and value in configured), None)
        elif configured:
            if not isinstance(category, str) or category not in configured:
                raise LLMError("Extraction returned an unconfigured category")
            result["categories"] = [category]
        result["extraction_policy_version"] = self.version
        result["memory_kind"] = self.fact_kind
        selected = configured.get(category) if isinstance(category, str) else None
        if selected is not None and selected.ttl_days is not None and not expiration_explicit:
            result["expiration_date"] = (now.date() + timedelta(days=selected.ttl_days)).isoformat()
        return result
