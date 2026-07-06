import { describe, expect, it } from "vitest";
import { buildSpaceModel } from "../src/graph/space-model.js";
import { ROOT, edge, fileNode, snap, symNode } from "./graph-fixtures.js";

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
