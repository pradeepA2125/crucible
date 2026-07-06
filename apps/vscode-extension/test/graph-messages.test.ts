import { describe, expect, it, vi } from "vitest";
import { handleGraphMessage, type GraphHostDeps, type GraphToWebview } from "../src/graph/graph-messages.js";
import { GraphSnapshotError } from "../src/graph/snapshot-store.js";
import { buildSpaceModel } from "../src/graph/space-model.js";
import { fileNode, snap } from "./graph-fixtures.js";

function deps(overrides: Partial<GraphHostDeps> = {}): GraphHostDeps {
  return {
    loadModel: () =>
      buildSpaceModel(snap([fileNode("apps/w/a.ts"), fileNode("apps/w/b.ts"), fileNode("apps/w/c.ts")], [])),
    staleAgeSec: () => null,
    fileDetail: (id) => ({ fileId: id, symbols: [], edges: [], withinFileCount: 0 }),
    symbolDetail: (id) => ({ symbolId: id, edges: [] }),
    searchSymbols: () => [],
    openFile: vi.fn(async () => {}),
    buildIndex: vi.fn(async () => {}),
    ...overrides,
  };
}

function collector(): { posts: GraphToWebview[]; post: (m: GraphToWebview) => void } {
  const posts: GraphToWebview[] = [];
  return { posts, post: (m) => posts.push(m) };
}

describe("handleGraphMessage", () => {
  it("ready -> posts space with staleAgeSec", async () => {
    const { posts, post } = collector();
    await handleGraphMessage({ type: "ready" }, deps({ staleAgeSec: () => 1200 }), post);
    expect(posts[0]!.type).toBe("space");
    expect((posts[0] as { staleAgeSec: number | null }).staleAgeSec).toBe(1200);
  });

  it("ready with missing snapshot -> posts noSnapshot, never throws", async () => {
    const { posts, post } = collector();
    const d = deps({
      loadModel: () => {
        throw new GraphSnapshotError("missing", "nope");
      },
    });
    await handleGraphMessage({ type: "ready" }, d, post);
    expect(posts[0]).toMatchObject({ type: "noSnapshot", reason: "missing", building: false });
  });

  it("buildIndex -> calls deps.buildIndex then reports noSnapshot building=true when still absent", async () => {
    const { posts, post } = collector();
    const d = deps({
      loadModel: () => {
        throw new GraphSnapshotError("missing", "nope");
      },
    });
    await handleGraphMessage({ type: "buildIndex" }, d, post);
    expect(d.buildIndex).toHaveBeenCalledOnce();
    expect(posts[0]).toMatchObject({ type: "noSnapshot", building: true });
  });

  it("fileDetail / symbolDetail / searchSymbols round-trip", async () => {
    const { posts, post } = collector();
    const d = deps();
    await handleGraphMessage({ type: "fileDetail", fileId: "apps/w/a.ts" }, d, post);
    await handleGraphMessage({ type: "symbolDetail", symbolId: "class:x:A" }, d, post);
    await handleGraphMessage({ type: "searchSymbols", query: "run" }, d, post);
    expect(posts.map((p) => p.type)).toEqual(["fileDetail", "symbolDetail", "symbolHits"]);
  });

  it("openFile delegates and posts error on failure", async () => {
    const { posts, post } = collector();
    const d = deps({
      openFile: vi.fn(async () => {
        throw new Error("no such file");
      }),
    });
    await handleGraphMessage({ type: "openFile", path: "apps/w/a.ts", line: 12 }, d, post);
    expect(d.openFile).toHaveBeenCalledWith("apps/w/a.ts", 12);
    expect(posts[0]).toMatchObject({ type: "error" });
  });
});
