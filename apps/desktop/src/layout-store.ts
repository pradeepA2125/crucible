import { DEFAULT_LAYOUT, normalizeLayout, type DesktopLayout } from "./layout.js";
import type { JsonState } from "./store.js";
interface Stored { version: 1; layout: Omit<DesktopLayout, "groupExpanded">; groups: Record<string, boolean>; }
const record = (raw: unknown): Record<string, unknown> => raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
export class LayoutPreferences {
  constructor(private readonly state: JsonState) {}
  read(projectId: string): DesktopLayout {
    const stored = record(this.state.get("desktop.layout"));
    if (stored.version !== 1) return { ...DEFAULT_LAYOUT };
    return normalizeLayout({ ...record(stored.layout), groupExpanded: record(stored.groups)[projectId] });
  }
  async save(projectId: string, value: DesktopLayout): Promise<void> {
    const { groupExpanded, ...layout } = normalizeLayout(value);
    const previous = record(this.state.get("desktop.layout"));
    const groups = Object.fromEntries(Object.entries(record(previous.groups))
      .filter(([key, expanded]) => key !== projectId && /^[a-f0-9]{64}$/.test(key) && typeof expanded === "boolean").slice(-127));
    groups[projectId] = groupExpanded;
    const stored: Stored = { version: 1, layout, groups: groups as Record<string, boolean> };
    await this.state.update("desktop.layout", stored);
  }
}
