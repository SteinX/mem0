"""Durable, fail-closed execution receipts for operator sidecar adds."""

import hashlib
import json
import re
from typing import Final, Literal

from fastapi import HTTPException
from models import MutationReceipt
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

MARKER: Final = "_mem0_sidecar_mutation_id"


class Receipt(BaseModel):
    model_config = ConfigDict(frozen=True)
    mutation_id: str
    project_id: str
    app_id: str
    status: Literal["RUNNING", "SUCCEEDED", "FAILED"]
    result: dict[str, JsonValue] | None = None


def _view(row: MutationReceipt) -> Receipt:
    return Receipt(
        mutation_id=row.mutation_id,
        project_id=row.project_id,
        app_id=row.app_id,
        status=row.status,
        result=json.loads(row.result_json) if row.result_json is not None else None,
    )


def validate_marker(marker: JsonValue) -> str:
    if not isinstance(marker, str) or re.fullmatch(r"[0-9a-f]{64}", marker) is None:
        raise HTTPException(400, "Invalid reserved mutation marker.")
    return marker


def claim(factory: sessionmaker[Session], payload: dict[str, JsonValue]) -> Receipt:
    """Commit an exclusive claim before execution; duplicates never execute again."""
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise HTTPException(400, "Mutation metadata is required.")
    marker = validate_marker(metadata.get(MARKER))
    scope = [metadata.get("_mem0_sidecar_project_id"), metadata.get("_mem0_sidecar_app_id")]
    if not all(isinstance(value, str) and value and value == value.strip() for value in scope):
        raise HTTPException(400, "Canonical project_id and app_id are required.")
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    row = MutationReceipt(
        mutation_id=marker,
        project_id=scope[0],
        app_id=scope[1],
        fingerprint=fingerprint,
        status="RUNNING",
    )
    with factory() as session:
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.get(MutationReceipt, marker)
            if existing is None:
                raise
            if existing.fingerprint == fingerprint and existing.status == "SUCCEEDED":
                return _view(existing)
            raise HTTPException(409, "Mutation marker already claimed; execution will not be repeated.") from None
    return _view(row)


def read(factory: sessionmaker[Session], marker: str) -> Receipt:
    validate_marker(marker)
    with factory() as session:
        row = session.get(MutationReceipt, marker)
        if row is None:
            raise HTTPException(404, "Mutation receipt not found; execution state is unknown.")
        return _view(row)


def _finish(factory: sessionmaker[Session], receipt: Receipt, *, result_json: str | None, failed: bool) -> None:
    with factory() as session:
        result = session.execute(
            update(MutationReceipt)
            .where(MutationReceipt.mutation_id == receipt.mutation_id, MutationReceipt.status == "RUNNING")
            .values(
                status="FAILED" if failed else "SUCCEEDED",
                result_json=result_json,
                error="Execution ended with an error." if failed else None,
            )
        )
        if result.rowcount != 1:
            raise HTTPException(409, "Mutation receipt is already terminal.")
        session.commit()


def succeed(factory: sessionmaker[Session], receipt: Receipt, result: dict[str, JsonValue]) -> None:
    _finish(factory, receipt, result_json=json.dumps(result, allow_nan=False), failed=False)


def fail(factory: sessionmaker[Session], receipt: Receipt) -> None:
    """Only call after the synchronous executor has actually returned an error."""
    _finish(factory, receipt, result_json=None, failed=True)
