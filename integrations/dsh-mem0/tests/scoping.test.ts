import { describe, it, expect } from "vitest";
import { resolveSearchFilters, resolveAddParams } from "../src/scoping.ts";

describe("resolveSearchFilters (snake_case, for filters)", () => {
  it("falls back to the configured default userId", () => {
    expect(resolveSearchFilters({}, "default-user")).toEqual({ user_id: "default-user" });
  });

  it("ORs speaker attribution while retaining the run boundary", () => {
    expect(
      resolveSearchFilters({ agentId: "agent-1", runId: "run-9" }, "alice"),
    ).toEqual({
      AND: [
        { OR: [{ user_id: "alice" }, { agent_id: "agent-1" }] },
        { run_id: "run-9" },
      ],
    });
  });

  it("omits blank agent/run scope", () => {
    const f = resolveSearchFilters({ agentId: "  ", runId: "run-9" }, "default");
    expect(f).toEqual({ AND: [{ user_id: "default" }, { run_id: "run-9" }] });
  });
});

describe("resolveAddParams (camelCase, for top-level add params)", () => {
  it("uses camelCase keys and falls back to the default userId", () => {
    expect(resolveAddParams({}, "default-user")).toEqual({ userId: "default-user" });
  });

  it("includes camelCase agent/run scope only when provided", () => {
    expect(
      resolveAddParams({ agentId: "agent-1", runId: "run-9" }, "alice"),
    ).toEqual({ userId: "alice", agentId: "agent-1", runId: "run-9" });
  });
});
