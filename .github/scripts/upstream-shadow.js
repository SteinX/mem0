#!/usr/bin/env node
// This controller deliberately has no merge, tag, release or dispatch operation.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const SHA = /^[0-9a-f]{40}$/;
const TAG = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const REQUIRED_GATES = [
  'core-ci', 'dashboard-native', 'sidecar-unit', 'dashboard-overlay',
  'old-core-new-sidecar', 'new-core-old-sidecar', 'new-core-new-sidecar',
  'release-images', 'independent-review',
];
const digest = (value) => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');

function version(tag) {
  const match = TAG.exec(tag);
  assert(match, `Not a stable Core tag: ${tag}`);
  return match.slice(1).map(Number);
}

function compareVersions(a, b) {
  const left = version(a);
  const right = version(b);
  for (let i = 0; i < left.length; i++) {
    if (left[i] !== right[i]) return left[i] > right[i] ? 1 : -1;
  }
  return 0;
}

function pendingReleases(releases, baseline) {
  return releases.filter((r) => r.draft === false && r.prerelease === false
    && TAG.test(r.tag_name) && compareVersions(r.tag_name, baseline) > 0)
    .sort((a, b) => compareVersions(a.tag_name, b.tag_name));
}

function classify(policy, candidate, paths) {
  const reasons = [];
  if (!policy.baseline.approved) reasons.push('BASELINE_NOT_APPROVED');
  const [major, minor] = version(candidate);
  const [oldMajor, oldMinor] = version(policy.baseline.tag);
  if (major !== oldMajor || minor !== oldMinor) reasons.push('MAJOR_OR_MINOR_UPDATE');
  if (compareVersions(candidate, policy.baseline.tag) <= 0) reasons.push('NOT_A_NEW_VERSION');
  if (!paths.length) reasons.push('EMPTY_DIFF');
  const blockedPaths = paths.filter((file) => file.split('/').includes('..') || !policy.allowedPaths.some((allowed) =>
    allowed.endsWith('/') ? file.startsWith(allowed) : file === allowed));
  if (blockedPaths.length) reasons.push('OUTSIDE_REVIEWED_ALLOWLIST');
  return { decision: reasons.length ? 'NEEDS_HUMAN' : 'READY_FOR_VALIDATION', reasons, blockedPaths };
}

function validatePolicy(policy) {
  assert.equal(policy.schema, 1);
  assert.equal(policy.mode, 'shadow', 'Only shadow mode is implemented');
  assert.equal(policy.upstream, 'mem0ai/mem0');
  assert.equal(policy.sidecar, 'SteinX/mem0-platform-sidecar');
  assert.equal(policy.mcp, 'SteinX/mem0-oss-mcp');
  version(policy.baseline.tag);
  assert(SHA.test(policy.baseline.sha));
  assert.equal(typeof policy.baseline.approved, 'boolean');
  assert(Array.isArray(policy.allowedPaths));
  // Expanding the allowlist requires a reviewed controller change, not just JSON.
  for (const entry of policy.allowedPaths) assert(['README.md', 'docs/changelog/'].includes(entry));
}

function assessEvidence(snapshot, evidence) {
  const failures = [];
  if (snapshot.decision !== 'READY_FOR_VALIDATION') failures.push('SOURCE_NOT_ELIGIBLE');
  if (evidence.batchId !== snapshot.batchId) failures.push('BATCH_MISMATCH');
  if (JSON.stringify(evidence.sources) !== JSON.stringify(snapshot.sources)) failures.push('SOURCE_MISMATCH');
  for (const name of REQUIRED_GATES) {
    const gates = (evidence.gates || []).filter((gate) => gate.name === name);
    const gate = gates[0];
    if (gates.length !== 1 || gate.status !== 'success' || !Number.isInteger(gate.tests)
      || gate.tests < 1 || gate.skipped !== 0 || !/^https:\/\/github\.com\/SteinX\//.test(gate.runUrl || '')) {
      failures.push(`MISSING_OR_INVALID_GATE:${name}`);
    }
  }
  // Local JSON cannot authenticate a check's issuer. Never turn it into authority.
  return {
    mode: 'shadow', wouldPassLocalShapeChecks: failures.length === 0, failures,
    mergeAllowed: false, publishAllowed: false,
    activationBlockers: ['TRUSTED_CHECK_ISSUER_VERIFICATION_NOT_IMPLEMENTED', 'ADMIN_ENABLEMENT_REQUIRED'],
  };
}

function command(binary, args) {
  return execFileSync(binary, args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }).trim();
}

function gh(endpoint, paginate = false) {
  return JSON.parse(command('gh', ['api', ...(paginate ? ['--paginate', '--slurp'] : []), endpoint]));
}

function commit(repo, ref) {
  const sha = gh(`repos/${repo}/commits/${encodeURIComponent(ref)}`).sha;
  assert(SHA.test(sha), `Invalid commit from ${repo}`);
  return sha;
}

function collect(policy) {
  validatePolicy(policy);
  const baselineSha = commit(policy.upstream, policy.baseline.tag);
  assert.equal(baselineSha, policy.baseline.sha, 'Baseline tag moved; refusing to continue');
  const releases = gh(`repos/${policy.upstream}/releases?per_page=100`, true).flat();
  const pending = pendingReleases(releases, policy.baseline.tag);
  const selected = pending[0];
  const sources = {
    upstreamBaseline: baselineSha,
    upstreamCandidate: selected ? commit(policy.upstream, selected.tag_name) : baselineSha,
    core: commit('SteinX/mem0', 'main'),
    sidecar: commit(policy.sidecar, 'main'),
    mcp: commit(policy.mcp, 'main'),
  };
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'mem0-shadow-'));
  let paths = [];
  try {
    command('git', ['init', '--bare', temp]);
    command('git', ['-C', temp, 'fetch', '--no-tags', 'https://github.com/mem0ai/mem0.git',
      sources.upstreamBaseline, sources.upstreamCandidate]);
    command('git', ['-C', temp, 'fetch', '--no-tags', 'https://github.com/SteinX/mem0.git', sources.core]);
    command('git', ['-C', temp, 'merge-base', '--is-ancestor', sources.upstreamBaseline, sources.core]);
    command('git', ['-C', temp, 'merge-base', '--is-ancestor', sources.upstreamBaseline, sources.upstreamCandidate]);
    if (selected) {
      // --no-renames includes both sides of a move, preventing allowlist laundering.
      const output = execFileSync('git', ['-C', temp, 'diff', '--no-renames', '--name-only', '-z',
        sources.upstreamBaseline, sources.upstreamCandidate], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
      paths = output.split('\0').filter(Boolean).sort();
    }
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
  const policyDigest = digest(policy);
  const batchId = digest({ policyDigest, sources });
  return {
    schema: 1, mode: 'shadow', batchId, policyDigest, sources,
    baselineTag: policy.baseline.tag,
    candidateTag: selected?.tag_name || null,
    pendingTags: pending.map((r) => r.tag_name), paths,
    ...(selected ? classify(policy, selected.tag_name, paths)
      : { decision: 'NO_UPDATE', reasons: [], blockedPaths: [] }),
    requiredGates: REQUIRED_GATES,
    mergeAllowed: false, publishAllowed: false,
    activationBlockers: ['SHADOW_ONLY', ...(!policy.baseline.approved ? ['BASELINE_NOT_APPROVED'] : [])],
  };
}

function main(args) {
  if (args[0] === 'collect' && args.length === 3) {
    const snapshot = collect(JSON.parse(fs.readFileSync(args[1], 'utf8')));
    fs.writeFileSync(args[2], `${JSON.stringify(snapshot, null, 2)}\n`);
    console.log(`${snapshot.decision}: ${snapshot.batchId}`);
  } else if (args[0] === 'assess' && args.length === 4) {
    const snapshot = JSON.parse(fs.readFileSync(args[1], 'utf8'));
    const evidence = JSON.parse(fs.readFileSync(args[2], 'utf8'));
    const verdict = assessEvidence(snapshot, evidence);
    fs.writeFileSync(args[3], `${JSON.stringify(verdict, null, 2)}\n`);
    if (!verdict.wouldPassLocalShapeChecks) process.exitCode = 1;
  } else {
    throw new Error('Usage: upstream-shadow.js collect POLICY OUTPUT | assess SNAPSHOT EVIDENCE OUTPUT');
  }
}

if (require.main === module) main(process.argv.slice(2));
module.exports = { assessEvidence, classify, collect, pendingReleases, validatePolicy, REQUIRED_GATES };
