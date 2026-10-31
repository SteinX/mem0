const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { assessEvidence, classify, pendingReleases, validatePolicy, REQUIRED_GATES } = require('./upstream-shadow');
const policy = JSON.parse(fs.readFileSync(path.join(__dirname, '../upstream-shadow.json'), 'utf8'));

test('only stable Core releases, sorted by version, not GitHub latest or publication order', () => {
  const releases = ['ts-v9.0.0', 'v2.2.3', 'v2.2.2', 'v2.2.1', 'v3.0.0-rc.1'].map((tag_name) =>
    ({ tag_name, draft: false, prerelease: false }));
  releases.push({ tag_name: 'v2.2.4', draft: true, prerelease: false });
  releases.push({ tag_name: 'v2.2.5', draft: false, prerelease: true });
  assert.deepEqual(pendingReleases(releases, 'v2.2.1').map((r) => r.tag_name), ['v2.2.2', 'v2.2.3']);
});

test('baseline approval and policy changes cannot silently activate writes', () => {
  validatePolicy(policy);
  assert(classify(policy, 'v2.2.2', ['README.md']).reasons.includes('BASELINE_NOT_APPROVED'));
  assert.throws(() => validatePolicy({ ...policy, mode: 'live' }));
  assert.throws(() => validatePolicy({ ...policy, allowedPaths: ['server/'] }));
});

test('sensitive, unknown, moved paths and major/minor updates fail closed', () => {
  const approved = { ...policy, baseline: { ...policy.baseline, approved: true } };
  assert.equal(classify(approved, 'v2.2.2', ['README.md']).decision, 'READY_FOR_VALIDATION');
  for (const file of ['server/auth.py', '.github/workflows/ci.yml', 'AGENTS.md', 'pyproject.toml',
    'mem0/memory/main.py', 'unknown.txt', 'docs/changelog/../../server/auth.py']) {
    assert.equal(classify(approved, 'v2.2.2', [file, 'README.md']).decision, 'NEEDS_HUMAN');
  }
  assert.equal(classify(approved, 'v2.3.0', ['README.md']).decision, 'NEEDS_HUMAN');
  assert.equal(classify(approved, 'v2.2.2', []).decision, 'NEEDS_HUMAN');
});

test('missing, skipped, duplicate and stale evidence fails; even complete JSON cannot authorize a merge', () => {
  const snapshot = { batchId: 'batch', sources: { core: 'a'.repeat(40) }, decision: 'READY_FOR_VALIDATION' };
  const evidence = { batchId: 'batch', sources: snapshot.sources,
    gates: REQUIRED_GATES.map((name) => ({ name, status: 'success', tests: 1, skipped: 0,
      runUrl: 'https://github.com/SteinX/mem0/actions/runs/123' })) };
  assert.equal(assessEvidence(snapshot, evidence).wouldPassLocalShapeChecks, true);
  assert.equal(assessEvidence(snapshot, evidence).mergeAllowed, false);
  assert.equal(assessEvidence(snapshot, evidence).publishAllowed, false);
  for (const changed of [
    { ...evidence, batchId: 'old' }, { ...evidence, sources: { core: 'b'.repeat(40) } },
    { ...evidence, gates: [] }, { ...evidence, gates: [...evidence.gates, evidence.gates[0]] },
    { ...evidence, gates: evidence.gates.map((g) => ({ ...g, skipped: 1 })) },
    { ...evidence, gates: evidence.gates.map((g) => ({ ...g, status: 'cancelled' })) },
  ]) assert.equal(assessEvidence(snapshot, changed).wouldPassLocalShapeChecks, false);
});

test('monitor workflow has read-only permissions, no candidate execution, and no mutation commands', () => {
  const workflow = fs.readFileSync(path.join(__dirname, '../workflows/upstream-shadow.yml'), 'utf8');
  assert.match(workflow, /contents: read/);
  assert.doesNotMatch(workflow, /: write|pull_request_target|secrets: inherit/);
  assert.doesNotMatch(workflow, /gh (pr merge|release create|workflow run)|git push/);
});
