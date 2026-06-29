import { useEffect, useState } from "react";
import { vscode } from "./vscodeApi";
import type { HostToMemory, RecallTrace, MemoryView } from "./types";
import { RecallTraceTab } from "./RecallTraceTab";
import { BrowserTab } from "./BrowserTab";

type Tab = "trace" | "browser";

export default function MemoryApp() {
  const [tab, setTab] = useState<Tab>("trace");
  const [trace, setTrace] = useState<RecallTrace | null>(null);
  const [memories, setMemories] = useState<MemoryView[]>([]);
  const [chains, setChains] = useState<Record<string, MemoryView[]>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onMessage(ev: MessageEvent<HostToMemory>) {
      const msg = ev.data;
      if (msg.type === "trace") setTrace(msg.trace);
      else if (msg.type === "list") setMemories(msg.memories);
      else if (msg.type === "chain") setChains((c) => ({ ...c, [msg.memoryId]: msg.chain }));
      else if (msg.type === "error") setError(msg.message);
    }
    window.addEventListener("message", onMessage);
    vscode.postMessage({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return (
    <div className="flex flex-col h-screen text-sm text-[var(--vscode-foreground)]">
      <div className="flex items-center gap-2 border-b border-[var(--vscode-panel-border)] px-3 py-2">
        <button
          className={tab === "trace" ? "font-semibold underline" : "opacity-70"}
          onClick={() => setTab("trace")}
        >
          Recall Trace
        </button>
        <button
          className={tab === "browser" ? "font-semibold underline" : "opacity-70"}
          onClick={() => setTab("browser")}
        >
          Browser
        </button>
        <button
          className="ml-auto opacity-80 hover:opacity-100"
          onClick={() => vscode.postMessage({ type: "refresh" })}
        >
          ⟳ Refresh
        </button>
      </div>
      {error && <div className="px-3 py-1 text-[var(--vscode-errorForeground)]">{error}</div>}
      <div className="flex-1 overflow-auto">
        {tab === "trace" ? (
          <RecallTraceTab trace={trace} />
        ) : (
          <BrowserTab memories={memories} chains={chains} />
        )}
      </div>
    </div>
  );
}
