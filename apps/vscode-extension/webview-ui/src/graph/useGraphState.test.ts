import { describe, expect, it } from "vitest";
import { graphReducer, initialGraphState, type GraphAction, type GraphUiState } from "./useGraphState";

function seq(...actions: GraphAction[]): GraphUiState {
  return actions.reduce(graphReducer, initialGraphState());
}

describe("focus stack", () => {
  it("pickPackage -> L1; pickStar -> L2; dive -> L3; pop unwinds one level", () => {
    let s = seq({ type: "pickPackage", pkg: "apps/web" });
    expect(s.focus).toEqual({ level: 1, pkg: "apps/web" });
    s = graphReducer(s, { type: "pickStar", fileId: "apps/web/src/a.ts", pkg: "apps/web" });
    expect(s.focus).toEqual({ level: 2, pkg: "apps/web", fileId: "apps/web/src/a.ts" });
    s = graphReducer(s, { type: "dive" });
    expect(s.focus.level).toBe(3);
    s = graphReducer(s, { type: "pop" });
    expect(s.focus.level).toBe(2);
    s = graphReducer(s, { type: "pop" });
    expect(s.focus.level).toBe(1);
    s = graphReducer(s, { type: "pop" });
    expect(s.focus).toEqual({ level: 0 });
  });

  it("picking a star from L0 infers its package (L2 directly)", () => {
    const s = seq({ type: "pickStar", fileId: "apps/web/src/a.ts", pkg: "apps/web" });
    expect(s.focus).toEqual({ level: 2, pkg: "apps/web", fileId: "apps/web/src/a.ts" });
  });

  it("pop from L3 with a selected symbol first clears the symbol", () => {
    let s = seq(
      { type: "pickStar", fileId: "a.ts", pkg: "" },
      { type: "dive" },
      { type: "pickSymbol", symbolId: "class:x:A" }
    );
    expect(s.focus).toEqual({ level: 3, pkg: "", fileId: "a.ts", symbolId: "class:x:A" });
    s = graphReducer(s, { type: "pop" });
    expect(s.focus).toEqual({ level: 3, pkg: "", fileId: "a.ts", symbolId: null });
  });
});

describe("edge layers", () => {
  it("References cannot be enabled below L2 and is force-disabled on pop below L2", () => {
    let s = seq({ type: "setLayer", kind: "References", on: true });
    expect(s.layers.References).toBe(false); // ignored at L0
    s = seq(
      { type: "pickStar", fileId: "a.ts", pkg: "" },
      { type: "setLayer", kind: "References", on: true }
    );
    expect(s.layers.References).toBe(true); // allowed at L2
    s = graphReducer(s, { type: "pop" });
    s = graphReducer(s, { type: "pop" });
    expect(s.focus.level).toBe(0);
    expect(s.layers.References).toBe(false); // forced off
  });
});

describe("detail plumbing", () => {
  it("fileDetail message is stored only when it matches the focused file", () => {
    let s = seq({ type: "pickStar", fileId: "a.ts", pkg: "" });
    s = graphReducer(s, {
      type: "hostFileDetail",
      detail: { fileId: "other.ts", symbols: [], edges: [], withinFileCount: 0 },
    });
    expect(s.fileDetail).toBeNull();
    s = graphReducer(s, {
      type: "hostFileDetail",
      detail: { fileId: "a.ts", symbols: [], edges: [], withinFileCount: 0 },
    });
    expect(s.fileDetail?.fileId).toBe("a.ts");
  });
});
