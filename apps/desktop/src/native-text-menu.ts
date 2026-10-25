import { Menu, type BrowserWindow, type ContextMenuParams, type MenuItemConstructorOptions, type WebContents } from "electron";
import { isAppDocument } from "./security.js";
export function textMenuItems(params: Pick<ContextMenuParams, "isEditable" | "formControlType" | "selectionText" | "dictionarySuggestions" | "editFlags">, replace: (word: string) => void): MenuItemConstructorOptions[] {
  const secret = params.formControlType === "input-password", items: MenuItemConstructorOptions[] = [];
  if (!params.isEditable && !params.selectionText) return items;
  if (params.isEditable && !secret) {
    for (const word of params.dictionarySuggestions.slice(0, 5)) items.push({ label: word, click: () => replace(word) });
    if (items.length) items.push({ type: "separator" });
  }
  if (params.isEditable) items.push({ role: "undo", enabled: params.editFlags.canUndo }, { role: "redo", enabled: params.editFlags.canRedo }, { type: "separator" },
    { role: "cut", enabled: !secret && params.editFlags.canCut });
  items.push({ role: "copy", enabled: !secret && params.editFlags.canCopy });
  if (params.isEditable) items.push({ role: "paste", enabled: params.editFlags.canPaste });
  items.push({ type: "separator" }, { role: "selectAll", enabled: params.editFlags.canSelectAll });
  return items;
}
export function installTextMenu(contents: WebContents, window: BrowserWindow): void {
  contents.on("context-menu", (_event, params) => {
    if (!isAppDocument(params.pageURL) || params.frame !== contents.mainFrame) return;
    const items = textMenuItems(params, word => contents.replaceMisspelling(word));
    if (items.length) Menu.buildFromTemplate(items).popup({ window });
  });
}
