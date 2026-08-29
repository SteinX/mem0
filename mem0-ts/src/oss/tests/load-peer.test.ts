import { loadPeer } from "../src/utils/load_peer";

describe("loadPeer", () => {
  it("returns the loaded optional peer", async () => {
    const peer = { ready: true };
    await expect(
      loadPeer("peer", "test adapter", async () => peer),
    ).resolves.toBe(peer);
  });

  it.each(["MODULE_NOT_FOUND", "ERR_MODULE_NOT_FOUND"])(
    "adds an install hint for a missing top-level peer (%s)",
    async (code) => {
      const cause = Object.assign(new Error("Cannot find module 'peer'"), {
        code,
      });

      await expect(
        loadPeer("peer", "test adapter", async () => {
          throw cause;
        }),
      ).rejects.toMatchObject({
        message: expect.stringContaining("npm install peer"),
        cause,
      });
    },
  );

  it("preserves a missing transitive dependency", async () => {
    const error = Object.assign(
      new Error("Cannot find module 'peer-transitive'"),
      { code: "MODULE_NOT_FOUND" },
    );

    await expect(
      loadPeer("peer", "test adapter", async () => {
        throw error;
      }),
    ).rejects.toBe(error);
  });

  it("preserves a peer initialization failure", async () => {
    const error = new Error("peer crashed while loading");

    await expect(
      loadPeer("peer", "test adapter", async () => {
        throw error;
      }),
    ).rejects.toBe(error);
  });
});
