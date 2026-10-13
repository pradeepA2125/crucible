export type EffortLevel = "off" | "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra";
export interface EffortOption { value: EffortLevel | null; label: string; description: string; state: "supported" | "unknown" | "unsupported" }
export interface EffortSupport {
  supported: EffortLevel[]; unsupported: Record<string, string>;
  control?: { options: (EffortOption & { value: EffortLevel })[]; source: string; default: EffortLevel | null; reason: string | null };
}
export interface EffortView { level: EffortLevel | null; support: EffortSupport | null; note?: string | null }
export const EFFORT_LABEL: Record<EffortLevel, string> = { off: "Off", none: "None", minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultra: "Ultra" };
export function effortStops(support: EffortSupport | null): EffortOption[] {
  const control = support?.control;
  if (control && control.options.length === 0) return [];
  const rows = control?.options ?? (["off", "low", "medium", "high", "max"] as EffortLevel[]).map(value => ({
    value, label: EFFORT_LABEL[value], description: support?.unsupported[value] ?? "",
    state: support?.supported.includes(value) ? "supported" as const : support?.unsupported[value] !== undefined ? "unsupported" as const : "unknown" as const,
  }));
  return [{ value: null, label: "Default", description: control?.default ? `Model default: ${EFFORT_LABEL[control.default]}` : "Let the model choose", state: "supported" },
    ...rows.filter(row => row.state !== "unsupported")];
}
