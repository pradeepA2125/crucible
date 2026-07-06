import { describe, expect, it } from "vitest";
import { buildSpaceModel, diffSpaceModel } from "../src/graph/space-model.js";
import { ROOT, edge, extModule, fileNode, snap, symNode } from "./graph-fixtures.js";

void ROOT;

describe("buildSpaceModel — stars & filtering", () => {
  it("emits one star per File node with workspace-relative id", () => {
    const m = buildSpaceModel(snap([fileNode("apps/web/src/a.ts")], []));
    expect(m.stars).toHaveLength(1);
    expect(m.stars[0].id).toBe("apps/web/src/a.ts");
    expect(m.stars[0].dir).toBe("apps/web/src");
  });

  it("counts symbols per file, excluding Variable nodes", () => {
    const m = buildSpaceModel(
      snap(
        [
          fileNode("apps/web/src/a.ts"),
          symNode("Class", "apps/web/src/a.ts", "A"),
          symNode("Function", "apps/web/src/a.ts", "f"),
          symNode("Variable", "apps/web/src/a.ts", "v"),
        ],
        []
      )
    );
    expect(m.stars[0].symbolCount).toBe(2);
  });

  it("rolls symbol-level Calls edges up to file-level degree + kindMix", () => {
    const a = symNode("Class", "apps/web/src/a.ts", "A");
    const b = symNode("Function", "apps/web/src/b.ts", "f");
    const m = buildSpaceModel(
      snap([fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), a, b], [edge(a.id, b.id, "Calls")])
    );
    const sa = m.stars.find((s) => s.id === "apps/web/src/a.ts")!;
    const sb = m.stars.find((s) => s.id === "apps/web/src/b.ts")!;
    expect(sa.outDeg).toBe(1);
    expect(sa.inDeg).toBe(0);
    expect(sb.inDeg).toBe(1);
    expect(sa.kindMix.Calls).toBe(1);
  });

  it("drops References edges and self-file edges from the ambient model", () => {
    const a = symNode("Class", "apps/web/src/a.ts", "A");
    const a2 = symNode("Function", "apps/web/src/a.ts", "g");
    const b = symNode("Function", "apps/web/src/b.ts", "f");
    const m = buildSpaceModel(
      snap(
        [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), a, a2, b],
        [edge(a.id, b.id, "References"), edge(a.id, a2.id, "Calls")]
      )
    );
    const sa = m.stars.find((s) => s.id === "apps/web/src/a.ts")!;
    expect(sa.outDeg).toBe(0);
    expect(sa.inDeg).toBe(0);
  });
});

describe("buildSpaceModel — packages & orphans", () => {
  const threeFiles = (pkg: string) => [
    fileNode(`${pkg}/src/a.ts`),
    fileNode(`${pkg}/src/b.ts`),
    fileNode(`${pkg}/src/c.ts`),
  ];

  it("groups apps/* and services/* two segments deep", () => {
    const m = buildSpaceModel(snap([...threeFiles("apps/web"), ...threeFiles("services/api")], []));
    expect(m.packages.map((p) => p.id).sort()).toEqual(["apps/web", "services/api"]);
    expect(m.stars.every((s) => s.pkg !== "")).toBe(true);
  });

  it("groups other top-level dirs one segment deep", () => {
    const m = buildSpaceModel(snap(threeFiles("webview-ui"), []));
    expect(m.packages.map((p) => p.id)).toEqual(["webview-ui"]);
  });

  it("files in groups smaller than 3 become orphans (pkg='')", () => {
    const m = buildSpaceModel(snap([fileNode("scripts/x.py"), ...threeFiles("apps/web")], []));
    expect(m.stars.find((s) => s.id === "scripts/x.py")!.pkg).toBe("");
    expect(m.packages.map((p) => p.id)).toEqual(["apps/web"]);
  });

  it("root-level files are orphans", () => {
    const m = buildSpaceModel(snap([fileNode("ui.html"), ...threeFiles("apps/web")], []));
    expect(m.stars.find((s) => s.id === "ui.html")!.pkg).toBe("");
  });

  it("packages carry fileCount and sorted unique dirs", () => {
    const m = buildSpaceModel(snap(threeFiles("apps/web"), []));
    expect(m.packages[0].fileCount).toBe(3);
    expect(m.packages[0].dirs).toEqual(["apps/web/src"]);
  });
});

describe("resolveModuleSpec via Imports edges", () => {
  it("resolves ./x.js relative specs to sibling .ts files", () => {
    const files = [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/c.ts")];
    const ext = extModule("./b.js", "apps/web/src/a.ts");
    const m = buildSpaceModel(snap([...files, ext], [edge(files[0]!.id, ext.id, "Imports")]));
    const sa = m.stars.find((s) => s.id === "apps/web/src/a.ts")!;
    const sb = m.stars.find((s) => s.id === "apps/web/src/b.ts")!;
    expect(sa.outDeg).toBe(1);
    expect(sb.inDeg).toBe(1);
  });

  it("resolves ../dir specs through .. and index files", () => {
    const files = [
      fileNode("apps/web/src/client/a.ts"),
      fileNode("apps/web/src/domain/index.ts"),
      fileNode("apps/web/src/client/pad.ts"),
    ];
    const ext = extModule("../domain", "apps/web/src/client/a.ts");
    const m = buildSpaceModel(snap([...files, ext], [edge(files[0]!.id, ext.id, "Imports")]));
    expect(m.stars.find((s) => s.id === "apps/web/src/domain/index.ts")!.inDeg).toBe(1);
  });

  it("leaves bare package specs unresolved (no edge)", () => {
    const files = [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/c.ts")];
    const ext = extModule("react", "apps/web/src/a.ts");
    const m = buildSpaceModel(snap([...files, ext], [edge(files[0]!.id, ext.id, "Imports")]));
    expect(m.stars.find((s) => s.id === "apps/web/src/a.ts")!.outDeg).toBe(0);
  });
});

describe("bundles, intraBundles, links", () => {
  function crossSnap() {
    const aFiles = [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/c.ts")];
    const sFiles = [fileNode("services/api/m.py"), fileNode("services/api/n.py"), fileNode("services/api/o.py")];
    const edges = [
      edge(aFiles[0]!.id, sFiles[0]!.id, "Calls"),
      edge(aFiles[1]!.id, sFiles[0]!.id, "Calls"),
      edge(aFiles[0]!.id, sFiles[1]!.id, "Imports"),
      edge(aFiles[0]!.id, aFiles[1]!.id, "Imports"), // intra-package
    ];
    return snap([...aFiles, ...sFiles], edges);
  }

  it("aggregates cross-package edges into one bundle with kindMix", () => {
    const m = buildSpaceModel(crossSnap());
    expect(m.bundles).toHaveLength(1);
    const b = m.bundles[0]!;
    expect(b.fromPkg).toBe("apps/web");
    expect(b.toPkg).toBe("services/api");
    expect(b.count).toBe(3);
    expect(b.kindMix).toEqual({ Calls: 2, Imports: 1 });
  });

  it("aggregates intra-package edges per directory pair", () => {
    const m = buildSpaceModel(crossSnap());
    expect(m.intraBundles).toEqual([{ pkg: "apps/web", fromDir: "apps/web/src", toDir: "apps/web/src", count: 1 }]);
  });

  it("emits deduped intra-package file links with a < b", () => {
    const m = buildSpaceModel(crossSnap());
    expect(m.links).toEqual([{ a: "apps/web/src/a.ts", b: "apps/web/src/b.ts", count: 1 }]);
  });
});

describe("entry points & hubs", () => {
  it("flags conventional entry names", () => {
    const files = [fileNode("services/api/main.py"), fileNode("services/api/util.py"), fileNode("services/api/io.py")];
    const m = buildSpaceModel(snap(files, []));
    expect(m.stars.find((s) => s.id === "services/api/main.py")!.isEntry).toBe(true);
    expect(m.stars.find((s) => s.id === "services/api/util.py")!.isEntry).toBe(false);
  });

  it("flags graph-signal entries: outDeg>=3 and inDeg==0", () => {
    const files = ["root.ts", "d1.ts", "d2.ts", "d3.ts"].map((n) => fileNode(`apps/web/src/${n}`));
    const edges = [1, 2, 3].map((i) => edge(files[0]!.id, files[i]!.id, "Imports"));
    const m = buildSpaceModel(snap(files, edges));
    expect(m.stars.find((s) => s.id === "apps/web/src/root.ts")!.isEntry).toBe(true);
  });

  it("flags the highest-degree star in a package as hub when degree >= 8", () => {
    const files = Array.from({ length: 10 }, (_, i) => fileNode(`apps/web/src/f${i}.ts`));
    const edges = Array.from({ length: 9 }, (_, i) => edge(files[i + 1]!.id, files[0]!.id, "Calls"));
    const m = buildSpaceModel(snap(files, edges));
    const hub = m.stars.find((s) => s.id === "apps/web/src/f0.ts")!;
    expect(hub.isHub).toBe(true);
    expect(m.stars.filter((s) => s.isHub)).toHaveLength(1);
  });
});

describe("diffSpaceModel", () => {
  it("reports added, removed, and changed stars by id", () => {
    const before = buildSpaceModel(
      snap([fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/c.ts")], [])
    );
    const afterFiles = [fileNode("apps/web/src/a.ts"), fileNode("apps/web/src/b.ts"), fileNode("apps/web/src/d.ts")];
    const after = buildSpaceModel(
      snap([...afterFiles, symNode("Class", "apps/web/src/a.ts", "A")], [])
    );
    const d = diffSpaceModel(before, after);
    expect(d.added.map((s) => s.id)).toEqual(["apps/web/src/d.ts"]);
    expect(d.removed).toEqual(["apps/web/src/c.ts"]);
    expect(d.changed.map((s) => s.id)).toEqual(["apps/web/src/a.ts"]);
    expect(d.bundles).toEqual(after.bundles);
  });
});
