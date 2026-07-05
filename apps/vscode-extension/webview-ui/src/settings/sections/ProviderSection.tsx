import { useEffect, useMemo, useRef, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnPrimary } from "../../components/shared/buttons";
import { Icon } from "../../components/Icon";
import { SectionHeader } from "../SectionHeader";
import { PROVIDERS } from "../types";
import { FIELD } from "../ui";
import type { SectionProps } from "./meta";

/**
 * ProviderSection — backend/model select + API key + "Save & validate".
 * Behavior is identical to the old flat page; the "✓ Saved" chip pops in
 * when a save round-trip lands a new provider snapshot.
 */
export function ProviderSection({ state, busy, send }: SectionProps) {
  const [backend, setBackend] = useState(state.provider?.backend ?? PROVIDERS[0].id);
  const [model, setModel] = useState(state.provider?.model ?? PROVIDERS[0].defaultModel);
  const [apiKey, setApiKey] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);

  const provider = useMemo(
    () => PROVIDERS.find((p) => p.id === backend) ?? PROVIDERS[0],
    [backend],
  );

  // Flash "✓ Saved" when the active provider snapshot changes after our save.
  const providerSig = state.provider ? `${state.provider.backend}/${state.provider.model}` : "";
  const pendingSave = useRef(false);
  useEffect(() => {
    if (!pendingSave.current) return;
    pendingSave.current = false;
    setSavedFlash(true);
    const id = setTimeout(() => setSavedFlash(false), 2000);
    return () => clearTimeout(id);
  }, [providerSig]);

  return (
    <div>
      <SectionHeader
        title="Provider"
        description="Pick the model provider and model. Saving validates the credentials and hot-swaps the running backend — no restart."
      />
      <CardShell icon="key" title="Model provider">
        <div className="flex flex-col gap-3 px-3 pb-3 pt-1">
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Provider
            <select
              className={FIELD}
              value={backend}
              onChange={(e) => {
                const next = PROVIDERS.find((p) => p.id === e.target.value)!;
                setBackend(next.id);
                setModel(next.defaultModel);
                setApiKey("");
              }}
            >
              {PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Model
            <input className={FIELD} value={model} onChange={(e) => setModel(e.target.value)} />
          </label>
          {!provider.local && (
            <label className="flex flex-col gap-1 text-xs text-text-2">
              API key ({provider.keyEnvVar}) — leave blank to keep the stored key
              <input
                type="password"
                className={FIELD}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-…"
              />
            </label>
          )}
          <div className="flex items-center gap-2">
            <BtnPrimary
              disabled={busy || !model}
              onClick={() => {
                pendingSave.current = true;
                send({
                  type: "settings/setProvider",
                  backend,
                  model,
                  ...(provider.local || !apiKey ? {} : { apiKey }),
                });
              }}
            >
              {busy ? "Validating…" : "Save & validate"}
            </BtnPrimary>
            {savedFlash && (
              <span className="anim-pop flex items-center gap-1 text-[11px]" style={{ color: "var(--color-green)" }}>
                <Icon name="check" size={11} /> Saved
              </span>
            )}
          </div>
          {state.provider && (
            <p className="text-[11px] text-text-3">
              Active: <code>{state.provider.backend}</code> / <code>{state.provider.model}</code>
            </p>
          )}
        </div>
      </CardShell>
    </div>
  );
}
