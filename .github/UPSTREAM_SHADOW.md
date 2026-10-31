# Upstream synchronization: shadow mode

This first infrastructure increment observes and validates. **It cannot merge,
approve, publish, push tags, update `latest`, or deploy.** Existing release
workflows are unchanged. Passing local JSON validation is not an authorization.

## Monitoring

`upstream-shadow.yml` runs at 01:17 UTC (09:17 Asia/Shanghai) after this PR is
merged, or manually. It has only `contents: read`, uses no persisted checkout
credential, never checks out or executes upstream candidate code, and uploads a
snapshot to the run. It does not open duplicate PRs or start paid Devin sessions.
GitHub scheduled runs are best effort, not a guaranteed delivery time.

The collector paginates all releases, ignores drafts, prereleases and package
prefixes such as `ts-v*`, and selects the oldest pending stable Core version.
Do not use GitHub's `releases/latest`: this monorepo's latest release can be a
TypeScript or integration release. API/fetch failures fail the job, not `NO_UPDATE`.

The proposed upstream baseline is `v2.2.1` at
`94c3fe9f238f3dbf29c9ce98643bd71eb13077cd`. The collector checks its current tag
resolution and ancestry in the fork. **`approved: false` is deliberate**: a
maintainer must review the baseline and policy before any new candidate may
progress to advisory agent analysis. No update does not mean approval.

Classification is fail-closed: only README/changelog-only patch changes can
progress after baseline approval. Core functionality, dependency/version files,
sidecar behavior, unknown paths, workflow/agent instructions and major/minor
upgrades require human review. This conservative starter allowlist intentionally
blocks most real upgrades; extending it needs a reviewed controller change.
The full Git diff (no API file-count truncation; both sides of renames) is used.

Each report binds policy digest, upstream baseline/candidate and the Core,
sidecar and MCP main SHAs into a stable batch ID. This is an input identity, not
a durable processing ledger. Later implementation must persist completion state
before enabling repeated mutation-capable runs.

## Local / Devin shadow run

Prerequisites: Node 22, Git and authenticated `gh` with read access to the four
repositories. Run from this repository:

```sh
mkdir -p "$HOME/.devin-files"
node .github/scripts/upstream-shadow.js collect .github/upstream-shadow.json \
  "$HOME/.devin-files/mem0-upstream-shadow.json"
node .github/scripts/upstream-shadow.test.js
```

For Devin orchestration, invoke the `dynamic-workflows` skill then `run_workflow`
with `script_path` pointing to `.github/scripts/upstream-shadow-workflow.py`.
The script reads the frozen snapshot above (`MEM0_SHADOW_SNAPSHOT` overrides the
path). Do not recollect it while resuming the same workflow run.

`NO_UPDATE` and `NEEDS_HUMAN` deliberately exit before spawning any agents.
Otherwise three read-only stages pass structured findings from synchronization
to sidecar adaptation to independent review. A failed agent aborts the pipeline;
resume the same run only if its frozen inputs remain valid. These stages produce
plans, not candidate commits or test passes. They have no write authorization.

The sidecar companion workflow `Upstream compatibility shadow` accepts five
full SHAs and executes old-Core/new-sidecar, new-Core/old-sidecar and new/new
checks. A maintainer dispatches it on the sidecar default branch after reviewing
the candidates. This version intentionally does not auto-dispatch arbitrary
upstream code. It uses a fixed MCP SHA and reports source-bound evidence.

## Evidence contract

`upstream-shadow.js assess SNAPSHOT EVIDENCE OUTPUT` checks a local aggregate:

```json
{
  "batchId": "<snapshot batchId>",
  "sources": {"<all keys>": "<copy snapshot.sources exactly>"},
  "gates": [
    {"name": "core-ci", "status": "success", "tests": 1, "skipped": 0,
     "runUrl": "https://github.com/SteinX/mem0/actions/runs/123"}
  ]
}
```

All names in the snapshot's `requiredGates` must appear once. Missing, duplicate,
skipped, cancelled or stale results fail. The sample is intentionally incomplete
and must not pass. A URL or local JSON cannot authenticate the check issuer:
even a complete aggregate always returns `mergeAllowed: false` and
`publishAllowed: false`. Do not configure this as a production release gate.

## Required before live automation (not implemented in this increment)

- Approved baseline, expanded reviewed risk policy, tracked conflict resolutions
  and overlay three-way analysis; never blindly choose ours/theirs.
- Candidate PR implementation, durable batch state, deduplication, bounded
  retries and a global pause switch; retain merge ancestry or an explicit replay
  baseline if squash-only rules apply.
- Trusted check issuer/run verification, expected test inventory, authenticated
  evidence and exact head/base checks at merge time. Candidate scripts cannot
  certify themselves. Source changes invalidate evidence.
- Independent review satisfying actual GitHub rules. Administrators enable
  auto-merge and required checks without bypass/weakening existing protections.
- Release-image validation (the current live harness uses the dev Core image),
  build-once/digest promotion and a final versioned cross-repo release manifest.
  GitHub releases and GHCR tags are not cross-repo transactions.
- Failure drills: moved tag, stale source, all-skipped E2E, missing artifact,
  partial cross-repo merge/publication, duplicate events and interrupted retry.

Do not enable live merging by merely flipping a JSON flag: no such mode exists.
Keep the existing publishers and production deployments untouched until these
follow-ups are separately reviewed and verified.
