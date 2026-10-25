import { LAYOUT_BOUNDS as B, clampWidth, type DesktopLayout } from "../../../../desktop/src/layout";
/** Effective geometry never writes window constraints back into preferences. */
export function layoutGeometry(viewport: number, layout: DesktopLayout, workspaceOpen: boolean, expanded: boolean) {
  const sidebarMax = Math.max(B.sidebarMin, Math.min(B.sidebarMax, viewport - B.chatMin - B.divider));
  const sidebarWidth = clampWidth(layout.sidebarWidth, B.sidebarMin, sidebarMax);
  const available = viewport - (layout.sidebarVisible ? sidebarWidth + B.divider : 0);
  const compact = available < B.chatMin + B.workspaceMin + B.divider;
  const workspaceMax = Math.max(B.workspaceMin, Math.min(B.workspaceMax, available - B.chatMin - B.divider));
  const workspaceWidth = clampWidth(layout.workspaceWidth, B.workspaceMin, workspaceMax);
  // In a split, the CSS workspace shrinks first during a sidebar drag, then stops
  // at its minimum; the chat keeps its readable minimum throughout the preview.
  const sidebarDragMax = workspaceOpen && !expanded && !compact
    ? Math.min(sidebarMax, viewport - B.chatMin - B.workspaceMin - B.divider * 2) : sidebarMax;
  return { sidebarWidth, sidebarMax: sidebarDragMax, workspaceWidth, workspaceMax, compact, isExpanded: workspaceOpen && (expanded || compact) };
}
