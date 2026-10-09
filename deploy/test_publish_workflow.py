import os
import subprocess
import textwrap
from pathlib import Path
from typing import Final

import pytest

WORKFLOW: Final = Path(__file__).parents[1] / ".github/workflows/publish-server-image.yml"
TAG: Final = "v2.2.1-steinx.1"


def workflow_script(name: str) -> str:
    lines = WORKFLOW.read_text().splitlines()
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


def test_release_trigger_and_tag_selection_are_wired() -> None:
    workflow = WORKFLOW.read_text()
    assert "release:\n    types: [published]" in workflow
    assert "IMAGE_TAG: ${{ github.event.release.tag_name || inputs.image_tag }}" in workflow
    assert "ref: ${{ env.IMAGE_TAG }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "type=raw,value=${{ env.IMAGE_TAG }}" in workflow
    assert "type=raw,value=latest,enable=${{ env.PUSH_LATEST }}" in workflow
    assert "github.event_name == 'release' && !github.event.release.prerelease" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.push_latest" in workflow
    assert workflow.index("- name: Resolve release commit") < workflow.index("- name: Log in to GHCR")
