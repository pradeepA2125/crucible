import { describe, expect, it } from "vitest";
import { aggregateToDirs, applyDiff, LOD_STAR_THRESHOLD } from "./lod";
import type { SpaceModel, StarRecord } from "./types";

function star(id: string, pkg: string): StarRecord {
  return {
    id,
    pkg,
    dir: id.slice(0, id.lastIndexOf("/")),
    symbolCount: 2,
    inDeg: 1,
    outDeg: 1,
    kindMix: {},
    isEntry: false,
    isHub: false,
  };
}
function model(stars: StarRecord[]): SpaceModel {
  return {
    workspaceRoot: "/ws",
    generatedAtMs: 1,
    packages: [{ id: "apps/web", fileCount: stars.length, dirs: [] }],
    stars,
    bundles: [],
    intraBundles: [],
    links: [],
  };
}

describe("applyDiff", () => {
  it("adds, removes, replaces changed stars, swaps aggregates", () => {
    const m = model([star("apps/web/a.ts", "apps/web"), star("apps/web/b.ts", "apps/web")]);
    const changed = { ...star("apps/web/a.ts", "apps/web"), symbolCount: 9 };
    const next = applyDiff(m, {
      added: [star("apps/web/c.ts", "apps/web")],
      removed: ["apps/web/b.ts"],
      changed: [changed],
      packages: m.packages,
      bundles: [],
      intraBundles: [],
      links: [],
    });
    expect(next.stars.map((s) => s.id).sort()).toEqual(["apps/web/a.ts", "apps/web/c.ts"]);
    expect(next.stars.find((s) => s.id === "apps/web/a.ts")!.symbolCount).toBe(9);
  });
});

describe("aggregateToDirs", () => {
  it("collapses files to one star per directory with summed rollups", () => {
    const m = model([
      star("apps/web/src/a.ts", "apps/web"),
      star("apps/web/src/b.ts", "apps/web"),
      star("apps/web/lib/c.ts", "apps/web"),
    ]);
    const agg = aggregateToDirs(m);
    expect(agg.stars).toHaveLength(2);
    const src = agg.stars.find((s) => s.id === "dir:apps/web/src")!;
    expect(src.symbolCount).toBe(4);
    expect(src.inDeg).toBe(2);
  });

  it("threshold constant is 5000", () => {
    expect(LOD_STAR_THRESHOLD).toBe(5000);
  });
});
