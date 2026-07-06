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

  const tabClass = (active: boolean) =>
    `rounded-t-md px-3.5 py-1.5 text-[13px] ${
      active ? "font-semibold" : ""
    }`;
  const tabStyle = (active: boolean): React.CSSProperties =>
    active
      ? { background: "var(--color-accent-deep)", color: "#fff" }
      : { background: "var(--color-surface-2)", color: "var(--color-text-2)" };

  return (
    <div
      className="flex h-screen flex-col text-sm"
      style={{ background: "var(--color-panel)", color: "var(--color-text)" }}
    >
      <div
        className="flex items-center gap-2 px-3 pt-2"
        style={{ borderBottom: "1px solid var(--color-border)" }}
      >
        <button className={tabClass(tab === "trace")} style={tabStyle(tab === "trace")} onClick={() => setTab("trace")}>
          Recall Trace
        </button>
        <button className={tabClass(tab === "browser")} style={tabStyle(tab === "browser")} onClick={() => setTab("browser")}>
          Browser
        </button>
        <button
          className="ml-auto mb-1"
          style={{ color: "var(--color-text-2)" }}
          onClick={() => vscode.postMessage({ type: "refresh" })}
        >
          ⟳ Refresh
        </button>
      </div>
      {error && <div className="px-3 py-1" style={{ color: "var(--color-red)" }}>{error}</div>}
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
