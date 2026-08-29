/**
 * Per-call memory scoping.
 *
 * The plugin is mounted with one default `userId`, but a single harness install
 * is bound to one configured user. Both tools accept optional `agentId` /
 * `runId` subscopes, while the model cannot override the user tenant boundary.
 *
 * The two call sites need different key casing, and it is deliberate rather than
 * incidental: search passes scope inside `filters`, sent to the platform raw, so
 * it must be snake_case; add takes the entity params top-level, through the
 * SDK's camel->snake converter, so it must be camelCase. Keeping the split
 * explicit (like integrations/pi-agent-plugin/src/memory/scoping.ts) means the
 * asymmetry is visible in the code, not load-bearing on a converter no-op.
 */

export interface EntityParams {
  agentId?: string;
  runId?: string;
}

const clean = (v: string | undefined) => v?.trim() || undefined;

export type SearchFilters = Record<string, string | SearchFilters[]>;

/** Search: OR speaker attribution while preserving the run boundary. */
export function resolveSearchFilters(
  params: EntityParams,
  defaultUserId: string,
): SearchFilters {
  const speakers: SearchFilters[] = [
    { user_id: defaultUserId },
  ];
  const agentId = clean(params.agentId);
  if (agentId) speakers.push({ agent_id: agentId });
  const speakerScope = speakers.length === 1 ? speakers[0] : { OR: speakers };
  const runId = clean(params.runId);
  return runId ? { AND: [speakerScope, { run_id: runId }] } : speakerScope;
}

/** Add: camelCase, top-level params run through the SDK's camel->snake converter. */
export function resolveAddParams(
  params: EntityParams,
  defaultUserId: string,
): Record<string, string> {
  const out: Record<string, string> = {
    userId: defaultUserId,
  };
  const agentId = clean(params.agentId);
  if (agentId) out.agentId = agentId;
  const runId = clean(params.runId);
  if (runId) out.runId = runId;
  return out;
}
