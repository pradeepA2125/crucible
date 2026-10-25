import { describe, expect, it } from "vitest";
import { layoutGeometry } from "../desktop/layoutGeometry";
import { DEFAULT_LAYOUT, LAYOUT_BOUNDS as B } from "../../../../desktop/src/layout";
describe("desktop fit precedence", () => {
  it("reserves the chat minimum before allowing workspace width and limits live sidebar previews", () => {
    const geometry = layoutGeometry(1080, DEFAULT_LAYOUT, true, false);
    expect(geometry).toMatchObject({ compact: false, sidebarWidth: 248, sidebarMax: 288, workspaceWidth: 400 });
    expect(1080 - geometry.sidebarWidth - B.divider - geometry.workspaceWidth - B.divider).toBe(B.chatMin);
  });
  it("switches to full-area inspection from the actual pane minima, including a hidden sidebar", () => {
    expect(layoutGeometry(1039, DEFAULT_LAYOUT, true, false).isExpanded).toBe(true);
    expect(layoutGeometry(1040, DEFAULT_LAYOUT, true, false).isExpanded).toBe(false);
    expect(layoutGeometry(800, { ...DEFAULT_LAYOUT, sidebarVisible: false }, true, false).compact).toBe(false);
    expect(layoutGeometry(720, { ...DEFAULT_LAYOUT, sidebarVisible: false }, true, false).compact).toBe(true);
  });
  it("clamps a small window without mutating the remembered geometry", () => {
    const preferred = { ...DEFAULT_LAYOUT, sidebarWidth: 420, workspaceWidth: 800 };
    expect(layoutGeometry(720, preferred, true, false)).toMatchObject({ sidebarWidth: 294, compact: true });
    expect(layoutGeometry(1800, preferred, true, false)).toMatchObject({ sidebarWidth: 420, workspaceWidth: 800 });
    expect(preferred).toMatchObject({ sidebarWidth: 420, workspaceWidth: 800 });
  });
});
