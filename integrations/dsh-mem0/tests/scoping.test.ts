import { describe, it, expect } from "vitest";
import { resolveSearchFilters, resolveAddParams } from "../src/scoping.ts";

describe("resolveSearchFilters (snake_case, for filters)", () => {
  it("falls back to the configured default userId", () => {
    expect(resolveSearchFilters({}, "default-user")).toEqual({ user_id: "default-user" });
  });

  it("keeps configured user and run as mandatory filters", () => {
    expect(resolveSearchFilters({ runId: "run-9" }, "alice")).toEqual({
      user_id: "alice",
      run_id: "run-9",
    });
  });

  it("omits blank agent/run scope", () => {
    const f = resolveSearchFilters({ runId: "run-9" }, "default");
    expect(f).toEqual({ user_id: "default", run_id: "run-9" });
  });
});

describe("resolveAddParams (camelCase, for top-level add params)", () => {
  it("uses camelCase keys and falls back to the default userId", () => {
    expect(resolveAddParams({}, "default-user")).toEqual({ userId: "default-user" });
  });

  it("includes camelCase run scope only when provided", () => {
    expect(resolveAddParams({ runId: "run-9" }, "alice")).toEqual({
      userId: "alice",
      runId: "run-9",
    });
  });
});
