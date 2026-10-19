from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[1] / "hooks" / "adapter.py"
QUESTION = "Where is the verified project configuration implemented?"


@pytest.fixture
def recall_session(tmp_path):
    requests = []
    fail_next = threading.Event()
    stream_next = threading.Event()
    stop_stream = threading.Event()
    memory = {
        "id": "native-recall-memory",
        "memory": "Configuration is in config.py.",
        "created_at": "2026-10-18T10:00:00Z",
        "updated_at": "2026-10-18T11:00:00Z",
    }

    class SearchHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if fail_next.is_set():
                fail_next.clear()
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            if stream_next.is_set():
                stream_next.clear()
                self.send_header("Content-Length", "100")
                self.end_headers()
                for _ in range(100):
                    if stop_stream.wait(0.05):
                        return
                    try:
                        self.wfile.write(b" ")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                return
            body = json.dumps({"results": [memory]}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SearchHandler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    environment = {
        **os.environ,
        "MEM0_TELEMETRY": "false",
        "MEM0_CODE_AUTO_FLUSH": "false",
        "MEM0_API_KEY": "native-test-placeholder",
        "MEM0_API_URL": f"http://127.0.0.1:{server.server_port}",
        "MEM0_CODE_DATA_DIR": str(tmp_path / "data"),
        "MEM0_CODE_USER_ID": "native-test-user",
        "MEM0_PROJECT_ID": "native-test-project",
        "MEM0_CODE_MIN_QUERY_CHARS": "20",
        "MEM0_CODE_SEARCH_ONCE_PER_SESSION": "false",
    }

    def invoke(action: str, prompt: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ADAPTER), action, "--plugin-data-dir", str(tmp_path / "data")],
            input=json.dumps({"session_id": "native-test-session", "cwd": str(tmp_path), "prompt": prompt}),
            text=True,
            capture_output=True,
            env=environment,
            timeout=3,
            check=True,
        )

    try:
        yield invoke, requests, fail_next, stream_next
    finally:
        stop_stream.set()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


def test_native_hook_recalls_after_short_prompts_and_emits_context_once(recall_session):
    invoke, requests, _, _ = recall_session
    assert invoke("session-start").stdout == ""
    assert invoke("user-prompt", "继续").stdout == ""

    result = invoke("user-prompt", QUESTION)

    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert "id=native-recall-memory" in output["additionalContext"]
    assert "2026-10-18T11:00:00Z" in output["additionalContext"]
    assert invoke("user-prompt", QUESTION).stdout == ""
    assert len(requests) == 1
    assert requests[0]["query"] == QUESTION


def test_native_hook_retries_after_an_http_failure(recall_session):
    invoke, requests, fail_next, _ = recall_session
    fail_next.set()
    assert invoke("user-prompt", QUESTION).stdout == ""

    result = invoke("user-prompt", QUESTION)

    assert json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert invoke("user-prompt", QUESTION).stdout == ""
    assert len(requests) == 2


def test_native_hook_deadline_allows_a_later_retry(recall_session):
    invoke, requests, _, stream_next = recall_session
    stream_next.set()
    started = time.monotonic()

    timed_out = invoke("user-prompt", QUESTION)

    assert time.monotonic() - started < 3
    assert timed_out.stdout == ""
    result = invoke("user-prompt", QUESTION)
    assert json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert len(requests) == 2
