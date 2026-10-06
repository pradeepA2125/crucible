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
  /** Human name when the model id isn't one (a ChatGPT catalog's display_name). */
  display?: string;
  active: boolean;
}

/** `chatgptCatalog` is the signed-in account's model list: with it the plan offers
 * one row per model (show display_name, send slug); without it, only what is in use. */
export function buildModelOptions(
  current: { backend: string; model: string } | null,
  keyedBackends: string[],
  providers: ProviderInfo[],
  chatgptCatalog?: { slug: string; displayName: string }[],
): ModelOption[] {
  const keyed = new Set(keyedBackends);
  return providers.flatMap((p): ModelOption[] => {
    if (p.signIn === "chatgpt" && chatgptCatalog?.length) {
      return chatgptCatalog.map((m) => ({
        backend: p.id, label: p.label, model: m.slug, display: m.displayName,
        active: current?.backend === p.id && current.model === m.slug,
      }));
    }
    if (!keyed.has(p.id) && p.id !== current?.backend) return [];
    return [{
      backend: p.id,
      label: p.label,
      model: p.id === current?.backend ? current.model : p.defaultModel,
      active: p.id === current?.backend,
    }];
  });
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
