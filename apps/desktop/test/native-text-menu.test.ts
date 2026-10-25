import { expect, it, vi } from "vitest";
vi.mock("electron", () => ({ Menu: {} }));
import { textMenuItems } from "../src/native-text-menu.js";
const editFlags = { canUndo: true, canRedo: false, canCut: true, canCopy: true, canPaste: true, canSelectAll: true, canDelete: true, canEditRichly: false };
it("uses standard edit roles and bounded spelling suggestions without storing selection text", () => {
  const replace = vi.fn(), items = textMenuItems({ isEditable: true, formControlType: "text-area", selectionText: "selected fixture", dictionarySuggestions: ["one", "two"], editFlags }, replace);
  expect(items.filter(item => item.role).map(item => item.role)).toEqual(["undo", "redo", "cut", "copy", "paste", "selectAll"]);
  expect(JSON.stringify(items)).not.toContain("selected fixture");
  expect(items.filter(item => item.label)).toHaveLength(2);
});
it("keeps password copying/cutting and spelling suggestions unavailable", () => {
  const items = textMenuItems({ isEditable: true, formControlType: "input-password", selectionText: "", dictionarySuggestions: ["hidden"], editFlags }, vi.fn());
  expect(items.find(item => item.role === "copy")?.enabled).toBe(false); expect(items.find(item => item.role === "cut")?.enabled).toBe(false); expect(items.some(item => item.label)).toBe(false);
});
