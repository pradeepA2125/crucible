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
