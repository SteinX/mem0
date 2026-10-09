from __future__ import annotations

import json
import os
import secrets
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any


REST_BASE = os.environ["MEM0_PUBLIC_BASE_URL"].rstrip("/")
MCP_URL = os.environ["MEM0_PUBLIC_MCP_URL"]
SIDECAR_BASE = os.environ["MEM0_OPERATOR_SIDECAR_URL"].rstrip("/")
ADMIN_API_KEY = os.environ["ADMIN_API_KEY"]
MCP_TOKEN = os.environ["MEM0_OSS_MCP_TOKEN"]
PROJECT_ID = os.environ.get("SIDECAR_PROJECT_ID", "default")
EXPECTED_MCP_CREDENTIAL_KIND = os.environ.get(
    "MEM0_CANARY_EXPECTED_CREDENTIAL_KIND",
    "core_api_key",
)
_consolidation_expected = os.environ.get(
    "MEM0_CANARY_EXPECT_CONSOLIDATION_ENABLED", "false"
).strip().lower()
if _consolidation_expected not in {"true", "false"}:
    raise ValueError("MEM0_CANARY_EXPECT_CONSOLIDATION_ENABLED must be true or false")
EXPECTED_CONSOLIDATION_ENABLED = _consolidation_expected == "true"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())
_CLEANUP_MEMORIES: list[tuple[str, str, str]] = []
_CLEANUP_SCOPES: set[tuple[str, str]] = set()


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 90,
) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed


def require_status(
    status: int,
    expected: int | set[int],
    operation: str,
    body: Any,
) -> None:
    allowed = {expected} if isinstance(expected, int) else expected
    if status not in allowed:
        raise RuntimeError(
            f"{operation} returned {status}; expected {sorted(allowed)}; "
            f"body_type={type(body).__name__}"
        )


def memory_id_from_add(body: Any) -> str:
    if isinstance(body, dict):
        results = body.get("results")
        if isinstance(results, list):
            for result in results:
                if isinstance(result, dict):
                    memory_id = result.get("id") or result.get("memory_id")
                    if memory_id:
                        return str(memory_id)
        memory = body.get("memory")
        if isinstance(memory, dict):
            memory_id = memory.get("id") or memory.get("memory_id")
            if memory_id:
                return str(memory_id)
        memory_id = body.get("memory_id")
        if memory_id:
            return str(memory_id)
    raise RuntimeError("add response did not contain a memory ID")


def mcp_rpc(method: str, params: dict[str, Any] | None, ident: int) -> Any:
    status, body = request_json(
        "POST",
        MCP_URL,
        payload={
            "jsonrpc": "2.0",
            "id": ident,
            "method": method,
            "params": params or {},
        },
        headers={"Authorization": f"Bearer {MCP_TOKEN}"},
    )
    require_status(status, 200, f"MCP {method}", body)
    if not isinstance(body, dict) or body.get("error"):
        raise RuntimeError(f"MCP {method} returned a JSON-RPC error")
    return body["result"]


def mcp_tool(name: str, arguments: dict[str, Any], ident: int) -> Any:
    result = mcp_rpc(
        "tools/call",
        {"name": name, "arguments": arguments},
        ident,
    )
    if result.get("isError"):
        raise RuntimeError(f"MCP tool {name} returned an error")
    content = result.get("content") or []
    if not content or not isinstance(content[0], dict):
        raise RuntimeError(f"MCP tool {name} returned no content")
    text = content[0].get("text")
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"MCP tool {name} returned non-JSON content") from exc


def contains_text(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(contains_text(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(contains_text(item, needle) for item in value)
    return False


def _cleanup_registered_memories() -> list[str]:
    failures: list[str] = []
    cleaned_scopes: set[tuple[str, str]] = set()
    while _CLEANUP_SCOPES:
        user_id, app_id = _CLEANUP_SCOPES.pop()
        query = urllib.parse.urlencode({"user_id": user_id, "app_id": app_id})
        try:
            status, _body = request_json(
                "DELETE",
                f"{REST_BASE}/memories?{query}",
                headers={"X-API-Key": ADMIN_API_KEY},
            )
            if status in {200, 204, 404}:
                cleaned_scopes.add((user_id, app_id))
            else:
                failures.append(f"scope {user_id}/{app_id}: HTTP {status}")
        except Exception as exc:
            failures.append(f"scope {user_id}/{app_id}: {type(exc).__name__}")

    while _CLEANUP_MEMORIES:
        memory_id, user_id, app_id = _CLEANUP_MEMORIES.pop()
        if (user_id, app_id) in cleaned_scopes:
            continue
        query = urllib.parse.urlencode({"user_id": user_id, "app_id": app_id})
        try:
            status, _body = request_json(
                "DELETE",
                (
                    f"{REST_BASE}/memories/"
                    f"{urllib.parse.quote(memory_id, safe='')}?{query}"
                ),
                headers={"X-API-Key": ADMIN_API_KEY},
            )
            if status not in {200, 204, 404}:
                failures.append(f"{memory_id}: HTTP {status}")
        except Exception as exc:
            failures.append(f"{memory_id}: {type(exc).__name__}")
    return failures


def cleanup_canary_memories(
    function: Callable[[], dict[str, Any]],
) -> Callable[[], dict[str, Any]]:
    @wraps(function)
    def wrapped() -> dict[str, Any]:
        primary_failed = False
        try:
            return function()
        except BaseException:
            primary_failed = True
            raise
        finally:
            cleanup_failures = _cleanup_registered_memories()
            if cleanup_failures and not primary_failed:
                raise RuntimeError(
                    f"canary cleanup failed for {len(cleanup_failures)} memories"
                )

    return wrapped


def verify_legacy_token_rejected() -> bool:
    if os.environ.get(
        "MEM0_EXPECT_LEGACY_MCP_REJECTED",
        "false",
    ).lower() not in {"1", "true", "yes", "on"}:
        return False

    token_path = Path(os.environ["MEM0_LEGACY_MCP_TOKEN_FILE"])
    if stat.S_IMODE(token_path.stat().st_mode) & 0o077:
        raise RuntimeError("legacy MCP rollback key file is not owner-only")
    legacy_token = token_path.read_text(encoding="utf-8").strip()
    if not legacy_token:
        raise RuntimeError("legacy MCP rollback key file is empty")
    if secrets.compare_digest(legacy_token, MCP_TOKEN):
        raise RuntimeError("named and legacy MCP keys must be different")

    status, body = request_json(
        "POST",
        MCP_URL,
        payload={
            "jsonrpc": "2.0",
            "id": "legacy-rejection",
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        headers={"Authorization": f"Bearer {legacy_token}"},
    )
    require_status(status, 401, "legacy MCP key rejection", body)
    return True


def expected_mcp_credential() -> dict[str, str | None]:
    if EXPECTED_MCP_CREDENTIAL_KIND == "legacy_static":
        if os.environ.get(
            "MEM0_EXPECT_LEGACY_MCP_REJECTED",
            "false",
        ).lower() in {"1", "true", "yes", "on"}:
            raise RuntimeError(
                "static rollback verification cannot expect the legacy key "
                "to be rejected"
            )
        return {
            "kind": "legacy_static",
            "id": None,
            "label": "Legacy shared MCP key",
            "key_prefix": None,
        }
    if EXPECTED_MCP_CREDENTIAL_KIND != "core_api_key":
        raise RuntimeError(
            "MEM0_CANARY_EXPECTED_CREDENTIAL_KIND must be core_api_key or legacy_static"
        )

    status, auth_context = request_json(
        "GET",
        f"{REST_BASE}/auth/me",
        headers={"X-API-Key": MCP_TOKEN},
    )
    require_status(
        status,
        200,
        "named MCP client authentication",
        auth_context,
    )
    credential = (
        auth_context.get("credential") if isinstance(auth_context, dict) else None
    )
    if not isinstance(credential, dict):
        raise RuntimeError("named MCP client authentication has no descriptor")
    if (
        credential.get("kind") != "core_api_key"
        or not isinstance(credential.get("id"), str)
        or not credential["id"]
        or not isinstance(credential.get("label"), str)
        or not credential["label"]
        or not isinstance(credential.get("key_prefix"), str)
        or not credential["key_prefix"]
    ):
        raise RuntimeError("MEM0_OSS_MCP_TOKEN is not an active named Core client key")
    return {
        "kind": "core_api_key",
        "id": credential["id"],
        "label": credential["label"],
        "key_prefix": credential["key_prefix"],
    }


@cleanup_canary_memories
def main() -> dict[str, Any]:
    if not MCP_TOKEN:
        raise RuntimeError("MEM0_OSS_MCP_TOKEN must contain the canary key")

    expected_credential = expected_mcp_credential()
    legacy_static_rejected = verify_legacy_token_rejected()

    suffix = uuid.uuid4().hex[:12]
    app_id = f"ingress-canary-{suffix}"
    user_id = f"canary-user-{suffix}"
    marker = f"transparent ingress canary {suffix}"
    updated_marker = f"{marker} updated"
    mcp_marker = f"named MCP client canary {suffix}"
    mcp_user_id = f"mcp-canary-user-{suffix}"
    request_ids = {
        "add": f"canary-add-{suffix}",
        "update": f"canary-update-{suffix}",
        "get": f"canary-get-{suffix}",
        "history": f"canary-history-{suffix}",
    }
    auth_headers = {"X-API-Key": ADMIN_API_KEY}

    credential_kind = expected_credential["kind"]
    credential_id = expected_credential["id"]
    credential_label = expected_credential["label"]
    credential_prefix = expected_credential["key_prefix"]

    status, body = request_json(
        "POST",
        f"{REST_BASE}/memories",
        payload={
            "messages": [{"role": "user", "content": marker}],
            "user_id": user_id,
            "app_id": app_id,
            "infer": False,
        },
    )
    require_status(status, 401, "unauthenticated REST add", body)

    _CLEANUP_SCOPES.add((user_id, app_id))
    status, body = request_json(
        "POST",
        f"{REST_BASE}/memories",
        payload={
            "messages": [{"role": "user", "content": marker}],
            "user_id": user_id,
            "app_id": app_id,
            "metadata": {"type": "healthcheck", "source": "ingress-canary"},
            "infer": False,
        },
        headers={**auth_headers, "X-Request-ID": request_ids["add"]},
    )
    require_status(status, 200, "REST add", body)
    memory_id = memory_id_from_add(body)
    _CLEANUP_MEMORIES.append((memory_id, user_id, app_id))

    initialized = mcp_rpc(
        "initialize",
        {"protocolVersion": "2025-03-26"},
        1,
    )
    tools = mcp_rpc("tools/list", {}, 2)["tools"]
    search = mcp_tool(
        "search_memories",
        {
            "query": marker,
            "filters": {"user_id": user_id, "app_id": app_id},
            "top_k": 5,
        },
        3,
    )
    if not contains_text(search, marker):
        raise RuntimeError("MCP search did not find the REST-created memory")

    _CLEANUP_SCOPES.add((mcp_user_id, app_id))
    mcp_add = mcp_tool(
        "add_memory",
        {
            "text": mcp_marker,
            "user_id": mcp_user_id,
            "app_id": app_id,
            "metadata": {
                "type": "healthcheck",
                "source": "named-mcp-canary",
            },
            "infer": False,
        },
        4,
    )
    mcp_event = mcp_tool(
        "get_event_status",
        {"event_id": mcp_add["event_id"]},
        5,
    )
    mcp_memory_id = memory_id_from_add(mcp_event)
    _CLEANUP_MEMORIES.append((mcp_memory_id, mcp_user_id, app_id))
    mcp_search = mcp_tool(
        "search_memories",
        {
            "query": mcp_marker,
            "filters": {"user_id": mcp_user_id, "app_id": app_id},
            "top_k": 5,
        },
        6,
    )
    if not contains_text(mcp_search, mcp_marker):
        raise RuntimeError("MCP search did not find the MCP-created memory")
    mcp_tool("delete_memory", {"id": mcp_memory_id}, 7)

    scope = urllib.parse.urlencode({"project_id": PROJECT_ID, "app_id": app_id})
    status, body = request_json(
        "PATCH",
        f"{REST_BASE}/v1/memories/{urllib.parse.quote(memory_id, safe='')}?{scope}",
        payload={"text": updated_marker},
        headers={**auth_headers, "X-Request-ID": request_ids["update"]},
    )
    require_status(status, 200, "Platform update", body)

    status, body = request_json(
        "GET",
        (
            f"{REST_BASE}/memories/{urllib.parse.quote(memory_id, safe='')}"
            f"?{urllib.parse.urlencode({'user_id': user_id, 'app_id': app_id})}"
        ),
        headers={**auth_headers, "X-Request-ID": request_ids["get"]},
    )
    require_status(status, 200, "REST get", body)
    if not contains_text(body, updated_marker):
        raise RuntimeError("REST get did not return the Platform-updated text")

    status, body = request_json(
        "GET",
        (
            f"{REST_BASE}/memories/{urllib.parse.quote(memory_id, safe='')}"
            f"/history?{urllib.parse.urlencode({'user_id': user_id, 'app_id': app_id})}"
        ),
        headers={**auth_headers, "X-Request-ID": request_ids["history"]},
    )
    require_status(status, 200, "REST history", body)

    status, events = request_json(
        "POST",
        f"{SIDECAR_BASE}/v1/events/query",
        payload={
            "project_id": PROJECT_ID,
            "app_id": app_id,
            "page": 1,
            "page_size": 100,
        },
        headers=auth_headers,
    )
    require_status(status, 200, "sidecar event query", events)
    observed_ids = {
        event.get("correlation_id")
        for event in events.get("results", [])
        if isinstance(event, dict)
    }
    missing_ids = set(request_ids.values()) - observed_ids
    if missing_ids:
        raise RuntimeError(
            f"sidecar events are missing {len(missing_ids)} canary correlations"
        )

    channel_filter = {
        "transport": "mcp",
        "credential_kind": credential_kind,
    }
    if credential_id is not None:
        channel_filter["credential_id"] = credential_id
    status, attributed_events = request_json(
        "POST",
        f"{SIDECAR_BASE}/v1/events/query",
        payload={
            "project_id": PROJECT_ID,
            "app_id": app_id,
            "channel": channel_filter,
            "page": 1,
            "page_size": 100,
        },
        headers=auth_headers,
    )
    require_status(
        status,
        200,
        "MCP client attribution query",
        attributed_events,
    )
    attributed_operations = {
        event.get("operation")
        for event in attributed_events.get("results", [])
        if isinstance(event, dict)
    }
    required_mcp_operations = {
        "memory.add",
        "memory.search",
        "memory.delete",
    }
    missing_operations = required_mcp_operations - attributed_operations
    if missing_operations:
        raise RuntimeError(
            f"MCP client attribution is missing {len(missing_operations)} operations"
        )
    for event in attributed_events.get("results", []):
        if not isinstance(event, dict):
            continue
        channel = event.get("channel")
        if (
            not isinstance(channel, dict)
            or channel.get("credential_kind") != credential_kind
            or channel.get("credential_id") != credential_id
            or channel.get("label") != credential_label
            or channel.get("key_prefix") != credential_prefix
        ):
            raise RuntimeError(
                "MCP client attribution descriptor did not match expected mode"
            )

    status, consolidation = request_json(
        "GET",
        (
            f"{SIDECAR_BASE}/v1/projects/{urllib.parse.quote(PROJECT_ID, safe='')}"
            f"/apps/{urllib.parse.quote(app_id, safe='')}/consolidation"
        ),
        headers=auth_headers,
    )
    require_status(status, 200, "consolidation status", consolidation)
    if consolidation.get("bridge_routing_ready") is not True:
        raise RuntimeError("MCP bridge routing heartbeat is not ready")
    if consolidation.get("consolidation_enabled") is not EXPECTED_CONSOLIDATION_ENABLED:
        raise RuntimeError("server-side consolidation did not match the configured expectation")
    if consolidation.get("hard_delete_enabled") is not False:
        raise RuntimeError("consolidation hard delete is not disabled")

    mcp_tool("delete_memory", {"id": memory_id}, 8)
    status, body = request_json(
        "GET",
        (
            f"{REST_BASE}/memories/{urllib.parse.quote(memory_id, safe='')}"
            f"?{urllib.parse.urlencode({'user_id': user_id, 'app_id': app_id})}"
        ),
        headers=auth_headers,
    )
    require_status(status, 404, "REST get after MCP delete", body)

    return {
        "status": "passed",
        "server": initialized["serverInfo"],
        "tool_count": len(tools),
        "memory_id": memory_id,
        "app_id": app_id,
        "event_correlations": len(request_ids),
        "bridge_routing_ready": True,
        "consolidation_enabled": consolidation["consolidation_enabled"],
        "hard_delete_enabled": False,
        "mcp_client": {
            "credential_kind": credential_kind,
            "credential_id": credential_id,
            "label": credential_label,
            "key_prefix": credential_prefix,
        },
        "mcp_operations": sorted(required_mcp_operations),
        "legacy_static_rejected": legacy_static_rejected,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except Exception as exc:
        print(f"transparent ingress check failed: {exc}", file=sys.stderr)
        raise
