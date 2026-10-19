from __future__ import annotations

import importlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest

CORE = Path(__file__).resolve().parents[1] / "python"
sys.path.insert(0, str(CORE))
memory_core = importlib.import_module("memory_core")
hook_runner = importlib.import_module("hook_runner")

QUESTION = "Where is the verified project configuration implemented?"
MEMORY = {
    "id": "memory-current",
    "memory": "The configuration is in config.py.",
    "created_at": "2026-10-18T10:00:00Z",
    "updated_at": "2026-10-18T11:00:00Z",
    "metadata": {"state": "current", "valid_from": "2026-10-18T09:00:00Z"},
}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    monkeypatch.setenv("MEM0_CODE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEM0_API_KEY", "diagnostic-placeholder")
    monkeypatch.setenv("MEM0_CODE_USER_ID", "recall-test-user")
    monkeypatch.setenv("MEM0_PROJECT_ID", "recall-test-project")
    monkeypatch.setenv("MEM0_CODE_MIN_QUERY_CHARS", "20")
    monkeypatch.setenv("MEM0_CODE_SEARCH_ONCE_PER_SESSION", "false")
    transport = Mock(return_value=({"results": [MEMORY]}, 100, 100))
    monkeypatch.setattr(memory_core, "_request_json_with_network_retry", transport)
    monkeypatch.setattr(memory_core, "_request_json_with_deadline", transport, raising=False)
    store = memory_core.EvidenceStore()
    payload = {"session_id": "recall-session", "cwd": str(tmp_path), "prompt": QUESTION}
    try:
        yield store, payload, transport
    finally:
        store.close()


def test_short_prompts_do_not_consume_automatic_recall(runtime):
    store, payload, transport = runtime
    hook_runner.first_prompt_memory_output(store, {**payload, "prompt": "继续"})
    hook_runner.first_prompt_memory_output(store, {**payload, "prompt": "好的"})

    output = hook_runner.first_prompt_memory_output(store, payload)

    assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert transport.call_count == 1
    assert store.conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'user_prompt'").fetchone()[0] == 3


def test_failure_can_retry_on_the_next_eligible_prompt(runtime):
    store, payload, transport = runtime
    transport.side_effect = [URLError("synthetic failure"), ({"results": [MEMORY]}, 100, 100)]
    assert hook_runner.first_prompt_memory_output(store, payload) == {}

    output = hook_runner.first_prompt_memory_output(store, payload)

    assert output["hookSpecificOutput"]["additionalContext"]
    assert hook_runner.first_prompt_memory_output(store, payload) == {}
    assert transport.call_count == 2


def test_failed_recall_attempts_are_bounded_and_survive_reopening(runtime):
    store, payload, transport = runtime
    transport.side_effect = URLError("synthetic failure")
    hook_runner.first_prompt_memory_output(store, payload)
    reopened = memory_core.EvidenceStore(store.path)
    try:
        hook_runner.first_prompt_memory_output(reopened, payload)
        hook_runner.first_prompt_memory_output(reopened, payload)
    finally:
        reopened.close()

    assert transport.call_count == 2


def test_empty_success_completes_recall_without_repeating_it(runtime):
    store, payload, transport = runtime
    transport.return_value = ({"results": []}, 100, 20)
    assert hook_runner.first_prompt_memory_output(store, payload) == {}

    assert hook_runner.first_prompt_memory_output(store, payload) == {}

    assert transport.call_count == 1


def test_success_does_not_block_another_session(runtime):
    store, payload, transport = runtime
    hook_runner.first_prompt_memory_output(store, payload)
    assert hook_runner.first_prompt_memory_output(store, payload) == {}

    output = hook_runner.first_prompt_memory_output(store, {**payload, "session_id": "another-session"})

    assert output["hookSpecificOutput"]["additionalContext"]
    assert transport.call_count == 2


def test_reentrant_prompt_cannot_start_a_second_inflight_recall(runtime):
    store, payload, transport = runtime
    overlapping = memory_core.EvidenceStore(store.path)

    def request(*args):
        assert hook_runner.first_prompt_memory_output(overlapping, payload) == {}
        return {"results": [MEMORY]}, 100, 100

    transport.side_effect = request
    try:
        output = hook_runner.first_prompt_memory_output(store, payload)
    finally:
        overlapping.close()

    assert output["hookSpecificOutput"]["additionalContext"]
    assert transport.call_count == 1


def test_legacy_failed_search_only_gets_one_more_attempt(runtime):
    store, payload, transport = runtime
    repo = store.repo_for_session(payload["session_id"], payload["cwd"])
    store.operation(repo, payload["session_id"], "first-prompt-search", 1, False)
    transport.side_effect = URLError("synthetic failure")

    hook_runner.first_prompt_memory_output(store, payload)
    hook_runner.first_prompt_memory_output(store, payload)

    assert transport.call_count == 1


def test_interrupted_recall_lease_can_expire(runtime, monkeypatch):
    store, payload, transport = runtime
    repo = store.repo_for_session(payload["session_id"], payload["cwd"])
    monkeypatch.setattr(memory_core.time, "time", lambda: 100)
    assert store.claim_automatic_recall(repo.identity, payload["session_id"])
    monkeypatch.setattr(memory_core.time, "time", lambda: 104)

    output = hook_runner.first_prompt_memory_output(store, payload)

    assert output["hookSpecificOutput"]["additionalContext"]
    assert transport.call_count == 1


def test_injected_context_preserves_identity_and_full_timestamps():
    context = memory_core.format_context([MEMORY])

    for key in ("id", "created_at", "updated_at"):
        assert MEMORY[key] in context
    assert "state=current" in context
    assert MEMORY["metadata"]["valid_from"] in context


def test_context_distinguishes_explicit_history_from_undetermined_state(monkeypatch):
    monkeypatch.setenv("MEM0_CODE_MAX_CONTEXT_CHARS", "1000")
    history = {**MEMORY, "memory": "x" * 10000, "metadata": {"superseded_by": "successor-id"}}

    context = memory_core.format_context([history])
    unknown = memory_core.format_context([{**MEMORY, "metadata": {}}])

    assert len(context) <= 1000
    assert MEMORY["id"] in context
    assert MEMORY["updated_at"] in context
    assert "state=historical" in context
    assert "superseded_by=successor-id" in context
    assert "state=unknown" in unknown


def test_deadline_covers_a_slow_streaming_response(tmp_path, monkeypatch):
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    monkeypatch.setenv("MEM0_CODE_DATA_DIR", str(tmp_path))
    stop_stream = threading.Event()
    request_started = threading.Event()

    class SlowResponse(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            request_started.set()
            for _ in range(100):
                if stop_stream.wait(0.05):
                    return
                try:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowResponse)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            memory_core._request_json_with_deadline(
                f"http://127.0.0.1:{server.server_port}/search", "test-key", {}, 0.5,
            )
        elapsed = time.monotonic() - started
    finally:
        stop_stream.set()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)

    assert request_started.is_set()
    assert elapsed < 1.5
