import os
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Final

import pytest

WORKFLOW: Final = Path(__file__).parents[1] / ".github/workflows/publish-server-image.yml"
ROUTER: Final = WORKFLOW.with_name("release.yml")
TAG: Final = "v2.2.1-steinx.1"


def workflow_script(name: str, workflow: Path = WORKFLOW) -> str:
    lines = workflow.read_text().splitlines()
    start = lines.index(f"      - name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i] == "        run: |")
    end = run + 1
    while end < len(lines) and (not lines[end] or lines[end].startswith("          ")):
        end += 1
    return textwrap.dedent("\n".join(lines[run + 1 : end]))


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def source_repo(tmp_path: Path) -> tuple[Path, str, str]:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Release fixture")
    git(tmp_path, "config", "user.email", "fixture@example.test")
    (tmp_path / "source.txt").write_text("released source\n")
    git(tmp_path, "add", "source.txt")
    git(tmp_path, "commit", "-qm", "release source")
    released = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "tag", TAG)
    (tmp_path / "source.txt").write_text("later main source\n")
    git(tmp_path, "commit", "-qam", "later source")
    return tmp_path, released, git(tmp_path, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("checkout_tag", "event", "event_matches", "accepted"),
    [
        (True, "release", True, True),
        (False, "release", True, False),
        (True, "release", False, False),
        (True, "workflow_dispatch", False, True),
    ],
)
def test_publication_binds_to_tag_and_release_event(
    source_repo: tuple[Path, str, str], checkout_tag: bool, event: str, event_matches: bool, accepted: bool
) -> None:
    path, released, later = source_repo
    if checkout_tag:
        git(path, "checkout", "-q", TAG)
    output = path / "outputs.txt"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", workflow_script("Resolve release commit")],
        cwd=path,
        env={
            **os.environ,
            "IMAGE_TAG": TAG,
            "GITHUB_EVENT_NAME": event,
            "GITHUB_SHA": released if event_matches else later,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is accepted, result.stderr
    if accepted:
        assert f"sha={released}" in output.read_text().splitlines()


@pytest.mark.parametrize(("tag", "accepted"), [(TAG, True), ("v2.3.0-rc.1", True), ("main", False)])
def test_only_version_tags_are_published(tag: str, accepted: bool) -> None:
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", workflow_script("Validate requested tag")],
        env={**os.environ, "IMAGE_TAG": tag},
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is accepted


def test_release_router_is_the_only_published_listener() -> None:
    # Given: all repository workflow definitions.
    # When: locating release event listeners.
    listeners = [
        path.name for path in WORKFLOW.parent.glob("*.yml") if re.search(r"^  release:$", path.read_text(), re.M)
    ]
    # Then: publication enters through the router only.
    assert listeners == ["release.yml"]
    assert "release:\n    types: [published]" in ROUTER.read_text()


def test_reusable_publisher_and_tag_selection_are_wired() -> None:
    # Given: the caller and reusable publisher definitions.
    workflow = WORKFLOW.read_text()
    router = ROUTER.read_text()
    # When: checking the workflow call contract consumed by GitHub Actions.
    server = router.split("\n  server:\n", 1)[1]
    # Then: the caller supplies the release inputs and permissions.
    assert "  workflow_call:\n    inputs:" in workflow
    assert "    needs: route" in server
    assert "needs.route.outputs.workflow == 'publish-server-image.yml'" in server
    assert "    uses: ./.github/workflows/publish-server-image.yml" in server
    assert "      image_tag: ${{ github.event.release.tag_name }}" in server
    assert "      push_latest: ${{ !github.event.release.prerelease }}" in server
    assert "      contents: read\n      packages: write" in server
    assert "      workflow: ${{ steps.match.outputs.workflow }}" in router
    assert (
        "      - name: Dispatch ${{ steps.match.outputs.workflow }}\n        if: github.repository == 'mem0ai/mem0'"
        in router
    )
    assert "IMAGE_TAG: ${{ github.event.release.tag_name || inputs.image_tag }}" in workflow
    assert "ref: ${{ env.IMAGE_TAG }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "type=raw,value=${{ env.IMAGE_TAG }}" in workflow
    assert "PUSH_LATEST: ${{ inputs.push_latest }}" in workflow
    assert "type=raw,value=latest,enable=${{ steps.latest.outputs.push_latest }}" in workflow
    assert workflow.index("- name: Resolve release commit") < workflow.index("- name: Log in to GHCR")
    assert workflow.index("- name: Decide latest promotion") < workflow.index("- name: Image metadata")


def test_latest_decision_precedes_registry_login() -> None:
    workflow = WORKFLOW.read_text()
    decision = workflow.index("- name: Decide latest promotion")
    assert workflow.index("- name: Resolve release commit") < decision < workflow.index("- name: Log in to GHCR")


def test_publications_share_a_preserving_queue() -> None:
    # Given: both automatic and manual runs use the reusable publisher.
    workflow = WORKFLOW.read_text()
    # When: checking its publication lock.
    concurrency = workflow.split("    concurrency:\n", 1)[1].split("\n    steps:", 1)[0]
    # Then: releases share one lock and pending runs are retained.
    assert "group: server-image-${{ github.repository }}" in concurrency
    assert "cancel-in-progress: false" in concurrency
    assert "queue: max" in concurrency


@pytest.mark.parametrize(
    "case",
    [
        ("SteinX/mem0", TAG, "publish-server-image.yml"),
        ("SteinX/mem0", "ts-v1.0.0", "ts-sdk-cd.yml"),
        ("mem0ai/mem0", "ts-v1.0.0", "ts-sdk-cd.yml"),
        ("mem0ai/mem0", "cli-node-v1.0.0", "cli-node-cd.yml"),
        ("mem0ai/mem0", "cli-v1.0.0", "cli-python-cd.yml"),
        ("mem0ai/mem0", "vercel-ai-v1.0.0", "vercel-ai-cd.yml"),
        ("mem0ai/mem0", "openclaw-v1.0.0", "openclaw-cd.yml"),
        ("mem0ai/mem0", "opencode-v1.0.0", "opencode-plugin-cd.yml"),
        ("mem0ai/mem0", "pi-agent-v1.0.0", "pi-agent-plugin-cd.yml"),
        ("mem0ai/mem0", "deepseek-plugin-v1.0.0", "deepseek-plugin-cd.yml"),
        ("mem0ai/mem0", "n8n-nodes-mem0-v1.0.0", "n8n-nodes-mem0-cd.yml"),
        ("mem0ai/mem0", "mem0-strands-v1.0.0", "mem0-strands-cd.yml"),
        ("mem0ai/mem0", TAG, "cd.yml"),
        ("mem0ai/mem0", "unknown", None),
    ],
)
def test_release_routing_when_repository_and_prefix_are_selected(
    tmp_path: Path, case: tuple[str, str, str | None]
) -> None:
    # Given: an official or fork release tag.
    repository, tag, expected = case
    output = tmp_path / "outputs.txt"
    # When: executing the actual router step.
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", workflow_script("Match tag prefix to CD workflow", ROUTER)],
        env={
            **os.environ,
            "GITHUB_REPOSITORY": repository,
            "TAG": tag,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.txt"),
        },
        capture_output=True,
        text=True,
    )
    # Then: the output names only the matching publisher.
    assert (result.returncode == 0) is (expected is not None), result.stderr
    if expected is not None:
        assert output.read_text().splitlines() == [f"workflow={expected}"]


@pytest.mark.parametrize(
    "case",
    [
        ("release", "true", TAG, 0, "true"),
        ("release", "true", "v2.2.1-steinx.2", 0, "false"),
        ("release", "false", TAG, 0, "false"),
        ("workflow_dispatch", "true", "v2.2.1-steinx.2", 0, "true"),
        ("workflow_dispatch", "false", TAG, 0, "false"),
        ("release", "true", TAG, 1, None),
    ],
)
def test_latest_promotion_when_release_state_changes(
    tmp_path: Path, case: tuple[str, str, str, int, str | None]
) -> None:
    # Given: the current latest stable release returned by GitHub's API.
    event, requested, latest_tag, api_exit, expected = case
    gh = tmp_path / "gh"
    gh.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$GH_CALL_LOG"\ntest -n "$GH_TOKEN" || exit 2\n'
        'printf "%s\\n" "$LATEST_TAG"\nexit "$GH_EXIT_CODE"\n'
    )
    gh.chmod(0o755)
    output = tmp_path / "outputs.txt"
    call_log = tmp_path / "gh-call.txt"
    # When: executing the actual promotion decision step.
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", workflow_script("Decide latest promotion")],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REPOSITORY": "SteinX/mem0",
            "IMAGE_TAG": TAG,
            "PUSH_LATEST": requested,
            "GITHUB_OUTPUT": str(output),
            "GH_TOKEN": "fixture-token",
            "GH_CALL_LOG": str(call_log),
            "LATEST_TAG": latest_tag,
            "GH_EXIT_CODE": str(api_exit),
        },
        capture_output=True,
        text=True,
    )
    # Then: outdated releases keep immutable tags and explicit manual promotion survives.
    assert (result.returncode == 0) is (expected is not None), result.stderr
    if expected is not None:
        assert output.read_text().splitlines() == [f"push_latest={expected}"]
    if event == "release" and requested == "true":
        assert call_log.read_text().strip() == "api repos/SteinX/mem0/releases/latest --jq .tag_name"
    else:
        assert not call_log.exists()


def test_router_changes_select_the_deployment_checks() -> None:
    # Given: the workflows that select and execute publication tests.
    gate = WORKFLOW.with_name("ci-gate.yml").read_text()
    checks = WORKFLOW.with_name("server-deployment-checks.yml").read_text()
    # When: checking the router's CI routing contract.
    # Then: changing the router selects the deployment pipeline on PR and push.
    assert "              - '.github/workflows/release.yml'" in gate.split("            server_deployment:\n", 1)[1]
    assert "      - '.github/workflows/release.yml'" in checks
    assert "python -m pytest deploy/test_publish_workflow.py -q" in checks
