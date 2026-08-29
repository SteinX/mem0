import os
import sys
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import auth  # noqa: E402
from db import Base, get_db  # noqa: E402
from models import APIKey, User  # noqa: E402
from routers import entities as entities_router  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        yield database


def test_admin_owned_client_api_key_cannot_enumerate_entities(
    session,
    monkeypatch,
):
    monkeypatch.setattr(auth, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    user = User(
        id=uuid.uuid4(),
        name="Root",
        email="root@example.invalid",
        password_hash="not-used",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )
    presented = "m0sk_client_cannot_enumerate_entities"
    session.add_all(
        [
            user,
            APIKey(
                id=uuid.uuid4(),
                key_prefix=presented[:12],
                key_hash=auth.pwd_context.hash(presented),
                label="restricted-client",
                created_by=user.id,
            ),
        ]
    )
    session.commit()

    memory = MagicMock()
    monkeypatch.setattr(entities_router, "get_memory_instance", lambda: memory)
    app = FastAPI()
    app.include_router(entities_router.router)
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)

    response = client.get("/entities", headers={"X-API-Key": presented})

    assert response.status_code == 403
    assert response.json()["detail"] == ("Client API keys cannot access operator endpoints.")
    memory.vector_store.list.assert_not_called()
