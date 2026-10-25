/** Desktop geometry shared by its main process and renderer; no host imports. */
export interface DesktopLayout { sidebarWidth: number; workspaceWidth: number; sidebarVisible: boolean; groupExpanded: boolean; }
export const LAYOUT_BOUNDS = { sidebarMin: 200, sidebarMax: 420, workspaceMin: 360, workspaceMax: 960, chatMin: 420, divider: 6 } as const;
export const DEFAULT_LAYOUT: DesktopLayout = { sidebarWidth: 248, workspaceWidth: 540, sidebarVisible: true, groupExpanded: true };
export const clampWidth = (value: number, min: number, max: number): number => Math.max(min, Math.min(Math.max(min, max), Math.round(value)));
export function normalizeLayout(raw: unknown): DesktopLayout {
  const input = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const width = (key: string, fallback: number, min: number, max: number) => typeof input[key] === "number" && Number.isFinite(input[key]) ? clampWidth(input[key] as number, min, max) : fallback;
  return { sidebarWidth: width("sidebarWidth", DEFAULT_LAYOUT.sidebarWidth, LAYOUT_BOUNDS.sidebarMin, LAYOUT_BOUNDS.sidebarMax),
    workspaceWidth: width("workspaceWidth", DEFAULT_LAYOUT.workspaceWidth, LAYOUT_BOUNDS.workspaceMin, LAYOUT_BOUNDS.workspaceMax),
    sidebarVisible: typeof input.sidebarVisible === "boolean" ? input.sidebarVisible : true,
    groupExpanded: typeof input.groupExpanded === "boolean" ? input.groupExpanded : true };
}
