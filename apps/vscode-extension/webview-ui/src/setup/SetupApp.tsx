import { useEffect, useMemo, useState } from "react";
import { CardShell } from "../components/shared/CardShell";
import { BtnPrimary } from "../components/shared/buttons";
import { Icon } from "../components/Icon";
import { FIELD } from "../settings/ui";
import { StepRail, type Step } from "./StepRail";
import { COMPONENT_LABELS, PROVIDERS, type SetupOutMsg } from "./types";
import { vscode } from "./vscodeApi";

interface ProgressRow {
  status: string;
  detail?: string;
}

const COMPONENT_ORDER = ["uv", "agentd", "indexer", "ripgrep", "lsps"];

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

export default function SetupApp() {
  const [step, setStep] = useState<Step>("welcome");
  const [progress, setProgress] = useState<Record<string, ProgressRow>>({});
  const [installOk, setInstallOk] = useState<boolean | null>(null);
  const [backend, setBackend] = useState(PROVIDERS[0].id);
  const [model, setModel] = useState(PROVIDERS[0].defaultModel);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [port, setPort] = useState<number | null>(null);

  const provider = useMemo(
    () => PROVIDERS.find((p) => p.id === backend) ?? PROVIDERS[0],
    [backend],
  );

  useEffect(() => {
    const onMessage = (event: MessageEvent<SetupOutMsg>) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;
      switch (msg.type) {
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
          break;
        case "setup/ready":
          setBusy(false);
          setError(null);
          setPort(msg.port);
          setStep("done");
          break;
        case "setup/error":
          setBusy(false);
          setError(msg.message);
          break;
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const startInstall = () => {
    setStep("install");
    setInstallOk(null);
    setProgress({});
    setBusy(true);
    setError(null);
    vscode.postMessage({ type: "setup/install" });
  };

  const saveAndStart = () => {
    setBusy(true);
    setError(null);
    vscode.postMessage({
      type: "setup/save",
      backend,
      model,
      ...(provider.local || !apiKey ? {} : { apiKey }),
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
                color: "#fff",
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
              Everything lands in <code>~/.crucible/runtime</code> — nothing touches your system Python or PATH.
            </p>
          </CardShell>
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
            {installOk === false && (
              <div className="px-3 pb-3">
                <BtnPrimary icon="retry" onClick={startInstall}>Retry</BtnPrimary>
              </div>
            )}
          </CardShell>
        </div>
      )}

      {step === "provider" && (
        <div className="anim-section">
          <CardShell icon="key" title="Choose a model provider">
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
                    setError(null);
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
                  API key ({provider.keyEnvVar})
                  <input type="password" className={FIELD} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-…" />
                </label>
              )}
              {provider.local && (
                <p className="text-[11px] text-text-3">Local provider — reachability is checked when the backend starts.</p>
              )}
              {error && <p className="text-[11px]" style={{ color: "var(--color-red)" }}>{error}</p>}
              <BtnPrimary className="self-start" disabled={busy || !model || (!provider.local && !apiKey)} onClick={saveAndStart}>
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
                Backend is running on port {port}. Provider <code>{backend}</code> / <code>{model}</code> validated.
              </p>
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
