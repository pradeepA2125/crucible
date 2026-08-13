import type { EffortSupport, ReasoningEffort } from "@crucible/editor-client";
import type { ProviderInfo } from "./setup-data.js";

// vscode-free assembly of the composer model-dropdown options. Only providers
// with a stored API key are offered (spec: never offer a hot-swap that is
// guaranteed to fail validation); the currently-active backend is always
// included — even a local/unkeyed one — since it is validated by definition.

export interface ModelOption {
  backend: string;
  label: string;
  model: string;
  active: boolean;
}

export function buildModelOptions(
  current: { backend: string; model: string } | null,
  keyedBackends: string[],
  providers: ProviderInfo[],
): ModelOption[] {
  const keyed = new Set(keyedBackends);
  return providers
    .filter((p) => keyed.has(p.id) || p.id === current?.backend)
    .map((p) => ({
      backend: p.id,
      label: p.label,
      model: p.id === current?.backend ? current.model : p.defaultModel,
      active: p.id === current?.backend,
    }));
}

export const EFFORT_LADDER: ReasoningEffort[] = ["off", "low", "medium", "high", "max"];

export interface EffortRow {
  level: ReasoningEffort;
  state: "supported" | "unsupported" | "unknown";
  reason?: string;
}

/** All five rungs, always, tagged with what we know about each.
 *
 * A null support map means the backend told us nothing (older backend, or a
 * capability lookup that failed) — that is UNKNOWN across the board, never
 * unsupported, so the chip stays usable instead of appearing broken. */
export function buildEffortRows(support: EffortSupport | null): EffortRow[] {
  return EFFORT_LADDER.map((level) => {
    if (support?.supported.includes(level)) return { level, state: "supported" as const };
    const reason = support?.unsupported[level];
    if (reason !== undefined) return { level, state: "unsupported" as const, reason };
    return { level, state: "unknown" as const };
  });
}
