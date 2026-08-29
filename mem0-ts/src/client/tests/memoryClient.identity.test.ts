/**
 * MemoryClient unit tests — org/project identity resolution ordering.
 *
 * Each test calls the method under test as the first awaited operation on a
 * fresh client. Any earlier await on the instance resolves identity and voids
 * the test.
 */
import { MemoryClient } from "../mem0";
import { TEST_ORG_ID, TEST_PROJECT_ID } from "./helpers";
import { setupMockFetch, installConsoleSuppression } from "./setup";

installConsoleSuppression();

// Distinct key per client keeps each test off the module-scope identity cache.
let keySeq = 0;
const freshClient = () =>
  new MemoryClient({ apiKey: `test-api-key-identity-${keySeq++}` });

// Selects by path; the ping and PostHog calls also land in the mock.
const findUrlOrNone = (mock: jest.Mock, needle: string): string | undefined =>
  mock.mock.calls
    .map((c: [string, RequestInit]) => c[0])
    .find((u: string) => u.includes(needle));

const findUrl = (mock: jest.Mock, needle: string): string => {
  const url = findUrlOrNone(mock, needle);
  expect(url).toBeDefined();
  return url as string;
};

describe("MemoryClient - project-scoped URLs on a fresh client", () => {
  test("getProject uses the resolved org and project ids", async () => {
    const extra = new Map<string, { status: number; body: unknown }>();
    extra.set("/api/v1/orgs/organizations/", {
      status: 200,
      body: { custom_instructions: "Be helpful" },
    });
    const mock = setupMockFetch(extra);

    await freshClient().getProject({ fields: ["custom_instructions"] });

    const url = findUrl(mock, "/api/v1/orgs/organizations/");
    expect(url).toContain(`/api/v1/orgs/organizations/${TEST_ORG_ID}/`);
    expect(url).toContain(`/projects/${TEST_PROJECT_ID}/`);
  });

  test("updateProject uses the resolved org and project ids", async () => {
    const extra = new Map<string, { status: number; body: unknown }>();
    extra.set("/api/v1/orgs/organizations/", {
      status: 200,
      body: { custom_instructions: "Updated" },
    });
    const mock = setupMockFetch(extra);

    await freshClient().updateProject({ customInstructions: "Updated" });

    const url = findUrl(mock, "/api/v1/orgs/organizations/");
    expect(url).toContain(`/api/v1/orgs/organizations/${TEST_ORG_ID}/`);
    expect(url).toContain(`/projects/${TEST_PROJECT_ID}/`);
  });

  test("getWebhooks targets the resolved project, not null", async () => {
    const extra = new Map<string, { status: number; body: unknown }>();
    extra.set("/api/v1/webhooks/projects/", { status: 200, body: [] });
    const mock = setupMockFetch(extra);

    await freshClient().getWebhooks();

    const url = findUrl(mock, "/api/v1/webhooks/projects/");
    expect(url).toContain(`/api/v1/webhooks/projects/${TEST_PROJECT_ID}/`);
    expect(url).not.toContain("/projects/null/");
    expect(url).not.toContain("/projects/undefined/");
  });

  test("createWebhook targets the resolved project, not null", async () => {
    const extra = new Map<string, { status: number; body: unknown }>();
    extra.set("/api/v1/webhooks/projects/", {
      status: 200,
      body: { webhook_id: "wh_1" },
    });
    const mock = setupMockFetch(extra);

    await freshClient().createWebhook({
      name: "hook",
      url: "https://example.com/hook",
      eventTypes: ["memory_add"],
    });

    const url = findUrl(mock, "/api/v1/webhooks/projects/");
    expect(url).toContain(`/api/v1/webhooks/projects/${TEST_PROJECT_ID}/`);
    expect(url).not.toContain("/projects/null/");
    expect(url).not.toContain("/projects/undefined/");
  });

  test("an explicit projectId is honored without waiting on identity", async () => {
    const extra = new Map<string, { status: number; body: unknown }>();
    extra.set("/api/v1/webhooks/projects/", { status: 200, body: [] });
    const mock = setupMockFetch(extra);

    await freshClient().getWebhooks({ projectId: "proj_explicit_789" });

    expect(findUrl(mock, "/api/v1/webhooks/projects/")).toContain(
      "/api/v1/webhooks/projects/proj_explicit_789/",
    );
  });
});

describe("MemoryClient - memory endpoints do not wait on identity", () => {
  test("search completes while the ping is still pending", async () => {
    // The ping never settles.
    const mock = jest.fn((url: string) => {
      if (url.includes("/v1/ping/")) return new Promise(() => {});
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ results: [] }),
        text: () => Promise.resolve(""),
      });
    });
    global.fetch = mock as unknown as typeof global.fetch;

    await expect(
      freshClient().search("query", { filters: { user_id: "alice" } }),
    ).resolves.toBeDefined();

    expect(findUrl(mock, "/v3/memories/search/")).toBeDefined();
  });
});

describe("MemoryClient - identity cache overflow", () => {
  const pingCount = (mock: jest.Mock) =>
    mock.mock.calls.filter((c: [string, RequestInit]) =>
      c[0].includes("/v1/ping/"),
    ).length;

  // The cache is module scope, so each test needs its own module registry.
  const isolatedClient = (): typeof MemoryClient => {
    jest.resetModules();
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("../mem0").MemoryClient;
  };

  const projectOk = () => {
    const extra = new Map<string, { status: number; body: unknown }>();
    extra.set("/api/v1/orgs/organizations/", { status: 200, body: {} });
    return setupMockFetch(extra);
  };

  test("a reused credential pair pings once", async () => {
    const mock = projectOk();
    const Client = isolatedClient();

    const opts = { apiKey: "cache-warm-key", identityCacheMax: 1 };
    await new Client(opts).getProject({ fields: [] });
    await new Client(opts).getProject({ fields: [] });

    expect(pingCount(mock)).toBe(1);
  });

  test("an evicted credential pair re-pings and still resolves identity", async () => {
    const mock = projectOk();
    const Client = isolatedClient();
    const opts = (apiKey: string) => ({ apiKey, identityCacheMax: 1 });

    await new Client(opts("key-a")).getProject({ fields: [] });
    // Evicts key-a.
    await new Client(opts("key-b")).getProject({ fields: [] });
    expect(pingCount(mock)).toBe(2);

    await new Client(opts("key-a")).getProject({ fields: [] });

    // Eviction costs a ping; it never yields an unresolved identity.
    expect(pingCount(mock)).toBe(3);
    expect(findUrl(mock, "/api/v1/orgs/organizations/")).toContain(
      `/api/v1/orgs/organizations/${TEST_ORG_ID}/`,
    );
  });

  test("zero disables credential caching", async () => {
    const mock = projectOk();
    const Client = isolatedClient();

    await new Client({
      apiKey: "uncached-key",
      identityCacheMax: 0,
    }).getProject({ fields: [] });
    await new Client({
      apiKey: "uncached-key",
      identityCacheMax: 0,
    }).getProject({ fields: [] });

    expect(pingCount(mock)).toBe(2);
  });

  test("zero bypasses an identity cached by an earlier client", async () => {
    const mock = projectOk();
    const Client = isolatedClient();

    await new Client({ apiKey: "warm-key", identityCacheMax: 2 }).getProject({
      fields: [],
    });
    await new Client({ apiKey: "warm-key", identityCacheMax: 0 }).getProject({
      fields: [],
    });

    expect(pingCount(mock)).toBe(2);
  });

  test("a lower maximum evicts all excess cached identities", async () => {
    const mock = projectOk();
    const Client = isolatedClient();

    for (const apiKey of ["cached-a", "cached-b", "cached-c"]) {
      await new Client({ apiKey, identityCacheMax: 3 }).getProject({
        fields: [],
      });
    }
    await new Client({ apiKey: "cached-d", identityCacheMax: 1 }).getProject({
      fields: [],
    });
    await new Client({ apiKey: "cached-c", identityCacheMax: 1 }).getProject({
      fields: [],
    });

    expect(pingCount(mock)).toBe(5);
  });

  test("a lower maximum is enforced before returning a cache hit", async () => {
    const mock = projectOk();
    const Client = isolatedClient();

    for (const apiKey of ["warm-a", "warm-b", "warm-c"]) {
      await new Client({ apiKey, identityCacheMax: 3 }).getProject({
        fields: [],
      });
    }
    await new Client({ apiKey: "warm-c", identityCacheMax: 1 }).getProject({
      fields: [],
    });
    await new Client({ apiKey: "warm-b", identityCacheMax: 1 }).getProject({
      fields: [],
    });

    expect(pingCount(mock)).toBe(4);
  });

  test("rejects invalid cache maxima", () => {
    const Client = isolatedClient();
    expect(() => new Client({ apiKey: "key", identityCacheMax: -1 })).toThrow(
      /non-negative integer/,
    );
    expect(() => new Client({ apiKey: "key", identityCacheMax: 1.5 })).toThrow(
      /non-negative integer/,
    );
  });
});

describe("MemoryClient - unresolved identity", () => {
  const pingFails = () => {
    const mock = jest.fn((url: string) => {
      if (url.includes("/v1/ping/")) {
        return Promise.resolve({
          ok: false,
          status: 500,
          text: () => Promise.resolve("boom"),
          json: () => Promise.resolve({}),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
        text: () => Promise.resolve(""),
      });
    });
    global.fetch = mock as unknown as typeof global.fetch;
    return mock;
  };

  test("retries identity after a transient ping failure", async () => {
    let pings = 0;
    const mock = jest.fn((url: string) => {
      if (url.includes("/v1/ping/")) {
        pings += 1;
        if (pings === 1) {
          return Promise.resolve({
            ok: false,
            status: 500,
            text: () => Promise.resolve("boom"),
            json: () => Promise.resolve({}),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          text: () => Promise.resolve(""),
          json: () =>
            Promise.resolve({
              status: "ok",
              org_id: TEST_ORG_ID,
              project_id: TEST_PROJECT_ID,
              user_email: "test@example.com",
            }),
        });
      }
      if (url.includes("/api/v1/orgs/organizations/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          text: () => Promise.resolve(""),
          json: () => Promise.resolve({}),
        });
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        text: () => Promise.resolve("not found"),
        json: () => Promise.resolve({}),
      });
    });
    global.fetch = mock as unknown as typeof global.fetch;

    await freshClient().getProject({ fields: [] });

    expect(pings).toBe(2);
    expect(findUrl(mock, "/api/v1/orgs/organizations/")).toContain(
      `/api/v1/orgs/organizations/${TEST_ORG_ID}/projects/${TEST_PROJECT_ID}/`,
    );
  });

  test("refreshes a cached partial identity", async () => {
    let pings = 0;
    const mock = jest.fn((url: string) => {
      if (url.includes("/v1/ping/")) {
        pings += 1;
        return Promise.resolve({
          ok: true,
          status: 200,
          text: () => Promise.resolve(""),
          json: () =>
            Promise.resolve(
              pings === 1
                ? {
                    status: "ok",
                    org_id: TEST_ORG_ID,
                    user_email: "test@example.com",
                  }
                : {
                    status: "ok",
                    org_id: TEST_ORG_ID,
                    project_id: TEST_PROJECT_ID,
                    user_email: "test@example.com",
                  },
            ),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(""),
        json: () => Promise.resolve({}),
      });
    });
    global.fetch = mock as unknown as typeof global.fetch;

    await freshClient().getProject({ fields: [] });

    expect(pings).toBe(2);
    expect(findUrl(mock, "/api/v1/orgs/organizations/")).toContain(
      `/api/v1/orgs/organizations/${TEST_ORG_ID}/projects/${TEST_PROJECT_ID}/`,
    );
  });

  test("getProject reports the unset ids", async () => {
    pingFails();
    await expect(freshClient().getProject({ fields: [] })).rejects.toThrow(
      "organizationId and projectId must be set",
    );
  });

  test("getWebhooks reports the unset project instead of requesting null", async () => {
    const mock = pingFails();
    await expect(freshClient().getWebhooks()).rejects.toThrow(
      "projectId must be set",
    );
    expect(findUrlOrNone(mock, "/api/v1/webhooks/")).toBeUndefined();
  });

  test("createWebhook reports the unset project instead of requesting null", async () => {
    const mock = pingFails();
    await expect(
      freshClient().createWebhook({
        name: "hook",
        url: "https://example.com/hook",
        eventTypes: ["memory_add"],
      }),
    ).rejects.toThrow("projectId must be set");
    expect(findUrlOrNone(mock, "/api/v1/webhooks/")).toBeUndefined();
  });
});
