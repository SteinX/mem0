"""Durable receipt regression tests using the real SQLAlchemy store."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def test_noop_result_survives_new_session_and_replays(tmp_path):
    # Given a file-backed application database and a claimed add.
    import mutation_receipts as receipts
    from db import Base

    factory = sessionmaker(create_engine(f"sqlite:///{tmp_path / 'app.db'}"), expire_on_commit=False)
    Base.metadata.create_all(factory.kw["bind"])
    payload = {
        "messages": [],
        "metadata": {"_mem0_sidecar_project_id": "p", "_mem0_sidecar_app_id": "a", receipts.MARKER: "a" * 64},
    }
    claim = receipts.claim(factory, payload)
    # When execution returns no memories, its response is committed before delivery.
    receipts.succeed(factory, claim, {"results": []})
    # Then losing delivery does not lose the result or permit reexecution.
    assert receipts.read(factory, "a" * 64).result == {"results": []}
    assert receipts.claim(factory, payload).result == {"results": []}


@pytest.fixture
def api(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    from mem0 import Memory

    memory = MagicMock()
    memory.add.return_value = {"results": []}
    monkeypatch.setattr(Memory, "from_config", lambda *args, **kwargs: memory)
    import server.main as main
    import auth
    from db import Base

    factory = sessionmaker(create_engine(f"sqlite:///{tmp_path / 'http.db'}"), expire_on_commit=False)
    Base.metadata.create_all(factory.kw["bind"])
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(auth, "SessionLocal", factory)
    monkeypatch.setattr(main, "get_memory_instance", lambda: memory)
    monkeypatch.setattr(main, "_should_log_request", lambda request: False)
    monkeypatch.setattr(auth, "AUTH_DISABLED", True)
    from fastapi.testclient import TestClient

    with TestClient(main.app, raise_server_exceptions=False) as client:
        yield client, memory, factory


def body():
    return {
        "messages": [{"role": "user", "content": "hello"}],
        "user_id": "u",
        "metadata": {
            "_mem0_sidecar_project_id": "p",
            "_mem0_sidecar_app_id": "a",
            "_mem0_sidecar_mutation_id": "a" * 64,
        },
    }


def test_http_lost_noop_response_and_repeat(api):
    client, memory, factory = api
    client.post("/memories", json=body())  # Deliberately discard original delivery.
    observed = client.get("/internal/mutations/" + "a" * 64)
    assert observed.status_code == 200
    assert observed.json() == {
        "mutation_id": "a" * 64,
        "project_id": "p",
        "app_id": "a",
        "status": "SUCCEEDED",
        "result": {"results": []},
    }
    assert client.post("/memories", json=body()).json() == {"results": []}
    assert memory.add.call_count == 1


def test_changed_payload_cannot_reexecute(api):
    client, memory, factory = api
    client.post("/memories", json=body())
    changed = body()
    changed["messages"][0]["content"] = "different"
    assert client.post("/memories", json=changed).status_code == 409
    assert memory.add.call_count == 1


def test_running_claim_is_not_reexecuted(api):
    client, memory, factory = api
    import mutation_receipts
    import server.main as main

    mutation_receipts.claim(factory, main.MemoryCreate(**body()).model_dump(mode="json"))
    assert client.post("/memories", json=body()).status_code == 409
    assert client.get("/internal/mutations/" + "a" * 64).json()["status"] == "RUNNING"
    memory.add.assert_not_called()


def test_handled_failure_is_durable_and_not_reexecuted(api):
    client, memory, factory = api
    memory.add.side_effect = ValueError("sensitive provider data")
    assert client.post("/memories", json=body()).status_code == 400
    observed = client.get("/internal/mutations/" + "a" * 64)
    assert observed.json()["status"] == "FAILED"
    assert "sensitive" not in observed.text
    assert client.post("/memories", json=body()).status_code == 409
    assert memory.add.call_count == 1


def test_receipt_persistence_failure_stays_running(api, monkeypatch):
    client, memory, factory = api
    import mutation_receipts
    from sqlalchemy.exc import OperationalError

    def fail_store(*args):
        raise OperationalError("store", {}, RuntimeError("disconnected"))

    monkeypatch.setattr(mutation_receipts, "succeed", fail_store)
    assert client.post("/memories", json=body()).status_code == 500
    assert client.get("/internal/mutations/" + "a" * 64).json()["status"] == "RUNNING"
    assert client.post("/memories", json=body()).status_code == 409
    assert memory.add.call_count == 1


def test_client_key_is_denied_receipts_and_reserved_add(api):
    client, memory, factory = api
    import auth
    import server.main as main
    from models import User
    from starlette.requests import Request

    async def client_auth(request: Request):
        request.state.auth_type = "api_key"
        request.state.credential = {"kind": "core_api_key"}
        return User(role="admin")

    main.app.dependency_overrides[auth.verify_auth] = client_auth
    try:
        assert client.post("/memories", json=body()).status_code == 403
        assert client.get("/internal/mutations/" + "a" * 64).status_code == 403
        memory.add.assert_not_called()
    finally:
        main.app.dependency_overrides.clear()


def test_missing_receipt_is_unknown(api):
    client, _, _ = api
    response = client.get("/internal/mutations/" + "b" * 64)
    assert response.status_code == 404
    assert "unknown" in response.json()["detail"]


@pytest.mark.parametrize("marker", [None, "bad", "A" * 64, 1])
def test_invalid_reserved_marker_rejected_before_execution(api, marker):
    client, memory, _ = api
    payload = body()
    payload["metadata"]["_mem0_sidecar_mutation_id"] = marker
    assert client.post("/memories", json=payload).status_code == 400
    memory.add.assert_not_called()


def test_unmarked_legacy_add_does_not_create_receipt(api):
    client, memory, factory = api
    payload = body()
    del payload["metadata"]["_mem0_sidecar_mutation_id"]
    assert client.post("/memories", json=payload).status_code == 200
    assert client.post("/memories", json=payload).status_code == 200
    assert memory.add.call_count == 2


def test_nonempty_response_replayed_exactly(api):
    client, memory, _ = api
    memory.add.return_value = {"results": [{"id": "memory-1", "event": "ADD", "memory": "hello"}]}
    first = client.post("/memories", json=body()).json()
    assert client.post("/memories", json=body()).json() == first
    assert client.get("/internal/mutations/" + "a" * 64).json()["result"] == first
    assert memory.add.call_count == 1


def test_success_commit_ack_loss_does_not_rewrite_receipt(api, monkeypatch):
    client, memory, factory = api
    import mutation_receipts
    from sqlalchemy.exc import OperationalError

    succeed = mutation_receipts.succeed

    def lost_ack(*args):
        succeed(*args)
        raise OperationalError("commit", {}, RuntimeError("ack lost"))

    monkeypatch.setattr(mutation_receipts, "succeed", lost_ack)
    assert client.post("/memories", json=body()).status_code == 500
    assert client.get("/internal/mutations/" + "a" * 64).json()["status"] == "SUCCEEDED"
    assert client.post("/memories", json=body()).status_code == 200
    assert memory.add.call_count == 1


def test_concurrent_claims_have_single_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import HTTPException
    import mutation_receipts
    from db import Base

    factory = sessionmaker(create_engine(f"sqlite:///{tmp_path / 'race.db'}"), expire_on_commit=False)
    Base.metadata.create_all(factory.kw["bind"])

    def claim_once():
        try:
            return mutation_receipts.claim(factory, body()).status
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: claim_once(), range(2)))
    assert outcomes.count("RUNNING") == 1
    assert outcomes.count(409) == 1


def test_additive_migration_preserves_existing_tables(tmp_path):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    path = Path(__file__).resolve().parents[1] / "server/alembic/versions/007_mutation_receipts.py"
    spec = importlib.util.spec_from_file_location("receipt_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE existing_data (value TEXT)"))
        connection.execute(text("INSERT INTO existing_data VALUES ('keep')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert "mutation_receipts" in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT value FROM existing_data")) == "keep"


def test_receipt_requires_auth_and_accepts_operator_key(api, monkeypatch):
    client, memory, _ = api
    import auth

    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "operator-test-key-long")
    assert client.get("/internal/mutations/" + "a" * 64).status_code == 401
    headers = {"X-API-Key": "operator-test-key-long"}
    assert client.post("/memories", json=body(), headers=headers).status_code == 200
    assert client.get("/internal/mutations/" + "a" * 64, headers=headers).json()["status"] == "SUCCEEDED"
    assert memory.add.call_count == 1
