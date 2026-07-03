import { useEffect, useMemo, useState } from "react";
import {
  COMPONENT_LABELS,
  PROVIDERS,
  type SetupOutMsg,
} from "./types";
import { vscode } from "./vscodeApi";

type Step = "welcome" | "install" | "provider" | "done";

interface ProgressRow {
  status: string;
  detail?: string;
}

const COMPONENT_ORDER = ["uv", "agentd", "indexer", "ripgrep", "lsps"];

function statusIcon(status: string): string {
  switch (status) {
    case "running": return "⏳";
    case "done": return "✓";
    case "failed": return "✗";
    case "skipped": return "⤷";
    default: return "·";
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
    <div className="mx-auto flex max-w-xl flex-col gap-6 p-6 text-sm">
      <header>
        <h1 className="text-lg font-semibold">AI Editor Setup</h1>
        <p className="opacity-70">
          Provision the local runtime, pick a model provider, and start chatting.
        </p>
      </header>

      {step === "welcome" && (
        <section className="flex flex-col gap-4">
          <p>
            This wizard downloads the AI Editor runtime (backend, indexer, ripgrep,
            language servers) into <code>~/.ai-editor/runtime</code>.
          </p>
          <button
            className="self-start rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-500"
            onClick={startInstall}
          >
            Install runtime
          </button>
        </section>
      )}

      {step === "install" && (
        <section className="flex flex-col gap-2">
          <h2 className="font-semibold">Installing components</h2>
          <ul className="flex flex-col gap-1">
            {COMPONENT_ORDER.map((id) => {
              const row = progress[id];
              return (
                <li key={id} className="flex items-baseline gap-2">
                  <span className="w-5 text-center">{statusIcon(row?.status ?? "pending")}</span>
                  <span>{COMPONENT_LABELS[id] ?? id}</span>
                  {row?.detail && (
                    <span className="text-xs opacity-60">{row.detail}</span>
                  )}
                </li>
              );
            })}
          </ul>
          {installOk === false && (
            <button
              className="self-start rounded bg-blue-600 px-3 py-1.5 text-white hover:bg-blue-500"
              onClick={startInstall}
            >
              Retry
            </button>
          )}
        </section>
      )}

      {step === "provider" && (
        <section className="flex flex-col gap-3">
          <h2 className="font-semibold">Choose a model provider</h2>
          <label className="flex flex-col gap-1">
            <span>Provider</span>
            <select
              className="rounded border border-neutral-600 bg-transparent px-2 py-1.5"
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
          <label className="flex flex-col gap-1">
            <span>Model</span>
            <input
              className="rounded border border-neutral-600 bg-transparent px-2 py-1.5"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </label>
          {!provider.local && (
            <label className="flex flex-col gap-1">
              <span>API key ({provider.keyEnvVar})</span>
              <input
                type="password"
                className="rounded border border-neutral-600 bg-transparent px-2 py-1.5"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-…"
              />
            </label>
          )}
          {provider.local && (
            <p className="text-xs opacity-60">
              Local provider — reachability is checked when the backend starts.
            </p>
          )}
          {error && <p className="text-red-400">{error}</p>}
          <button
            className="self-start rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-500 disabled:opacity-50"
            disabled={busy || !model || (!provider.local && !apiKey)}
            onClick={saveAndStart}
          >
            {busy ? "Starting…" : "Save & Start"}
          </button>
        </section>
      )}

      {step === "done" && (
        <section className="flex flex-col gap-3">
          <h2 className="font-semibold">✓ Ready</h2>
          <p>
            Backend is running on port {port}. Provider <code>{backend}</code> /{" "}
            <code>{model}</code> validated.
          </p>
          <button
            className="self-start rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-500"
            onClick={() => vscode.postMessage({ type: "setup/openChat" })}
          >
            Open chat
          </button>
        </section>
      )}
    </div>
  );
}
