import os
import sys
import uuid
from datetime import datetime, timezone
from functools import partial
from unittest.mock import MagicMock

import anyio
import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from mem0 import Memory
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request


SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import auth  # noqa: E402
from db import Base, get_db  # noqa: E402
from models import APIKey, User  # noqa: E402
from routers import api_keys as api_keys_router  # noqa: E402
from routers import auth as auth_router  # noqa: E402


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/auth/me", "headers": []})


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


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        name="Root",
        email="root@example.invalid",
        password_hash="not-used",
        role="admin",
        created_at=datetime.now(timezone.utc),
    )


def test_client_api_key_cannot_manage_credentials():
    request = _request()
    request.state.credential = {
        "kind": "core_api_key",
        "id": str(uuid.uuid4()),
        "label": "compromised-client",
        "key_prefix": "m0sk_client_",
    }

    with pytest.raises(HTTPException) as captured:
        anyio.run(
            partial(
                auth.require_credential_manager,
                request,
                user=_user(),
            )
        )

    assert captured.value.status_code == 403
    assert captured.value.detail == "Client API keys cannot manage credentials."


def test_client_api_key_cannot_access_operator_dependency():
    request = _request()
    request.state.auth_type = "api_key"
    request.state.credential = {
        "kind": "core_api_key",
        "id": str(uuid.uuid4()),
        "label": "restricted-client",
        "key_prefix": "m0sk_client_",
    }

    with pytest.raises(HTTPException) as captured:
        anyio.run(
            partial(
                auth.require_admin,
                request,
                user=_user(),
                db=None,
            )
        )

    assert captured.value.status_code == 403
    assert captured.value.detail == ("Client API keys cannot access operator endpoints.")


def test_client_api_key_cannot_modify_account_dependency():
    request = _request()
    request.state.credential = {
        "kind": "core_api_key",
        "id": str(uuid.uuid4()),
        "label": "restricted-client",
        "key_prefix": "m0sk_client_",
    }

    with pytest.raises(HTTPException) as captured:
        anyio.run(
            partial(
                auth.require_account_user,
                request,
                user=_user(),
            )
        )

    assert captured.value.status_code == 403
    assert captured.value.detail == ("Client API keys cannot modify account settings.")


@pytest.mark.parametrize("kind", ["session", "operator_static", "disabled"])
def test_interactive_and_operator_credentials_can_manage_keys(kind):
    request = _request()
    request.state.credential = {
        "kind": kind,
        "id": None,
        "label": None,
        "key_prefix": None,
    }
    user = _user()

    result = anyio.run(
        partial(
            auth.require_credential_manager,
            request,
            user=user,
        )
    )

    assert result is user


def test_client_api_key_cannot_manage_credentials_over_http(
    session,
    monkeypatch,
):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    user = _user()
    presented = "m0sk_client_cannot_rotate_credentials"
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
    app = FastAPI()
    app.include_router(api_keys_router.router)
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    headers = {"X-API-Key": presented}

    responses = [
        client.get("/api-keys", headers=headers),
        client.post(
            "/api-keys",
            headers=headers,
            json={"label": "unauthorized-child"},
        ),
        client.delete(f"/api-keys/{uuid.uuid4()}", headers=headers),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert all(response.json()["detail"] == "Client API keys cannot manage credentials." for response in responses)


def test_admin_owned_client_api_key_cannot_use_operator_or_account_routes(
    session,
    monkeypatch,
):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    user = _user()
    presented = "m0sk_client_cannot_operate_control_plane"
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
    app = FastAPI()

    @app.post("/operator")
    def operator_route(_user: User = Depends(auth.require_admin)):
        return {"ok": True}

    app.include_router(auth_router.router)
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    headers = {"X-API-Key": presented}

    responses = [
        client.post("/operator", headers=headers),
        client.patch(
            "/auth/me",
            headers=headers,
            json={"name": "renamed-by-client-key"},
        ),
        client.post(
            "/auth/onboarding-complete",
            headers=headers,
            json={"use_case": "unauthorized telemetry mutation"},
        ),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert responses[0].json()["detail"] == ("Client API keys cannot access operator endpoints.")
    assert all(
        response.json()["detail"] == "Client API keys cannot modify account settings." for response in responses[1:]
    )
    assert session.get(User, user.id).name == "Root"


def test_admin_owned_client_api_key_cannot_list_all_memories_over_http(
    session,
    monkeypatch,
):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    monkeypatch.setattr(auth, "JWT_SECRET", "test-only-jwt-secret")
    user = _user()
    presented = "m0sk_client_cannot_list_all_memories"
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
    memory.get_all.return_value = {"results": []}
    monkeypatch.setattr(Memory, "from_config", lambda _config: memory)
    import main as server_main

    monkeypatch.setattr(server_main, "get_memory_instance", lambda: memory)
    app = FastAPI()
    app.add_api_route(
        "/memories",
        server_main.get_all_memories,
        methods=["GET"],
    )
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    headers = {"X-API-Key": presented}

    unscoped = client.get("/memories", headers=headers)
    scoped = client.get(
        "/memories",
        headers=headers,
        params={"user_id": "root"},
    )

    assert unscoped.status_code == 403
    assert unscoped.json()["detail"] == ("Client API keys cannot list all memories.")
    assert scoped.status_code == 200
    memory.get_all.assert_called_once_with(
        filters={"user_id": "root"},
        show_expired=False,
    )


def test_non_admin_session_cannot_manage_credentials_over_http(
    session,
    monkeypatch,
):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    monkeypatch.setattr(auth, "JWT_SECRET", "test-only-jwt-secret")
    user = _user()
    user.role = "member"
    session.add(user)
    session.commit()
    app = FastAPI()
    app.include_router(api_keys_router.router)
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {auth.create_access_token(str(user.id), user.role)}"}

    responses = [
        client.get("/api-keys", headers=headers),
        client.post(
            "/api-keys",
            headers=headers,
            json={"label": "unauthorized-member-key"},
        ),
        client.delete(f"/api-keys/{uuid.uuid4()}", headers=headers),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert all(response.json()["detail"] == "Admin role required." for response in responses)
