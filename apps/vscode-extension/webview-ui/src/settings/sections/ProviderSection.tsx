import { useEffect, useMemo, useRef, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { Icon } from "../../components/Icon";
import { SectionHeader } from "../SectionHeader";
import { PROVIDERS } from "../types";
import { FIELD } from "../ui";
import { contextWindowError, defaultContextWindow } from "../contextWindows";
import type { SectionProps } from "./meta";

/**
 * ProviderSection — backend/model select + API key + "Save & validate".
 * Behavior is identical to the old flat page; the "✓ Saved" chip pops in
 * when a save round-trip lands a new provider snapshot.
 */
export function ProviderSection({ state, busy, send }: SectionProps) {
  const [backend, setBackend] = useState(state.provider?.backend ?? PROVIDERS[0].id);
  const [model, setModel] = useState(state.provider?.model ?? PROVIDERS[0].defaultModel);
  // The live backend value wins over the table: it is what compaction is actually
  // using, and showing the table's guess over the top of it would be a lie.
  const [contextWindow, setContextWindow] = useState(
    String(state.provider?.contextWindow ?? defaultContextWindow(state.provider?.model ?? "")),
  );
  // Blocks the save before it can reach the route — see the note in contextWindows.ts
  // for why a backend 422 would reach the user as an unreadable message.
  const windowError = contextWindowError(contextWindow);
  const [apiKey, setApiKey] = useState("");
  const [extraValues, setExtraValues] = useState<Record<string, string>>({});
  const [savedFlash, setSavedFlash] = useState(false);
  const [clearedFlash, setClearedFlash] = useState(false);

  const provider = useMemo(
    () => PROVIDERS.find((p) => p.id === backend) ?? PROVIDERS[0],
    [backend],
  );

  const extraCredentials = useMemo(() => {
    if (!provider.extraFields?.length) return undefined;
    const creds: Record<string, string> = {};
    for (const f of provider.extraFields) {
      if (extraValues[f.envVar]) creds[f.envVar] = extraValues[f.envVar];
    }
    return Object.keys(creds).length ? creds : undefined;
  }, [provider, extraValues]);

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
                setContextWindow(String(defaultContextWindow(next.defaultModel)));
                setApiKey("");
                setExtraValues({});
                // The "key deleted" note is scoped to one provider — it must not
                // linger over a different slot's key field.
                setClearedFlash(false);
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
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Context window (tokens)
            <input
              className={FIELD}
              inputMode="numeric"
              value={contextWindow}
              onChange={(e) => setContextWindow(e.target.value.replace(/[^0-9]/g, ""))}
              placeholder="128000"
            />
          </label>
          {windowError && (
            <p className="text-xs" style={{ color: "var(--color-red)" }}>{windowError}</p>
          )}
          <p className="text-[11px] leading-relaxed text-text-3">
            Take this from the model's own model card or the provider's
            documentation — it cannot be detected, and a remembered number is
            usually wrong.{" "}
            <strong>Too small</strong> and history is evicted (and a summary paid
            for) while the window is still half empty — wasteful, but safe.{" "}
            <strong>Too large</strong> and the prompt overruns the real window: some
            providers return no error at all, bill every token, and answer with
            nothing. Use Test to confirm.
          </p>
          {provider.keyEnvVar && (
            <>
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
              {/* A blank field keeps the stored key, so removing one has to be
                  explicit. This matters most for openai_compatible, whose key follows
                  the provider SLOT rather than the Base URL: retargeting the endpoint
                  would otherwise keep sending the previous host's token. Scoped to the
                  provider currently selected in the dropdown — the same one whose env
                  var the label above names. */}
              <div className="flex items-center gap-2">
                <BtnGhost
                  disabled={busy}
                  onClick={() => {
                    setApiKey("");
                    setClearedFlash(true);
                    send({ type: "settings/clearProviderKey", backend });
                  }}
                >
                  Clear key
                </BtnGhost>
                {clearedFlash && (
                  <span className="anim-pop text-[11px] text-text-3">
                    Stored key deleted — restart the backend to stop sending it.
                  </span>
                )}
              </div>
            </>
          )}
          {provider.extraFields?.map((f) => (
            <label key={f.envVar} className="flex flex-col gap-1 text-xs text-text-2">
              {f.label} ({f.envVar}) — leave blank to keep the stored value
              <input
                className={FIELD}
                value={extraValues[f.envVar] ?? ""}
                onChange={(e) => setExtraValues((prev) => ({ ...prev, [f.envVar]: e.target.value }))}
                placeholder={f.placeholder}
              />
            </label>
          ))}
          <div className="flex items-center gap-2">
            <BtnPrimary
              disabled={busy || !model || windowError !== null}
              onClick={() => {
                pendingSave.current = true;
                setClearedFlash(false);
                send({
                  type: "settings/setProvider",
                  backend,
                  model,
                  contextWindow: Number(contextWindow),
                  ...(provider.local || !apiKey ? {} : { apiKey }),
                  ...(extraCredentials ? { extraCredentials } : {}),
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
          {state.providerWarning && (
            <p className="text-xs" style={{ color: "var(--color-amber)" }}>⚠ {state.providerWarning}</p>
          )}
        </div>
      </CardShell>
    </div>
  );
}
