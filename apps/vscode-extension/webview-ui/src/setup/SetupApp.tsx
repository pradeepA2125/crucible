import { useEffect, useMemo, useRef, useState } from "react";
import { CardShell } from "../components/shared/CardShell";
import { BtnGhost, BtnPrimary } from "../components/shared/buttons";
import { Icon } from "../components/Icon";
import { FIELD } from "../settings/ui";
import { ContinueWithClaudeButton } from "../components/shared/ClaudeBrand";
import { ClaudePlanPanel } from "../settings/sections/ClaudePlanPanel";
import { ChatGPTPlanPanel, PlanOffer } from "../settings/sections/ChatGPTPlanPanel";
import type { SettingsState } from "../settings/types";
import { StepRail, type Step } from "./StepRail";
import { COMPONENT_LABELS, PROVIDERS, type SetupOutMsg } from "./types";
import { vscode } from "./vscodeApi";

interface ProgressRow {
  status: string;
  detail?: string;
}

// Mirrors installer.ts's ORDER (apps/vscode-extension/src/runtime/installer.ts) —
// display-only; the actual install sequence is driven server-side regardless
// of what's listed here. Keep in sync when a component is added there.
const COMPONENT_ORDER = [
  "uv", "agentd", "indexer", "ripgrep", "rust-analyzer", "gopls", "jre", "jdtls", "lsps",
];

/** Animated per-component install glyph — same four states as the old text icons. */
function StatusGlyph({ status }: { status: string }) {
  switch (status) {
    case "running":
      return (
        <span
          className="inline-block rounded-full border-2"
          style={{
            width: 10,
            height: 10,
            borderColor: "var(--color-accent-ink) var(--accent-bg) var(--accent-bg) var(--accent-bg)",
            animation: "spin 0.75s linear infinite",
          }}
          aria-label="running"
        />
      );
    case "done":
      return (
        <span className="anim-pop inline-flex" style={{ color: "var(--color-green)" }} aria-label="done">
          <Icon name="check" size={12} />
        </span>
      );
    case "failed":
      return (
        <span className="anim-pop inline-flex" style={{ color: "var(--color-red)" }} aria-label="failed">
          <Icon name="x" size={12} />
        </span>
      );
    case "skipped":
      return (
        <span style={{ color: "var(--color-amber)" }} aria-label="skipped">
          <Icon name="chev-r" size={12} />
        </span>
      );
    default:
      return <span className="text-text-4">·</span>;
  }
}

// The API-key form. "Use your ChatGPT plan" signs in instead and has its own card.
const KEY_PROVIDERS = PROVIDERS.filter((p) => !p.signIn);

// The plan panel reads only `provider`; during setup nothing is active yet.
const NO_PROVIDER_STATE: SettingsState = {
  provider: null, runtime: null, mcp: { enabled: false, servers: [] }, skills: [],
  envFlags: {}, restartRequired: false,
};

type SetupVoicePort = { request(command: { type: "status" | "install" | "cancelInstall"; model?: "whisper-small" | "pocket" }): Promise<unknown> };
function setupVoice(): SetupVoicePort | undefined {
  return (window as unknown as { crucibleVoice?: SetupVoicePort }).crucibleVoice;
}

export default function SetupApp() {
  const [step, setStep] = useState<Step>("welcome");
  const [localModels, setLocalModels] = useState(true);
  const [voiceAvailable, setVoiceAvailable] = useState(false);
  // Spoken replies (Pocket TTS) download right after speech recognition where this computer runs them.
  const [pocketAvailable, setPocketAvailable] = useState(false);
  const [localVoice, setLocalVoice] = useState(false);
  const [voiceInstall, setVoiceInstall] = useState<"idle" | "working" | "ready" | "failed" | "cancelled">("idle");
  const [voiceStage, setVoiceStage] = useState<"whisper-small" | "pocket">("whisper-small");
  const voiceDownload = useRef(0);

  useEffect(() => {
    let current = true;
    void setupVoice()?.request({ type: "status" }).then(status => {
      if (current && typeof status === "object" && status && "available" in status) {
        setVoiceAvailable(status.available === true);
        setPocketAvailable("pocketAvailable" in status && status.pocketAvailable === true);
      }
    }, () => {});
    return () => { current = false; voiceDownload.current++; };
  }, []);

  const [runtimePath, setRuntimePath] = useState("~/.crucible/runtime");
  const [runtimeUpdate, setRuntimeUpdate] = useState<{ installed: string; bundled: string } | null>(null);
  const localModelsChosen = useRef(false);
  const [progress, setProgress] = useState<Record<string, ProgressRow>>({});
  const [installOk, setInstallOk] = useState<boolean | null>(null);
  const [backend, setBackend] = useState(KEY_PROVIDERS[0].id);
  const [model, setModel] = useState(KEY_PROVIDERS[0].defaultModel);
  // "Continue with ChatGPT": idle → starting (backend spawning) → ready (panel shown).
  const [chatgptPhase, setChatgptPhase] = useState<"idle" | "starting" | "ready">("idle");
  const [usedChatGPT, setUsedChatGPT] = useState(false);
  const [claudePhase, setClaudePhase] = useState<"idle" | "starting" | "ready">("idle");
  const [usedClaude, setUsedClaude] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [extraValues, setExtraValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [port, setPort] = useState<number | null>(null);
  const [validateWarning, setValidateWarning] = useState<string | null>(null);

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

  const extraFieldsFilled = useMemo(() => {
    if (!provider.extraFields?.length) return true;
    return provider.extraFields.every((f) => f.optional || !!extraValues[f.envVar]?.trim());
  }, [provider, extraValues]);

  useEffect(() => {
    const onMessage = (event: MessageEvent<SetupOutMsg>) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;
      switch (msg.type) {
        case "setup/claudeReady":
          setClaudePhase("ready"); setBusy(false); break;
        case "setup/progress":
          setProgress((prev) => ({
            ...prev,
            [msg.component]: { status: msg.status, detail: msg.detail },
          }));
          break;
        case "setup/installDone":
          setInstallOk(msg.ok);
          setBusy(false);
          if (msg.ok) setStep("provider");
          break;
        case "setup/validateResult":
          setBusy(false);
          setError(msg.ok ? null : msg.error ?? "validation failed");
          setValidateWarning(msg.warning ?? null);
          break;
        case "setup/chatgptReady":
          setBusy(false);
          setChatgptPhase("ready");
          // A returning user picks a saved account rather than registering a new client.
          if (!msg.hasAccounts) vscode.postMessage({ type: "settings/chatgptSignIn" });
          break;
        case "setup/preferences":
          if(msg.runtimePath)setRuntimePath(msg.runtimePath);
          setRuntimeUpdate(msg.runtimeUpdate ?? null);
          if (!localModelsChosen.current) setLocalModels(msg.localModels);
          break;
        case "setup/ready":
          setBusy(false);
          setError(null);
          setPort(msg.port);
          setValidateWarning(msg.warning ?? null);
          setStep("done");
          break;
        case "setup/error":
          setBusy(false);
          setError(msg.message);
          setChatgptPhase((phase) => (phase === "starting" ? "idle" : phase));
          setClaudePhase((phase) => (phase === "starting" ? "idle" : phase));
          break;
      }
    };
    window.addEventListener("message", onMessage);
    vscode.postMessage({ type: "setup/preferences" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const startInstall = () => {
    localModelsChosen.current = true;
    setStep("install");
    setInstallOk(null);
    setProgress({});
    setBusy(true);
    setError(null);
    vscode.postMessage({ type: "setup/install", localModels });
    if (localVoice && voiceInstall !== "working" && voiceInstall !== "ready") {
      const port = setupVoice();
      if (!port) return;
      const id = ++voiceDownload.current;
      setVoiceInstall("working");
      setVoiceStage("whisper-small");
      void port.request({ type: "install", model: "whisper-small" }).then(async () => {
        if (id !== voiceDownload.current) return;
        if (pocketAvailable) {
          setVoiceStage("pocket");
          await port.request({ type: "install", model: "pocket" });
        }
        if (id === voiceDownload.current) setVoiceInstall("ready");
      }).catch(() => { if (id === voiceDownload.current) setVoiceInstall("failed"); });
    }

  };

  const continueWithChatGPT = () => {
    setUsedClaude(false);
    setBusy(true);
    setError(null);
    setUsedChatGPT(true);
    setChatgptPhase("starting");
    vscode.postMessage({ type: "setup/startForChatGPT" });
  };

  const saveAndStart = () => {
    setBusy(true);
    setError(null);
    setUsedChatGPT(false);
    setUsedClaude(false);
    setValidateWarning(null);
    vscode.postMessage({
      type: "setup/save",
      backend,
      model,
      ...(provider.local || !apiKey ? {} : { apiKey }),
      ...(extraCredentials ? { extraCredentials } : {}),
    });
  };

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-4 p-6 text-sm">
      <StepRail current={step} />

      {step === "welcome" && (
        <div className="anim-section flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <span
              className="flex h-11 w-11 items-center justify-center rounded-[12px]"
              style={{
                background: "linear-gradient(180deg, var(--color-accent-deep), var(--color-accent-hot))",
                color: "var(--on-accent)",
                animation: "breathe 2.4s ease-in-out infinite",
              }}
            >
              <Icon name="spark" size={20} />
            </span>
            <div>
              <h1 className="text-base font-semibold text-text">Crucible Setup</h1>
              <p className="text-xs text-text-3">Provision the local runtime, pick a model provider, start chatting.</p>
            </div>
          </div>
          {runtimeUpdate && (
            <p role="status" className="surface-card px-3 py-2 text-xs text-text-2">
              Crucible was updated. Its runtime is still {runtimeUpdate.installed}; this version needs {runtimeUpdate.bundled}.
              Install it to continue. Your projects, conversations and settings are kept.
            </p>
          )}
          <CardShell icon="chip" title="What gets installed">
            <ul className="flex flex-col px-3 pb-3 pt-1">
              {COMPONENT_ORDER.map((id, i) => (
                <li
                  key={id}
                  className="anim-section flex items-center gap-2 border-b py-2 text-xs text-text-2 last:border-b-0"
                  style={{ borderColor: "var(--hairline)", animationDelay: `${i * 45}ms` }}
                >
                  <Icon name="check" size={10} className="text-text-4" />
                  {COMPONENT_LABELS[id] ?? id}
                </li>
              ))}
            </ul>
            <p className="px-3 pb-3 text-[11px] text-text-3">
              Everything lands in <code className="break-all">{runtimePath}</code> — nothing touches your system Python or PATH.
            </p>
          </CardShell>
          <BtnPrimary className="self-start" icon="chev-r" onClick={() => setStep("models")}>
            Continue
          </BtnPrimary>
        </div>
      )}

      {step === "models" && (
        <div className="anim-section flex flex-col gap-4">
          <CardShell icon="chip" title="Local models">
            <label className="flex items-center gap-3 px-3 py-3 text-sm">
              <input type="checkbox" checked={localModels}
                onChange={event => {
                  localModelsChosen.current = true;
                  setLocalModels(event.target.checked);
                }} />
              Better code and memory search (recommended)
            </label>
            <p className="px-3 pb-3 text-xs text-text-3">
              About 1 GB download. Runs on this machine. With this off, searches use keywords.
              You can change the choice later in Settings.
            </p>
          </CardShell>
          {voiceAvailable && <CardShell icon="spark" title="Local voice">
            <label className="flex items-center gap-3 px-3 py-3 text-sm">
              <input type="checkbox" checked={localVoice} onChange={event => setLocalVoice(event.target.checked)} />
              Enable local voice
            </label>
            <p className="px-3 pb-3 text-xs text-text-3">
              {pocketAvailable ? "Optional download: speech recognition (465 MiB) and spoken replies with Pocket TTS."
                : "Optional 465 MiB speech recognition download."} Microphone access is requested when you press Voice in a conversation.
            </p>
          </CardShell>}
          <BtnPrimary className="self-start" icon="bolt" onClick={startInstall}>
            Install runtime
          </BtnPrimary>
        </div>
      )}
      {step === "install" && (
        <div className="anim-section">
          <CardShell icon="chip" title="Installing components">
            <ul className="flex flex-col px-3 pb-3 pt-1">
              {COMPONENT_ORDER.map((id) => {
                const row = progress[id];
                return (
                  <li key={id} className="flex items-center gap-2.5 border-b py-2 last:border-b-0" style={{ borderColor: "var(--hairline)" }}>
                    <span className="flex w-4 justify-center"><StatusGlyph status={row?.status ?? "pending"} /></span>
                    <span className="text-xs text-text">{COMPONENT_LABELS[id] ?? id}</span>
                    {row?.detail && <span className="min-w-0 flex-1 truncate text-[11px] text-text-3">{row.detail}</span>}
                  </li>
                );
              })}
            </ul>
            {/* A refused or failed install (another project still working, a download error)
                must say so here: the rows alone would sit at pending forever. */}
            {error && !busy && (
              <p role="alert" className="px-3 pb-2 text-[11px]" style={{ color: "var(--color-red)" }}>{error}</p>
            )}
            {(installOk === false || (error && !busy)) && (
              <div className="px-3 pb-3">
                <BtnPrimary icon="retry" onClick={startInstall}>Retry</BtnPrimary>
              </div>
            )}
          </CardShell>
        </div>
      )}

      {voiceInstall !== "idle" && (step === "install" || step === "provider") &&
        <div className="flex items-center gap-3 rounded-control border border-border px-3 py-2 text-meta text-text-2" role="status">
          <span>{voiceInstall === "working"
            ? voiceStage === "pocket" ? "Downloading spoken replies (Pocket TTS, 2 of 2)…"
              : pocketAvailable ? "Downloading speech recognition (Whisper Small, 1 of 2)…" : "Downloading speech recognition (Whisper Small)…"
            : voiceInstall === "ready" ? "Local voice is ready."
              : voiceInstall === "cancelled" ? "Voice download cancelled. You can download the models later in Settings › Voice."
                : "Voice download failed. You can retry from Settings › Voice, or press Voice in a conversation."}</span>
          {voiceInstall === "working" && <BtnGhost onClick={() => {
            voiceDownload.current++;
            setVoiceInstall("cancelled");
            void setupVoice()?.request({ type: "cancelInstall" }).catch(() => {});
          }}>Cancel voice download</BtnGhost>}
        </div>}

      {step === "provider" && (
        <div className="anim-section flex flex-col gap-3">
          {chatgptPhase === "idle" && (
            <CardShell icon="spark" iconColor="var(--color-text)" title="ChatGPT plan">
              <div className="px-3 pb-3 pt-1">
                <PlanOffer pending={false} busy={busy} onContinue={continueWithChatGPT} />
              </div>
            </CardShell>
          )}
          {chatgptPhase === "starting" && (
            <p className="text-xs text-text-2" role="status">Starting Crucible for ChatGPT sign-in…</p>
          )}
          {chatgptPhase === "ready" && (
            <ChatGPTPlanPanel state={NO_PROVIDER_STATE} busy={busy}
                              send={(m) => vscode.postMessage(m)} />
          )}
          {claudePhase === "idle" && <CardShell icon="spark" title="Claude subscription">
            <div className="px-3 pb-3 pt-1">
              <p className="mb-2 text-xs text-text-2">Experimental connection using your Claude subscription.</p>
              <ContinueWithClaudeButton disabled={busy} onClick={() => {
                setUsedClaude(true); setUsedChatGPT(false); setClaudePhase("starting"); setBusy(true);
                vscode.postMessage({ type: "setup/startForClaude" });
              }} />
            </div>
          </CardShell>}
          {claudePhase === "starting" && <p className="text-xs text-text-2" role="status">Starting Crucible for Claude sign-in…</p>}
          {claudePhase === "ready" && <ClaudePlanPanel state={NO_PROVIDER_STATE} busy={busy} send={m => vscode.postMessage(m)} />}
          <CardShell icon="key" title="Or use your own API key">
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
                      setApiKey("");
                      setExtraValues({});
                      setError(null);
                      setValidateWarning(null);
                    }}
                >
                  {KEY_PROVIDERS.map((p) => (
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
                  API key ({provider.keyEnvVar})
                  <input type="password" className={FIELD} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-…" />
                </label>
              )}
              {provider.extraFields?.map((f) => (
                <label key={f.envVar} className="flex flex-col gap-1 text-xs text-text-2">
                  {f.label} ({f.envVar})
                  <input
                    className={FIELD}
                    value={extraValues[f.envVar] ?? ""}
                    onChange={(e) => setExtraValues((prev) => ({ ...prev, [f.envVar]: e.target.value }))}
                    placeholder={f.placeholder}
                  />
                </label>
              ))}
              {provider.local && (
                <p className="text-[11px] text-text-3">Local provider — reachability is checked when the backend starts.</p>
              )}
              {error && <p className="text-[11px]" style={{ color: "var(--color-red)" }}>{error}</p>}
              {validateWarning && (
                <p className="text-xs" style={{ color: "var(--color-amber)" }}>⚠ {validateWarning}</p>
              )}
              <BtnPrimary
                className="self-start"
                disabled={busy || !model || (!provider.local && !provider.keyOptional && !apiKey) || !extraFieldsFilled}
                onClick={saveAndStart}
              >
                {busy ? "Starting…" : "Save & Start"}
              </BtnPrimary>
            </div>
          </CardShell>
        </div>
      )}

      {step === "done" && (
        <div className="anim-section">
          <CardShell icon="check" iconColor="var(--color-green)" title="Ready" borderColor="var(--green-brd)">
            <div className="flex flex-col items-start gap-3 px-3 pb-3 pt-1">
              <p className="text-xs text-text-2">
                {usedClaude ? <>Backend is running on port {port}, using your Claude subscription.</> : usedChatGPT
                  ? <>Backend is running on port {port}, using your ChatGPT plan.</>
                  : <>Backend is running on port {port}. Provider <code>{backend}</code> / <code>{model}</code> validated.</>}
              </p>
              {validateWarning && (
                <p className="text-xs" style={{ color: "var(--color-amber)" }}>⚠ {validateWarning}</p>
              )}
              <BtnPrimary icon="send" onClick={() => vscode.postMessage({ type: "setup/openChat" })}>
                Open chat
              </BtnPrimary>
            </div>
          </CardShell>
        </div>
      )}
    </div>
  );
}
