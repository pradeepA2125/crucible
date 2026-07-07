import { describe, expect, it } from "vitest";
import { computeLayout, hash32, mulberry32 } from "./layout";
import type { SpaceModel, StarRecord } from "./types";

function star(id: string, pkg: string, over: Partial<StarRecord> = {}): StarRecord {
  const dir = id.slice(0, id.lastIndexOf("/"));
  return {
    id,
    pkg,
    dir,
    symbolCount: 3,
    inDeg: 1,
    outDeg: 1,
    kindMix: {},
    isEntry: false,
    isHub: false,
    ...over,
  };
}

function model(stars: StarRecord[], links: SpaceModel["links"] = []): SpaceModel {
  const pkgs = new Map<string, number>();
  for (const s of stars) if (s.pkg) pkgs.set(s.pkg, (pkgs.get(s.pkg) ?? 0) + 1);
  return {
    workspaceRoot: "/ws",
    generatedAtMs: 1,
    packages: [...pkgs.entries()].map(([id, fileCount]) => ({ id, fileCount, dirs: [] })),
    stars,
    bundles: [],
    intraBundles: [],
    links,
  };
}

describe("layout determinism", () => {
  const stars = [
    star("apps/web/src/a.ts", "apps/web"),
    star("apps/web/src/b.ts", "apps/web"),
    star("services/api/m.py", "services/api"),
    star("orphan.ts", ""),
  ];

  it("same model twice -> identical positions", () => {
    const r1 = computeLayout(model(stars));
    const r2 = computeLayout(model(stars));
    expect(r1.ids).toEqual(r2.ids);
    expect([...r1.positions]).toEqual([...r2.positions]);
  });

  it("hash32/mulberry32 are stable", () => {
    expect(hash32("apps/web/src/a.ts")).toBe(hash32("apps/web/src/a.ts"));
    const rng = mulberry32(42);
    const seq = [rng(), rng(), rng()];
    const rng2 = mulberry32(42);
    expect([rng2(), rng2(), rng2()]).toEqual(seq);
  });

  it("adding one star leaves other packages' positions untouched", () => {
    const before = computeLayout(model(stars));
    const withNew = [...stars, star("services/api/n.py", "services/api")];
    const after = computeLayout(model(withNew));
    const idx = (r: { ids: string[] }, id: string) => r.ids.indexOf(id) * 3;
    const iB = idx(before, "apps/web/src/a.ts");
    const iA = idx(after, "apps/web/src/a.ts");
    // apps/web is untouched by a services/api addition (pre-force positions are per-id
    // seeded; force springs only act within a package)
    expect(after.positions[iA]).toBeCloseTo(before.positions[iB]!, 5);
  });

  it("linked files end closer than the average unlinked pair in the same package", () => {
    const many = Array.from({ length: 12 }, (_, i) => star(`apps/web/src/f${i}.ts`, "apps/web"));
    const linked = model(many, [{ a: "apps/web/src/f0.ts", b: "apps/web/src/f1.ts", count: 8 }]);
    const r = computeLayout(linked);
    const pos = (id: string) => {
      const i = r.ids.indexOf(id) * 3;
      return [r.positions[i]!, r.positions[i + 1]!, r.positions[i + 2]!];
    };
    const d = (p: number[], q: number[]) => Math.hypot(p[0]! - q[0]!, p[1]! - q[1]!, p[2]! - q[2]!);
    const p0 = pos("apps/web/src/f0.ts");
    const dLinked = d(p0, pos("apps/web/src/f1.ts"));
    let sum = 0;
    for (let i = 2; i < 12; i++) sum += d(p0, pos(`apps/web/src/f${i}.ts`));
    const meanUnlinked = sum / 10;
    expect(dLinked).toBeLessThan(meanUnlinked);
  });
});
