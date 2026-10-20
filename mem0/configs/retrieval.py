import math
import re
from typing import List, Optional, Set, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing_extensions import TypedDict

_PATH = re.compile(
    r"(?<![A-Za-z0-9_./\\])(?:[A-Za-z]:[\\/]|/|(?:\.{1,2}[\\/])?[A-Za-z0-9_@~.-]+[\\/])"
    r"(?:[A-Za-z0-9_@~.+:-]+[\\/])*[A-Za-z0-9_@~.+:-]+"
)
_VERSION = re.compile(
    r"(?<![A-Za-z0-9_.])[vV]?\d+\.\d+\.\d+"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"(?![A-Za-z0-9_+-]|\.[A-Za-z0-9])"
)
_T = TypeVar("_T")


class ScoredMemory(TypedDict, total=False):
    rerank_score: float


_Scored = TypeVar("_Scored", bound=ScoredMemory)


def _paths(text: str) -> Set[str]:
    paths = set()
    for match in _PATH.finditer(text):
        path = match.group().rstrip(".,;:!").replace("\\", "/")
        if re.match(r"^[A-Za-z]:", path):
            path = path.casefold()
        paths.add(path.removeprefix("./"))
    return paths


def _versions(text: str) -> Set[str]:
    return {match.group().lstrip("vV") for match in _VERSION.finditer(text)}


def _path_matches(query_path: str, stored_path: str) -> bool:
    if re.match(r"^[a-z]:/", stored_path):
        query_path = query_path.casefold()
    if query_path == stored_path:
        return True
    absolute = query_path.startswith("/") or bool(re.match(r"^[a-z]:/", query_path))
    if not absolute and stored_path.endswith("/" + query_path):
        return True
    basename = query_path.rsplit("/", 1)[-1]
    prefix = query_path.rstrip("/") + "/"
    return "." not in basename and (
        stored_path.startswith(prefix) or (not absolute and "/" + prefix in stored_path)
    )


class RetrievalPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    exact_identifiers: StrictBool = False
    rerank_candidate_limit: Optional[int] = Field(default=None, strict=True, ge=1, le=1000)
    rerank_min_score: Optional[float] = Field(default=None, strict=True, allow_inf_nan=False)

    def matches(self, query: str, text: str) -> bool:
        if not self.exact_identifiers:
            return True
        query_paths, query_versions = _paths(query), _versions(query)
        if not query_paths and not query_versions:
            return True
        stored_paths, stored_versions = _paths(text), _versions(text)
        if query_paths and not any(
            _path_matches(expected, actual) for expected in query_paths for actual in stored_paths
        ):
            return False
        return not query_versions or bool(query_versions & stored_versions)

    def rerank_pool(self, memories: List[_T]) -> List[_T]:
        if self.rerank_candidate_limit is None:
            return memories
        return memories[: self.rerank_candidate_limit]

    def filter_scores(self, memories: List[_Scored]) -> List[_Scored]:
        if self.rerank_min_score is None:
            return memories
        selected = []
        for memory in memories:
            score = memory.get("rerank_score")
            if score is None or not math.isfinite(score):
                raise ValueError("rerank_min_score requires finite rerank_score values")
            if score >= self.rerank_min_score:
                selected.append(memory)
        return selected
