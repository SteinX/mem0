import os
import sys
import uuid
from datetime import datetime, timezone
from functools import partial

import anyio
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request


SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import auth  # noqa: E402
from db import Base, get_db  # noqa: E402
from models import APIKey, RequestLog, User  # noqa: E402
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


def _verify(
    request: Request,
    session: Session,
    *,
    bearer: str | None = None,
    api_key: str | None = None,
):
    credentials = (
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=bearer)
        if bearer is not None
        else None
    )
    return anyio.run(
        partial(
            auth.verify_auth,
            request,
            credentials=credentials,
            x_api_key=api_key,
            db=session,
        ),
    )


def test_api_key_reports_the_exact_hash_matched_descriptor(session, monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    user = _user()
    presented = "m0sk_samepref_correct-secret"
    same_prefix = presented[:12]
    other = APIKey(
        id=uuid.uuid4(),
        key_prefix=same_prefix,
        key_hash=auth.pwd_context.hash("m0sk_samepref_other-secret"),
        label="other-client",
        created_by=user.id,
    )
    matched = APIKey(
        id=uuid.uuid4(),
        key_prefix=same_prefix,
        key_hash=auth.pwd_context.hash(presented),
        label="codex-devbox",
        created_by=user.id,
    )
    session.add_all([user, other, matched])
    session.commit()
    request = _request()

    result = _verify(request, session, api_key=presented)

    assert result is not None
    assert result.id == user.id
    assert request.state.auth_type == "api_key"
    assert request.state.credential == {
        "kind": "core_api_key",
        "id": str(matched.id),
        "label": "codex-devbox",
        "key_prefix": same_prefix,
    }
    assert presented not in str(request.state.credential)
    assert matched.key_hash not in str(request.state.credential)


def test_operator_static_descriptor_never_derives_a_prefix(session, monkeypatch):
    operator_secret = "operator-secret-that-must-not-leak"
    monkeypatch.setattr(auth, "ADMIN_API_KEY", operator_secret)
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    request = _request()

    result = _verify(request, session, api_key=operator_secret)

    assert result is None
    assert request.state.credential == {
        "kind": "operator_static",
        "id": None,
        "label": "Legacy admin API key",
        "key_prefix": None,
    }
    assert operator_secret not in str(request.state.credential)


def test_session_descriptor_has_no_key_fields(session, monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET", "unit-test-jwt-secret")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    user = _user()
    session.add(user)
    session.commit()
    token = auth.create_access_token(str(user.id), user.role)
    request = _request()

    result = _verify(request, session, bearer=token)

    assert result is not None
    assert result.id == user.id
    assert request.state.credential == {
        "kind": "session",
        "id": None,
        "label": None,
        "key_prefix": None,
    }
    assert token not in str(request.state.credential)


def test_disabled_descriptor_has_no_secret_fields(session, monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", True)
    request = _request()

    assert _verify(request, session) is None
    assert request.state.credential == {
        "kind": "disabled",
        "id": None,
        "label": None,
        "key_prefix": None,
    }


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


def test_revoked_api_key_is_rejected_without_descriptor(session, monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    user = _user()
    presented = "m0sk_revoked_client_secret"
    session.add(user)
    session.add(
        APIKey(
            id=uuid.uuid4(),
            key_prefix=presented[:12],
            key_hash=auth.pwd_context.hash(presented),
            label="revoked",
            created_by=user.id,
            revoked_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    request = _request()

    with pytest.raises(HTTPException) as captured:
        _verify(request, session, api_key=presented)

    assert captured.value.status_code == 401
    assert not hasattr(request.state, "credential")


def test_me_returns_additive_safe_credential_descriptor():
    user = _user()
    request = _request()
    request.state.credential = {
        "kind": "core_api_key",
        "id": str(uuid.uuid4()),
        "label": "opencode-devbox",
        "key_prefix": "m0sk_example",
    }

    response = auth_router.me(request, user)
    payload = response.model_dump(mode="json")

    assert payload["id"] == str(user.id)
    assert payload["name"] == user.name
    assert payload["credential"]["kind"] == "core_api_key"
    assert payload["credential"]["label"] == "opencode-devbox"
    assert "key" not in payload["credential"]
    assert "hash" not in payload["credential"]


def test_auth_me_never_discloses_presented_key_or_hash_in_http_or_request_log(
    session,
    monkeypatch,
):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    user = _user()
    presented = "m0sk_http_boundary_secret"
    stored_hash = auth.pwd_context.hash(presented)
    session.add_all(
        [
            user,
            APIKey(
                id=uuid.uuid4(),
                key_prefix=presented[:12],
                key_hash=stored_hash,
                label="codex-devbox",
                created_by=user.id,
            ),
        ]
    )
    session.commit()
    captured_logs: list[RequestLog] = []
    app = FastAPI()

    @app.middleware("http")
    async def capture_request_log(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Request-ID"] = "unit-test-request"
        captured_logs.append(
            RequestLog(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                latency_ms=0,
                auth_type=getattr(request.state, "auth_type", "none"),
            )
        )
        return response

    app.include_router(auth_router.router)
    app.dependency_overrides[get_db] = lambda: session

    response = TestClient(app).get(
        "/auth/me",
        headers={"X-API-Key": presented},
    )
    serialized_response = f"{response.text}\n{dict(response.headers)}"
    serialized_log = repr(vars(captured_logs[0]))

    assert response.status_code == 200
    assert response.json()["credential"]["label"] == "codex-devbox"
    assert presented not in serialized_response
    assert stored_hash not in serialized_response
    assert presented not in serialized_log
    assert stored_hash not in serialized_log
    assert captured_logs[0].auth_type == "api_key"


def test_me_rejects_inconsistent_credential_descriptor():
    user = _user()
    request = _request()
    request.state.credential = {
        "kind": "session",
        "id": str(uuid.uuid4()),
        "label": "must-not-be-accepted",
        "key_prefix": "m0sk_leaked",
    }

    with pytest.raises(HTTPException) as captured:
        auth_router.me(request, user)

    assert captured.value.status_code == 500
    assert captured.value.detail == "Authentication context is unavailable."
