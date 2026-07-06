import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { GraphSnapshotError, GraphSnapshotStore } from "../src/graph/snapshot-store.js";
import { edge, fileNode, snap, symNode } from "./graph-fixtures.js";
import type { RawSnapshot } from "../src/graph/space-model.js";

function storeFor(s: RawSnapshot): GraphSnapshotStore {
  const dir = mkdtempSync(join(tmpdir(), "axon-"));
  const p = join(dir, "index-snapshot.json");
  writeFileSync(p, JSON.stringify(s));
  return new GraphSnapshotStore(p);
}

function richSnap(): RawSnapshot {
  const a = fileNode("apps/web/src/a.ts");
  const b = fileNode("apps/web/src/b.ts");
  const c = fileNode("apps/web/src/c.ts");
  const m = fileNode("services/api/m.py");
  const m2 = fileNode("services/api/n.py");
  const m3 = fileNode("services/api/o.py");
  const clsA = symNode("Class", "apps/web/src/a.ts", "A", 5);
  const fnA = symNode("Function", "apps/web/src/a.ts", "helper", 40);
  const varA = symNode("Variable", "apps/web/src/a.ts", "cfg", 2);
  const fnB = symNode("Function", "apps/web/src/b.ts", "run", 3);
  const fnM = symNode("Function", "services/api/m.py", "serve", 9);
  return snap(
    [a, b, c, m, m2, m3, clsA, fnA, varA, fnB, fnM],
    [
      edge(clsA.id, fnB.id, "Calls"), // out, within-package
      edge(clsA.id, fnM.id, "Calls"), // out, cross-package
      edge(fnB.id, fnA.id, "References"), // in (References only in detail)
      edge(clsA.id, fnA.id, "Calls"), // within-file -> counted, not listed
      edge(clsA.id, "external:call:Error", "Calls"), // unresolvable target
    ]
  );
}

describe("GraphSnapshotStore", () => {
  it("load() returns the model; missing file throws code=missing", () => {
    const st = storeFor(richSnap());
    expect(st.load().stars.length).toBe(6);
    const bad = new GraphSnapshotStore("/nonexistent/index-snapshot.json");
    expect(() => bad.load()).toThrowError(GraphSnapshotError);
    try {
      bad.load();
    } catch (e) {
      expect((e as GraphSnapshotError).code).toBe("missing");
    }
  });

  it("malformed JSON throws code=malformed", () => {
    const dir = mkdtempSync(join(tmpdir(), "axon-"));
    const p = join(dir, "index-snapshot.json");
    writeFileSync(p, "{nope");
    try {
      new GraphSnapshotStore(p).load();
      expect.unreachable();
    } catch (e) {
      expect((e as GraphSnapshotError).code).toBe("malformed");
    }
  });

  it("fileDetail: symbols (no Variables), grouped edges, withinFileCount", () => {
    const st = storeFor(richSnap());
    st.load();
    const d = st.fileDetail("apps/web/src/a.ts");
    expect(d.symbols.map((s) => s.name).sort()).toEqual(["A", "helper"]);
    expect(d.withinFileCount).toBe(1);
    const out = d.edges.filter((e) => e.dir === "out");
    expect(out.map((e) => e.otherFile).sort()).toEqual(["apps/web/src/b.ts", "services/api/m.py"]);
    expect(out.find((e) => e.otherFile === "services/api/m.py")!.crossPackage).toBe(true);
    const refIn = d.edges.find((e) => e.kind === "References")!;
    expect(refIn.dir).toBe("in");
    expect(refIn.otherFile).toBe("apps/web/src/b.ts");
  });

  it("symbolDetail: unresolvable externals keep fileId null with derived name", () => {
    const st = storeFor(richSnap());
    st.load();
    const d = st.symbolDetail(`class:file:/ws/apps/web/src/a.ts:A`);
    const ext = d.edges.find((e) => e.fileId === null)!;
    expect(ext.name).toBe("Error");
    expect(d.edges.filter((e) => e.dir === "out").length).toBeGreaterThanOrEqual(3);
  });

  it("searchSymbols: case-insensitive substring, capped", () => {
    const st = storeFor(richSnap());
    st.load();
    const hits = st.searchSymbols("HELP");
    expect(hits).toHaveLength(1);
    expect(hits[0]!.name).toBe("helper");
    expect(hits[0]!.fileId).toBe("apps/web/src/a.ts");
    expect(hits[0]!.line).toBe(40);
  });

  it("reload() returns a diff after the file changes", () => {
    const dir = mkdtempSync(join(tmpdir(), "axon-"));
    const p = join(dir, "index-snapshot.json");
    writeFileSync(p, JSON.stringify(richSnap()));
    const st = new GraphSnapshotStore(p);
    st.load();
    const s2 = richSnap();
    s2.graph.nodes.push(fileNode("apps/web/src/new.ts"));
    writeFileSync(p, JSON.stringify(s2));
    const { diff } = st.reload();
    expect(diff!.added.map((s) => s.id)).toEqual(["apps/web/src/new.ts"]);
  });
});
