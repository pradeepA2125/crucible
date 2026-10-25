export type WorkspaceTab = { id: string; kind: "new" } | { id: string; kind: "files" } | { id: string; kind: "agents" } | { id: string; kind: "terminal" } | { id: string; kind: "changes" } | { id: string; kind: "file"; path: string } | { id: string; kind: "inspection"; inspectionId: string; label: string };
export type WorkspaceTool = "files" | "changes" | "agents" | "terminal";
export interface WorkspaceTabs { tabs: WorkspaceTab[]; active: string; serial: number; notice?: string; }
export type WorkspaceTabAction = { type: "new" } | { type: "tool"; kind: WorkspaceTool } | { type: "file"; path: string } | { type: "select" | "close"; id: string } | { type: "syncFiles"; paths: string[] } | { type: "panels"; panels: { id: string; label: string }[] };
export const INITIAL_WORKSPACE_TABS: WorkspaceTabs = { tabs: [{ id: "new:1", kind: "new" }], active: "new:1", serial: 1 };
function available(state: WorkspaceTabs, tabs: WorkspaceTab[], active = state.active): WorkspaceTabs {
  if (!tabs.length) { const id = `new:${state.serial + 1}`; return { tabs: [{ id, kind: "new" }], active: id, serial: state.serial + 1 }; }
  return { ...state, tabs, active: tabs.some(tab => tab.id === active) ? active : tabs.at(-1)!.id, notice: undefined };
}
function open(state: WorkspaceTabs, tab: WorkspaceTab): WorkspaceTabs {
  const tabs = state.tabs.filter(item => item.id !== state.active || item.kind !== "new");
  if (!tabs.some(item => item.id === tab.id)) {
    if (tabs.length >= 20) return { ...state, notice: "Close a workspace tab before opening another (20 maximum)." };
    tabs.push(tab);
  }
  return available(state, tabs, tab.id);
}
export function workspaceTabs(state: WorkspaceTabs, action: WorkspaceTabAction): WorkspaceTabs {
  switch (action.type) {
    case "new": {
      if (state.tabs.length >= 20) return { ...state, notice: "Close a workspace tab before opening another (20 maximum)." };
      const id = `new:${state.serial + 1}`;
      return { tabs: [...state.tabs, { id, kind: "new" }], active: id, serial: state.serial + 1 };
    }
    case "tool": return open(state, { id: action.kind, kind: action.kind });
    case "file": return open(state, { id: `file:${action.path}`, kind: "file", path: action.path });
    case "select": return state.tabs.some(tab => tab.id === action.id) ? { ...state, active: action.id, notice: undefined } : state;
    case "close": return available(state, state.tabs.filter(tab => tab.id !== action.id));
    case "syncFiles": {
      const tabs = state.tabs.filter(tab => tab.kind !== "file" || action.paths.includes(tab.path));
      return tabs.length === state.tabs.length ? state : available(state, tabs);
    }
    case "panels": {
      let next = available(state, state.tabs.filter(tab => tab.kind !== "inspection" || action.panels.some(panel => panel.id === tab.inspectionId)));
      for (const panel of action.panels) if (!next.tabs.some(tab => tab.id === `inspection:${panel.id}`)) next = open(next, { id: `inspection:${panel.id}`, kind: "inspection", inspectionId: panel.id, label: panel.label });
      return next;
    }
  }
}
