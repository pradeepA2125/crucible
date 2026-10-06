import { useEffect, useMemo, useRef, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { Icon } from "../../components/Icon";
import { SectionHeader } from "../SectionHeader";
import { PROVIDERS } from "../types";
import type { SettingsOutMsg } from "../types";
import { FIELD } from "../ui";
import { contextWindowError, defaultContextWindow } from "../contextWindows";
import { ChatGPTPlanPanel } from "./ChatGPTPlanPanel";
import type { SectionProps } from "./meta";

/**
 * ProviderSection — backend/model select + API key + "Save & validate".
 * Behavior is identical to the old flat page; the "✓ Saved" chip pops in
 * when a save round-trip lands a new provider snapshot.
 */
// The API-key form. ChatGPT plan usage signs in instead and has its own card above.
const KEY_PROVIDERS = PROVIDERS.filter((p) => !p.signIn);

export function ProviderSection({ state, busy, send }: SectionProps) {
  const activeKeyProvider = KEY_PROVIDERS.some((p) => p.id === state.provider?.backend);
  const [backend, setBackend] = useState(
    activeKeyProvider ? state.provider!.backend : KEY_PROVIDERS[0].id);
  const [model, setModel] = useState(
    activeKeyProvider ? state.provider!.model : KEY_PROVIDERS[0].defaultModel);
  // The live backend value wins over the table: it is what compaction is actually
  // using, and showing the table's guess over the top of it would be a lie.
  // Falls back from the SAME model string the Model field above initializes
  // from (not state.provider?.model ?? "") — otherwise the two fields silently
  // diverge the moment a table entry exists for PROVIDERS[0].defaultModel but
  // not for "" (both currently resolve to 128,000, which is why this was
  // invisible until now).
  const [contextWindow, setContextWindow] = useState(
    String(state.provider?.contextWindow ?? defaultContextWindow(model)),
  );
  // Blocks the save before it can reach the route — see the note in contextWindows.ts
  // for why a backend 422 would reach the user as an unreadable message.
  const windowError = contextWindowError(contextWindow);
  const [apiKey, setApiKey] = useState("");
  const [extraValues, setExtraValues] = useState<Record<string, string>>({});
  const [savedFlash, setSavedFlash] = useState(false);
  const [clearedFlash, setClearedFlash] = useState(false);
  // Two-step, not window.confirm: a browser modal blocks the webview's message
  // pipeline and can wedge the panel. The first click reveals the cost, the
  // second runs it.
  const [confirmingTest, setConfirmingTest] = useState(false);
  const [testResult, setTestResult] = useState<
    { ok: boolean; recalled: boolean; promptTokens?: number; exact?: boolean; error?: string } | null
  >(null);

  const provider = useMemo(
    () => KEY_PROVIDERS.find((p) => p.id === backend) ?? KEY_PROVIDERS[0],
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

  // The verdict is not part of the settings snapshot (state.provider) — it is
  // advisory, per-session, and never persisted — so it rides its own message
  // rather than a prop.
  useEffect(() => {
    const onMessage = (event: MessageEvent<SettingsOutMsg>) => {
      const msg = event.data;
      // Bare `MessageEvent` types `.data` as `any` — the `<SettingsOutMsg>`
      // parameter plus this discriminant narrow gives `msg.result` its real
      // type, matching the `no any` constraint the rest of this file follows.
      if (msg && typeof msg === "object" && msg.type === "settings/contextTestResult") {
        setConfirmingTest(false);
        setTestResult(msg.result);
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return (
    <div>
      <SectionHeader
        title="Provider"
        description="Use your ChatGPT plan, or pick a model provider with your own API key. Saving validates and hot-swaps the running backend — no restart."
      />
      <div className="mb-3">
        <ChatGPTPlanPanel state={state} busy={busy} send={send} />
      </div>
      <CardShell icon="key" title="API key provider">
        <div className="flex flex-col gap-3 px-3 pb-3 pt-1">
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Provider
            <select
              className={FIELD}
              value={backend}
              onChange={(e) => {
                const next = KEY_PROVIDERS.find((p) => p.id === e.target.value)!;
                setBackend(next.id);
                setModel(next.defaultModel);
                setContextWindow(String(defaultContextWindow(next.defaultModel)));
                setApiKey("");
                setExtraValues({});
                // The "key deleted" note is scoped to one provider — it must not
                // linger over a different slot's key field.
                setClearedFlash(false);
                // A verdict describes a specific (provider, window) pair; switching
                // provider makes it stale immediately.
                setTestResult(null);
                setConfirmingTest(false);
              }}
            >
              {KEY_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Model
            <input
              className={FIELD}
              value={model}
              onChange={(e) => {
                setModel(e.target.value);
                // A verdict describes a specific (model, window) pair; editing the
                // model out from under a green "Passphrase recalled" tick would
                // otherwise leave that tick beside a model it never tested.
                setTestResult(null);
                setConfirmingTest(false);
              }}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Context window (tokens)
            <input
              className={FIELD}
              inputMode="numeric"
              value={contextWindow}
              onChange={(e) => {
                setContextWindow(e.target.value.replace(/[^0-9]/g, ""));
                // A verdict describes the number the user has since edited away from.
                setTestResult(null);
                setConfirmingTest(false);
              }}
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
          <div className="flex flex-col gap-2">
            {!confirmingTest ? (
              <BtnGhost
                disabled={busy || windowError !== null}
                onClick={() => { setTestResult(null); setConfirmingTest(true); }}
              >
                Test
              </BtnGhost>
            ) : (
              <div className="flex flex-col gap-2 rounded-md border border-border-strong p-2">
                <p className="text-[11px] leading-relaxed text-text-3">
                  <Icon name="warn" size={11} /> This sends about{" "}
                  {Number(contextWindow).toLocaleString()} input tokens in a single
                  request — a multi-megabyte upload that may take a minute, billed
                  in full by metered providers.
                </p>
                <div className="flex items-center gap-2">
                  <BtnPrimary
                    disabled={busy}
                    onClick={() =>
                      send({
                        type: "settings/testContextWindow",
                        backend,
                        model,
                        contextWindow: Number(contextWindow),
                        ...(provider.local || !apiKey ? {} : { apiKey }),
                        ...(extraCredentials ? { extraCredentials } : {}),
                      })
                    }
                  >
                    {busy ? "Testing…" : "Run test"}
                  </BtnPrimary>
                  <BtnGhost disabled={busy} onClick={() => setConfirmingTest(false)}>
                    Cancel
                  </BtnGhost>
                </div>
              </div>
            )}
            {testResult && <TestVerdict result={testResult} />}
          </div>
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

/** Three outcomes, deliberately not collapsed into pass/fail: a call that
 * SUCCEEDS without recall is the silent-overflow case this whole feature exists
 * to expose, and it must not read as a green tick. Advisory only — nothing here
 * changes the stored value. */
function TestVerdict({ result }: {
  result: { ok: boolean; recalled: boolean; promptTokens?: number; exact?: boolean; error?: string };
}) {
  const count = result.promptTokens?.toLocaleString();
  const label = result.exact ? "sent" : "sent (estimated)";
  if (!result.ok) {
    return (
      <p className="text-xs" style={{ color: "var(--color-red)" }}>
        ✗ Test failed: {result.error ?? "unknown error"}
      </p>
    );
  }
  if (result.recalled) {
    return (
      <p className="text-xs" style={{ color: "var(--color-green)" }}>
        <Icon name="check" size={11} /> Passphrase recalled
        {count ? ` — ${count} tokens ${label}` : ""}. This window is usable.
      </p>
    );
  }
  return (
    <p className="text-xs leading-relaxed" style={{ color: "var(--color-amber)" }}>
      ⚠ The call succeeded but the passphrase came back wrong or empty
      {count ? ` — ${count} tokens ${label}` : ""}. The front of the prompt is not
      reaching the model, so this window is too large. Lower it and test again.
    </p>
  );
}
