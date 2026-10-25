import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_LAYOUT, normalizeLayout } from "../src/layout.js";
import { LayoutPreferences } from "../src/layout-store.js";
import { JsonState } from "../src/store.js";
import { parsePayload } from "../src/contract.js";
import { refusalMessages } from "../src/refusal.js";
const project = "a".repeat(64), other = "b".repeat(64);
describe("desktop layout preferences", () => {
  it("keeps widths app-wide and groups per project, atomically across restart", async () => {
    const directory = mkdtempSync(join(tmpdir(), "crucible-layout-")), path = join(directory, "preferences.json");
    try {
      const preferences = new LayoutPreferences(new JsonState(path));
      await preferences.save(project, { ...DEFAULT_LAYOUT, sidebarWidth: 380, sidebarVisible: false, groupExpanded: false });
      const restarted = new LayoutPreferences(new JsonState(path));
      expect(restarted.read(project)).toMatchObject({ sidebarWidth: 380, sidebarVisible: false, groupExpanded: false });
      expect(restarted.read(other)).toMatchObject({ sidebarWidth: 380, groupExpanded: true });
      await restarted.save(other, { ...restarted.read(other), workspaceWidth: 680 });
      expect(restarted.read(project)).toMatchObject({ workspaceWidth: 680, groupExpanded: false });
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });
  it("clamps corrupt or obsolete geometry and strictly validates renderer values without unlocking chat on refusal", () => {
    expect(normalizeLayout({ sidebarWidth: 5000, workspaceWidth: NaN, sidebarVisible: "yes" })).toMatchObject({ sidebarWidth: 420, workspaceWidth: 540, sidebarVisible: true });
    for (const layout of [{ ...DEFAULT_LAYOUT, sidebarWidth: 999 }, { ...DEFAULT_LAYOUT, workspaceWidth: 400.2 }, { ...DEFAULT_LAYOUT, backend: "x" }]) {
      expect(() => parsePayload({ type: "desktop/layoutSave", requestId: "layout", layout })).toThrow();
    }
    expect(refusalMessages({ type: "desktop/layoutSave", requestId: "layout" }, "retry")).toEqual([{ type: "desktop/layoutResult", requestId: "layout", error: "retry" }]);
  });
});
