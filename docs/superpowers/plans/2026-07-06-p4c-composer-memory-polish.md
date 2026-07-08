# P4-C: Memory Inspector Polish + Composer Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out the copilot-parity roadmap's remaining P4-C scope: reskin the memory inspector onto the shared design tokens with a chat shortcut to open it, and add `@`-file mentions + a unified `/`-autocomplete dropdown to the chat composer.

**Architecture:** Two independent slices sharing no code. Memory polish is a pure CSS/JSX token-swap plus one new header button wired through the existing `ChatPanel` message-routing pattern. Composer intelligence adds one small shared trigger-detection layer (`composerTrigger.ts` + `TriggerDropdown.tsx`) consumed by two independent behaviors in `InputArea.tsx`; `@`-mentions additionally thread a new optional field through the extension host → editor-client → backend route → `ChatController.handle_message`, which folds mentioned file content into the current turn's text only (never persisted, never a system-prompt/dynamic-tail change).

**Tech Stack:** TypeScript/React (webview-ui, Vitest + Testing Library), TypeScript (VS Code extension host, Vitest), TypeScript (editor-client, Vitest), Python (agentd-py, pytest + pytest-asyncio).

## Global Constraints

- No new env vars for either slice (per the approved specs — the mention file-size cap is a fixed constant, not a config knob).
- `controller.ts` (VS Code extension) stays vscode-API-free; anything needing `vscode.workspace`/`vscode.window` lives in `extension.ts` as a closure, wired into `ChatPanel`'s constructor the same way `onOpenSettings` already is.
- `mentioned_files` folding is **controller-only** (`ChatController`, not the legacy `ChatAgent`) — matches the existing controller-only convention for `write_doc`/MCP/skills.
- Reuse existing `--color-*` CSS custom properties and `.menu-item`/`.surface-card` primitives from `webview-ui/src/index.css` — no new hex colors introduced anywhere in this plan.
- Follow existing test conventions exactly: `vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }))` for webview tests, `ScriptedReasoningEngine` + `ChatThreadStore(tmp_path / "chat.sqlite3")` for Python controller tests.

---

### Task 1: Memory inspector palette migration

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/memory/MemoryApp.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/memory/BrowserTab.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/memory/RecallTraceTab.tsx`

**Interfaces:** None — visual-only, no prop/type changes. Existing tests (`MemoryApp.test.tsx`, `BrowserTab.test.tsx`, `RecallTraceTab.test.tsx`) contain no hardcoded-hex assertions (verified), so they continue to pass unchanged.

Token mapping used throughout (from `webview-ui/src/index.css`):

| Old hex | New token |
|---|---|
| `#0b1220` (page bg) | `var(--color-panel)` |
| `#111827` (card/list bg) | `var(--color-surface)` |
| `#1f2937` (inactive tab bg) | `var(--color-surface-2)` |
| `#0f172a` (chip/filter bg) | `var(--color-surface-2)` |
| `#1e293b` (border) | `var(--color-border)` |
| `#334155` (border strong) | `var(--color-border-strong)` |
| `#2563eb` (selected/active) | `var(--color-accent-deep)` |
| `#cbd5e1`, `#e2e8f0` (text) | `var(--color-text)` |
| `#94a3b8`, `#9ca3af` (text-2) | `var(--color-text-2)` |
| `#64748b` (text-3) | `var(--color-text-3)` |
| `#fca5a5` (retired/error) | `var(--color-red)` |
| `#6ee7b7`, `#34d399` (live/success) | `var(--color-green)` |
| `#7f1d1d` bg + border → | `var(--red-bg)` bg, `var(--red-brd)` border |
| `#065f46` bg + border → | `var(--green-bg)` bg, `var(--green-brd)` border |
| `#93c5fd` (entity tag text) | `var(--color-code)` |
| `#3b82f6` (signal bar) | `var(--color-code)` |
| `#8b5cf6` (signal bar, importance) | `var(--color-accent-deep)` |
| `#10b981` (signal bar, recency) | `var(--color-green)` |
| Kind fills (`#1d4ed8`/`#6d28d9`/`#0e7490`) | filled pill: bg = tint token, text = `var(--color-panel)` (dark-on-light — these tokens are pastel, matching the settings-nav tint convention in `meta.ts`) |

- [ ] **Step 1: Rewrite `MemoryApp.tsx` onto tokens**

```tsx
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
```

- [ ] **Step 2: Rewrite `BrowserTab.tsx` onto tokens**

```tsx
import { useState } from "react";
import { vscode } from "./vscodeApi";
import type { MemoryView } from "./types";

// Filled kind pills reuse the shared tint tokens (settings nav convention, meta.ts) —
// pastel bg + dark text (var(--color-panel)) rather than a one-off saturated hex.
const KIND_TINT: Record<string, string> = {
  semantic: "var(--color-code)",
  procedural: "var(--color-accent)",
  episodic: "var(--color-green)",
};
const KINDS = ["all", "episodic", "semantic", "procedural"] as const;
const SCOPES = ["workspace", "thread"] as const;

function isRetired(m: MemoryView): boolean {
  return m.validTo !== null;
}

function KindBadge({ kind }: { kind: string }) {
  return (
    <span
      className="rounded px-1.5 py-px text-[10px] font-semibold"
      style={{ background: KIND_TINT[kind] ?? "var(--color-text-3)", color: "var(--color-panel)" }}
    >
      {kind}
    </span>
  );
}

export function BrowserTab({
  memories,
  chains,
}: {
  memories: MemoryView[];
  chains: Record<string, MemoryView[]>;
}) {
  const [scopeKind, setScopeKind] = useState<string>("workspace");
  const [kind, setKind] = useState<string>("all");
  const [includeRetired, setIncludeRetired] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  function emitBrowse(next: { scopeKind?: string; kind?: string; includeRetired?: boolean }) {
    vscode.postMessage({
      type: "browse",
      filter: {
        scopeKind: next.scopeKind ?? scopeKind,
        scopeId: "",
        kind: (next.kind ?? kind) === "all" ? undefined : next.kind ?? kind,
        includeRetired: next.includeRetired ?? includeRetired,
      },
    });
  }

  function select(id: string) {
    setSelected(id);
    vscode.postMessage({ type: "loadChain", memoryId: id });
  }

  const liveCount = memories.filter((m) => !isRetired(m)).length;
  const retiredCount = memories.length - liveCount;
  const detail = memories.find((m) => m.id === selected) ?? null;
  const chain = selected ? chains[selected] ?? null : null;

  return (
    <div
      data-testid="memory-browser-tab"
      className="flex h-full flex-col"
      style={{ background: "var(--color-panel)", color: "var(--color-text)" }}
    >
      <div
        className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-[12px]"
        style={{ borderBottom: "1px solid var(--color-border)" }}
      >
        <label className="flex items-center gap-1 rounded-md px-2.5 py-1" style={{ background: "var(--color-surface-2)" }}>
          scope:
          <select
            className="bg-transparent outline-none"
            style={{ color: "var(--color-text)" }}
            value={scopeKind}
            onChange={(e) => {
              setScopeKind(e.target.value);
              emitBrowse({ scopeKind: e.target.value });
            }}
          >
            {SCOPES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 rounded-md px-2.5 py-1" style={{ background: "var(--color-surface-2)" }}>
          kind:
          <select
            className="bg-transparent outline-none"
            style={{ color: "var(--color-text)" }}
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              emitBrowse({ kind: e.target.value });
            }}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 rounded-md px-2.5 py-1" style={{ background: "var(--color-surface-2)" }}>
          <input
            type="checkbox"
            checked={includeRetired}
            onChange={(e) => {
              setIncludeRetired(e.target.checked);
              emitBrowse({ includeRetired: e.target.checked });
            }}
          />
          include retired
        </label>
        <span className="ml-auto" style={{ color: "var(--color-text-2)" }}>
          {liveCount} live · {retiredCount} retired
        </span>
      </div>

      <div className="grid min-h-0 flex-1 gap-4 p-4" style={{ gridTemplateColumns: "1.1fr 1fr" }}>
        <ul data-testid="memory-list" className="flex flex-col gap-2 overflow-auto text-[12px]">
          {memories.map((m) => {
            const retired = isRetired(m);
            const isSel = selected === m.id;
            return (
              <li
                key={m.id}
                onClick={() => select(m.id)}
                className={`cursor-pointer rounded-lg border p-2.5 leading-snug ${retired ? "opacity-50" : ""}`}
                style={{
                  borderColor: isSel ? "var(--color-accent-deep)" : "var(--color-border-strong)",
                  background: isSel ? "var(--color-panel)" : "var(--color-surface)",
                }}
              >
                <div className="mb-1 flex items-center gap-1.5">
                  <KindBadge kind={m.kind} />
                  {retired ? (
                    <span style={{ color: "var(--color-red)" }}>retired</span>
                  ) : (
                    <span style={{ color: "var(--color-text-2)" }}>imp {m.importance}</span>
                  )}
                </div>
                <div
                  className={`line-clamp-2 ${retired ? "line-through" : ""}`}
                  style={{ color: retired ? "var(--color-text-2)" : "var(--color-text)" }}
                >
                  {m.content}
                </div>
              </li>
            );
          })}
        </ul>

        <div
          data-testid="memory-detail"
          className="overflow-auto rounded-lg border p-4 text-[12px]"
          style={{ borderColor: "var(--color-border-strong)", background: "var(--color-panel)" }}
        >
          {detail ? (
            <>
              <div className="mb-3">
                <KindBadge kind={detail.kind} />
                {isRetired(detail) ? (
                  <span
                    className="ml-1 rounded px-[7px] py-px text-[11px]"
                    style={{ background: "var(--red-bg)", border: "1px solid var(--red-brd)", color: "var(--color-red)" }}
                  >
                    retired
                  </span>
                ) : (
                  <span
                    className="ml-1 rounded px-[7px] py-px text-[11px]"
                    style={{ background: "var(--green-bg)", border: "1px solid var(--green-brd)", color: "var(--color-green)" }}
                  >
                    live
                  </span>
                )}
              </div>
              <div className="mb-3 whitespace-pre-wrap leading-relaxed" style={{ color: "var(--color-text)" }}>
                {detail.content}
              </div>
              {detail.entities.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-1.5">
                  {detail.entities.map((e) => (
                    <span
                      key={e}
                      className="rounded-[10px] px-[7px] py-0.5 text-[11px]"
                      style={{ background: "var(--color-surface-3)", color: "var(--color-code)" }}
                    >
                      {e}
                    </span>
                  ))}
                </div>
              )}
              <div className="leading-[1.7]" style={{ color: "var(--color-text-2)" }}>
                <b>importance</b> {detail.importance} &nbsp;·&nbsp; <b>scope</b> {detail.scopeKind}
                <br />
                <b>source</b> {detail.sourceKind} · run {detail.sourceRef}
                <br />
                <b>valid_from</b> {detail.validFrom} &nbsp;·&nbsp; <b>segments</b> seq{" "}
                {detail.sourceSeqLo ?? "—"}–{detail.sourceSeqHi ?? "—"}
                {isRetired(detail) && (
                  <>
                    <br />
                    <b>valid_to</b> <span style={{ color: "var(--color-red)" }}>{detail.validTo}</span>
                  </>
                )}
              </div>

              {chain && chain.length > 1 && (
                <div className="mt-4 pt-3" style={{ borderTop: "1px solid var(--color-border)" }}>
                  <div className="mb-2 text-[11px] tracking-wide" style={{ color: "var(--color-text-2)" }}>
                    SUPERSEDE CHAIN
                  </div>
                  <div data-testid="supersede-chain" className="flex flex-col gap-1.5" style={{ color: "var(--color-text)" }}>
                    {chain.map((c, i) => (
                      <div key={c.id} data-testid={`chain-node-${c.id}`}>
                        {isRetired(c) ? (
                          <span className="opacity-60">
                            <span className="line-through">{c.content}</span>{" "}
                            <span style={{ color: "var(--color-red)" }}>retired</span>
                          </span>
                        ) : (
                          <span>
                            <b>{c.content}</b> <span style={{ color: "var(--color-green)" }}>live</span>
                          </span>
                        )}
                        {i < chain.length - 1 && (
                          <div className="ml-1.5" style={{ color: "var(--color-text-3)" }}>↓ superseded_by</div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <span style={{ color: "var(--color-text-3)" }}>Select a memory to inspect.</span>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Rewrite `RecallTraceTab.tsx` onto tokens**

```tsx
import type { RecallTrace, RecallTraceEntry, RecallSignals } from "./types";

const KIND_TINT: Record<string, string> = {
  semantic: "var(--color-code)",
  procedural: "var(--color-accent)",
  episodic: "var(--color-green)",
};

const SIGNALS: { key: keyof RecallSignals; label: string; color: string }[] = [
  { key: "semantic", label: "semantic", color: "var(--color-code)" },
  { key: "lexical", label: "lexical", color: "var(--color-code)" },
  { key: "structural", label: "structural", color: "var(--color-code)" },
  { key: "importance", label: "importance", color: "var(--color-accent-deep)" },
  { key: "recency", label: "recency", color: "var(--color-green)" },
];

function KindBadge({ kind }: { kind: string }) {
  return (
    <span
      className="rounded px-[7px] py-px text-[11px] font-semibold"
      style={{ background: KIND_TINT[kind] ?? "var(--color-text-3)", color: "var(--color-panel)" }}
    >
      {kind}
    </span>
  );
}

function SignalCell({ label, value, color }: { label: string; value: number; color: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div data-signal={label} className="flex flex-col gap-1">
      <span className="flex justify-between text-[10px]" style={{ color: "var(--color-text-2)" }}>
        <span>{label}</span>
        <span className="tabular-nums" style={{ color: "var(--color-text)" }}>{value.toFixed(2)}</span>
      </span>
      <div className="h-1.5 rounded-full" style={{ background: "var(--color-border)" }}>
        <div className="h-1.5 rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function Entry({
  entry,
  reranked,
  fusedRank,
}: {
  entry: RecallTraceEntry;
  reranked: boolean;
  fusedRank: number;
}) {
  let rankSuffix = "";
  if (reranked && fusedRank !== entry.finalRank) {
    rankSuffix = ` ${entry.finalRank < fusedRank ? "▲" : "▼"} from #${fusedRank + 1}`;
  }

  return (
    <div
      data-testid={`trace-entry-${entry.memoryId}`}
      className={`mb-3 rounded-lg border p-3.5 ${entry.injected ? "" : "opacity-55"}`}
      style={{ borderColor: "var(--color-border-strong)", background: "var(--color-surface)" }}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <span className="min-w-0 flex items-center gap-2">
          <KindBadge kind={entry.kind} />
          <span className="line-clamp-2 leading-snug" style={{ color: "var(--color-text)" }}>{entry.content}</span>
        </span>
        {entry.injected ? (
          <span
            className="shrink-0 whitespace-nowrap rounded px-2 py-0.5 text-[11px]"
            style={{ background: "var(--green-bg)", border: "1px solid var(--green-brd)", color: "var(--color-green)" }}
          >
            ✓ injected · #{entry.finalRank + 1}
            {rankSuffix}
          </span>
        ) : (
          <span
            className="shrink-0 whitespace-nowrap rounded px-2 py-0.5 text-[11px]"
            style={{ background: "var(--red-bg)", border: "1px solid var(--red-brd)", color: "var(--color-red)" }}
          >
            ✗ below floor
          </span>
        )}
      </div>

      <div
        className="grid items-end gap-x-4 gap-y-2"
        style={{ gridTemplateColumns: `repeat(5,1fr) auto${reranked ? " auto" : ""}` }}
      >
        {SIGNALS.map((s) => (
          <SignalCell key={s.key} label={s.label} value={entry.signals[s.key]} color={s.color} />
        ))}
        <div className="pl-4 text-right text-[10px]" style={{ borderLeft: "1px solid var(--color-border)", color: "var(--color-text-2)" }}>
          fused
          <br />
          <b className="text-[13px]" style={{ color: "var(--color-text)" }}>{entry.fusedScore.toFixed(3)}</b>
        </div>
        {reranked && (
          <div className="text-right text-[10px]" style={{ color: "var(--color-text-2)" }}>
            rerank
            <br />
            <b className="text-[13px]" style={{ color: "var(--color-green)" }}>
              {entry.rerankScore !== null ? `${entry.rerankScore.toFixed(2)} ▲` : "—"}
            </b>
          </div>
        )}
      </div>
    </div>
  );
}

export function RecallTraceTab({ trace }: { trace: RecallTrace | null }) {
  if (!trace) {
    return (
      <div className="p-3" style={{ color: "var(--color-text-2)" }}>
        No recall recorded yet. Run a chat turn, then Refresh.
      </div>
    );
  }

  const byFused = [...trace.entries].sort((a, b) => b.fusedScore - a.fusedScore);
  const fusedRankOf = new Map(byFused.map((e, i) => [e.memoryId, i]));

  return (
    <div data-testid="memory-trace-tab" className="p-4" style={{ background: "var(--color-panel)", color: "var(--color-text)" }}>
      <div
        className="mb-4 rounded-md px-3.5 py-2.5 text-[12px] leading-relaxed"
        style={{ background: "var(--color-surface-2)", color: "var(--color-text)" }}
      >
        <b>query</b> "{trace.query}" &nbsp;·&nbsp; <b>scope</b> {trace.scopeKind} &nbsp;·&nbsp;{" "}
        <b>{trace.entries.length} candidates</b> &nbsp;·&nbsp;{" "}
        <span style={{ color: trace.reranked ? "var(--color-green)" : "var(--color-text-2)" }}>
          reranked {trace.reranked ? "✓" : "✗"}
        </span>{" "}
        &nbsp;·&nbsp; <b>floor</b> {trace.floor.toFixed(2)} &nbsp;·&nbsp; <b>k</b> {trace.k}
      </div>

      {trace.entries.length === 0 ? (
        <div style={{ color: "var(--color-text-2)" }}>
          0 candidates (recall returned nothing — empty query or no matches).
        </div>
      ) : (
        trace.entries.map((e) =>
          e.injected ? (
            <Entry
              key={e.memoryId}
              entry={e}
              reranked={trace.reranked}
              fusedRank={fusedRankOf.get(e.memoryId) ?? e.finalRank}
            />
          ) : (
            <div
              key={e.memoryId}
              data-testid={`trace-entry-${e.memoryId}`}
              className="mb-3 rounded-lg border p-3.5 opacity-55"
              style={{ borderColor: "var(--color-border-strong)", background: "var(--color-surface)" }}
            >
              <div className="mb-2 flex items-start justify-between gap-3">
                <span className="min-w-0 flex items-center gap-2">
                  <KindBadge kind={e.kind} />
                  <span className="line-clamp-2 leading-snug" style={{ color: "var(--color-text)" }}>{e.content}</span>
                </span>
                <span
                  className="shrink-0 whitespace-nowrap rounded px-2 py-0.5 text-[11px]"
                  style={{ background: "var(--red-bg)", border: "1px solid var(--red-brd)", color: "var(--color-red)" }}
                >
                  ✗ below floor
                </span>
              </div>
              <div style={{ color: "var(--color-text-3)" }}>
                fused <b>{e.fusedScore.toFixed(2)}</b> &lt; floor {trace.floor.toFixed(2)} — not
                injected
              </div>
            </div>
          )
        )
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run the memory webview test suite**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/memory`
Expected: PASS (no assertions reference specific hex values; `data-testid`s and text content are unchanged).

- [ ] **Step 5: Typecheck**

Run: `cd apps/vscode-extension/webview-ui && npm run typecheck`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/memory/MemoryApp.tsx apps/vscode-extension/webview-ui/src/memory/BrowserTab.tsx apps/vscode-extension/webview-ui/src/memory/RecallTraceTab.tsx
git commit -m "style(memory): migrate inspector palette onto shared design tokens"
```

---

### Task 2: Chat-window shortcut to open the Memory Inspector

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/Icon.tsx` (new `db` icon)
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (new `openMemoryPanel` webview message)
- Modify: `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx` (new header button)
- Modify: `apps/vscode-extension/webview-ui/src/components/ThreadView.test.tsx` (new test)
- Modify: `apps/vscode-extension/src/chat-panel.ts` (new handler type + routing branch + constructor param)
- Modify: `apps/vscode-extension/src/extension.ts` (wire the new handler to `aiEditor.openMemoryPanel`)

**Interfaces:**
- Consumes: existing `aiEditor.openMemoryPanel` VS Code command (`extension.ts:392`, already gracefully degrades when `CRUCIBLE_MEMORY_ENABLED` is off).
- Produces: `OpenMemoryPanelHandler = () => void`, a 25th constructor parameter on `ChatPanel` (after `onOpenSettings`).

- [ ] **Step 1: Add a `db` icon to `Icon.tsx`**

In `apps/vscode-extension/webview-ui/src/components/Icon.tsx`, add `"db"` to the `IconName` union:

```tsx
export type IconName =
  | "spark" | "search" | "plus" | "clock" | "chev-r" | "chev-l" | "chev-d"
  | "check" | "x" | "copy" | "file" | "term" | "list" | "diff" | "warn"
  | "send" | "stop" | "retry" | "bolt" | "bug"
  | "home" | "key" | "plug" | "book" | "shield" | "chip" | "gear" | "menu" | "db";
```

Add the glyph to `ICONS` (a simple stacked-database/storage icon, right before the closing `};` of `ICONS`):

```tsx
  db: (
    <>
      <ellipse cx="8" cy="4" rx="5.5" ry="2" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2.5 4v4c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2V4" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2.5 8v4c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2V8" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </>
  ),
```

- [ ] **Step 2: Add the `openMemoryPanel` webview message type**

In `apps/vscode-extension/webview-ui/src/types.ts`, in the `WebviewMessage` union, add after `openSettings`:

```ts
  | { type: "openSettings"; section?: string }
  // Chat-window shortcut to the standalone Memory Inspector panel/command.
  | { type: "openMemoryPanel" };
```

- [ ] **Step 3: Write the failing `ThreadView` test**

Add to `apps/vscode-extension/webview-ui/src/components/ThreadView.test.tsx`, inside the existing `describe("ThreadView settings overlay", ...)` block is fine, or a new describe — add as a new test:

```tsx
import { vscode } from "../vscodeApi";

// ... (existing content unchanged) ...

describe("ThreadView memory shortcut", () => {
  it("posts openMemoryPanel when the memory inspector button is clicked", () => {
    renderView();
    fireEvent.click(screen.getByRole("button", { name: /memory inspector/i }));
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({ type: "openMemoryPanel" });
  });
});
```

(`vscode` is already importable — the file already `vi.mock`s `"../vscodeApi"` at the top; add the `import { vscode } from "../vscodeApi";` line near the top imports if not already present in this file.)

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/ThreadView.test.tsx`
Expected: FAIL — no button with accessible name "memory inspector" exists yet.

- [ ] **Step 5: Add the header button in `ThreadView.tsx`**

In `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx`, immediately after the closing `</button>` of the ☰ settings-menu button (before the "Back button" comment), insert:

```tsx
        {/* Memory Inspector shortcut — opens the standalone panel (aiEditor.openMemoryPanel),
            which already degrades gracefully if CRUCIBLE_MEMORY_ENABLED is off. */}
        <button
          type="button"
          onClick={() => vscode.postMessage({ type: "openMemoryPanel" })}
          aria-label="Memory Inspector"
          title="Memory Inspector"
          className={[
            "flex items-center justify-center w-6 h-6 rounded-md",
            "border transition-colors duration-150",
          ].join(" ")}
          style={{ color: "var(--color-text-3)", background: "transparent", borderColor: "transparent" }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "var(--accent-bg)";
            (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--accent-brd)";
            (e.currentTarget as HTMLButtonElement).style.color = "var(--color-accent)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "transparent";
            (e.currentTarget as HTMLButtonElement).style.borderColor = "transparent";
            (e.currentTarget as HTMLButtonElement).style.color = "var(--color-text-3)";
          }}
        >
          <Icon name="db" size={14} />
        </button>

```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/ThreadView.test.tsx`
Expected: PASS

- [ ] **Step 7: Wire the extension host — `chat-panel.ts`**

In `apps/vscode-extension/src/chat-panel.ts`:

Add a new handler type near `OpenSettingsHandler` (line ~65):

```ts
export type OpenMemoryPanelHandler = () => void;
```

Add a new constructor parameter after `onOpenSettings` (the last positional param, currently `private readonly onOpenSettings: OpenSettingsHandler = () => {}`):

```ts
    private readonly onOpenSettings: OpenSettingsHandler = () => {},
    private readonly onOpenMemoryPanel: OpenMemoryPanelHandler = () => {}
  ) {}
```

Add a routing branch right after the existing `openSettings` branch in `registerHandlers()`:

```ts
      } else if (m["type"] === "openSettings") {
        this.onOpenSettings(typeof m["section"] === "string" ? m["section"] : undefined);
        return;
      } else if (m["type"] === "openMemoryPanel") {
        this.onOpenMemoryPanel();
        return;
      } else {
```

- [ ] **Step 8: Wire `extension.ts`**

In `apps/vscode-extension/src/extension.ts`, add a 34th argument to the `new ChatPanel(...)` call, right after the existing `onOpenSettings` closure (the one ending `void vscode.commands.executeCommand("aiEditor.openSettingsPanel", section);`):

```ts
    (section?: string) => {
      void vscode.commands.executeCommand("aiEditor.openSettingsPanel", section);
    },
    () => {
      void vscode.commands.executeCommand("aiEditor.openMemoryPanel");
    }
  );
```

- [ ] **Step 9: Typecheck the extension**

Run: `npm run -w ai-editor-vscode-extension typecheck`
Expected: PASS

- [ ] **Step 10: Run the extension test suite**

Run: `cd apps/vscode-extension && npx vitest run`
Expected: PASS (no existing test constructs `ChatPanel` positionally with a fixed arg count that this addition would break — the new parameter has a default value).

- [ ] **Step 11: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/Icon.tsx apps/vscode-extension/webview-ui/src/types.ts apps/vscode-extension/webview-ui/src/components/ThreadView.tsx apps/vscode-extension/webview-ui/src/components/ThreadView.test.tsx apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/src/extension.ts
git commit -m "feat(chat): header shortcut to open the Memory Inspector panel"
```

---

### Task 3: Shared composer trigger-detection (`composerTrigger.ts` + `TriggerDropdown.tsx`)

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/composerTrigger.ts`
- Create: `apps/vscode-extension/webview-ui/src/composerTrigger.test.ts`
- Create: `apps/vscode-extension/webview-ui/src/components/TriggerDropdown.tsx`
- Create: `apps/vscode-extension/webview-ui/src/components/TriggerDropdown.test.tsx`

**Interfaces:**
- Produces: `detectTrigger(text: string, cursor: number): ComposerTrigger | null` where `ComposerTrigger = { kind: "slash" | "file"; query: string; start: number; end: number }` (`start`/`end` are the token's character offsets in `text`, `end` always equals `cursor`).
- Produces: `<TriggerDropdown items={DropdownItem[]} activeIndex={number} onHover={(i: number) => void} onSelect={(id: string) => void} />` where `DropdownItem = { id: string; label: string; sublabel?: string; badge?: string }`.
- Consumed by: Task 4 (slash) and Task 6 (`@`-file).

- [ ] **Step 1: Write the failing trigger-detection tests**

```ts
// apps/vscode-extension/webview-ui/src/composerTrigger.test.ts
import { describe, it, expect } from "vitest";
import { detectTrigger } from "./composerTrigger";

describe("detectTrigger — slash", () => {
  it("detects a slash command being typed at the very start", () => {
    expect(detectTrigger("/rev", 4)).toEqual({ kind: "slash", query: "rev", start: 0, end: 4 });
  });

  it("does not trigger once a space has been typed after the name", () => {
    expect(detectTrigger("/review src/a.py", 8)).toBeNull();
  });

  it("does not trigger for a slash that isn't at the start of the message", () => {
    expect(detectTrigger("hello /world", 12)).toBeNull();
  });

  it("returns an empty query for a bare slash", () => {
    expect(detectTrigger("/", 1)).toEqual({ kind: "slash", query: "", start: 0, end: 1 });
  });
});

describe("detectTrigger — file mention", () => {
  it("detects an @-mention anywhere in the text", () => {
    expect(detectTrigger("look at @src/fo", 15)).toEqual({
      kind: "file", query: "src/fo", start: 8, end: 15,
    });
  });

  it("does not trigger once whitespace follows the mention", () => {
    const text = "look at @src/foo.py more";
    expect(detectTrigger(text, text.length)).toBeNull();
  });

  it("returns an empty query for a bare @", () => {
    expect(detectTrigger("hi @", 4)).toEqual({ kind: "file", query: "", start: 3, end: 4 });
  });

  it("returns null with no trigger character before the cursor", () => {
    expect(detectTrigger("just plain text", 6)).toBeNull();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/composerTrigger.test.ts`
Expected: FAIL — `composerTrigger.ts` does not exist yet.

- [ ] **Step 3: Implement `composerTrigger.ts`**

```ts
export interface ComposerTrigger {
  kind: "slash" | "file";
  query: string;
  start: number;
  end: number;
}

// Mirrors slash.ts's parseSlashCommand grammar: a slash command is only valid as
// the ENTIRE leading token of the message (name chars: [A-Za-z0-9._-]). Once a
// space is typed after the name, this stops matching — the user is now typing
// args, and doSend()'s existing parseSlashCommand/expandPrompt flow takes over.
function detectSlash(text: string, cursor: number): ComposerTrigger | null {
  const head = text.slice(0, cursor);
  const match = /^\/([A-Za-z0-9._-]*)$/.exec(head.trimStart());
  if (!match) return null;
  const start = head.length - (match[0].length - 1); // offset of the name start (after "/")
  return { kind: "slash", query: match[1] ?? "", start: start - 1, end: cursor };
}

// An @-mention can start anywhere, as long as there's no whitespace between the
// "@" and the cursor (the token is still being typed).
function detectFile(text: string, cursor: number): ComposerTrigger | null {
  let i = cursor;
  while (i > 0 && text[i - 1] !== "@" && !/\s/.test(text[i - 1])) i--;
  if (i > 0 && text[i - 1] === "@") {
    return { kind: "file", query: text.slice(i, cursor), start: i - 1, end: cursor };
  }
  return null;
}

/** Detects an in-progress "/" or "@" trigger token ending exactly at `cursor`. */
export function detectTrigger(text: string, cursor: number): ComposerTrigger | null {
  return detectSlash(text, cursor) ?? detectFile(text, cursor);
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/composerTrigger.test.ts`
Expected: PASS

- [ ] **Step 5: Write the failing `TriggerDropdown` test**

```tsx
// apps/vscode-extension/webview-ui/src/components/TriggerDropdown.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { TriggerDropdown } from "./TriggerDropdown";

const items = [
  { id: "review", label: "review", badge: "Prompt" },
  { id: "git-commit", label: "git-commit", sublabel: "Commit staged changes", badge: "Skill" },
];

describe("TriggerDropdown", () => {
  it("renders each item's label, sublabel, and badge", () => {
    render(<TriggerDropdown items={items} activeIndex={0} onHover={() => {}} onSelect={() => {}} />);
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.getByText("Prompt")).toBeTruthy();
    expect(screen.getByText("Commit staged changes")).toBeTruthy();
    expect(screen.getByText("Skill")).toBeTruthy();
  });

  it("calls onSelect with the clicked item's id", () => {
    const onSelect = vi.fn();
    render(<TriggerDropdown items={items} activeIndex={0} onHover={() => {}} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("git-commit"));
    expect(onSelect).toHaveBeenCalledWith("git-commit");
  });

  it("marks the active-index row for keyboard navigation styling", () => {
    render(<TriggerDropdown items={items} activeIndex={1} onHover={() => {}} onSelect={() => {}} />);
    expect(screen.getByTestId("trigger-item-git-commit").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("trigger-item-review").getAttribute("data-active")).toBe("false");
  });

  it("renders nothing for an empty item list", () => {
    const { container } = render(
      <TriggerDropdown items={[]} activeIndex={0} onHover={() => {}} onSelect={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/TriggerDropdown.test.tsx`
Expected: FAIL — `TriggerDropdown.tsx` does not exist yet.

- [ ] **Step 7: Implement `TriggerDropdown.tsx`**

Positioning mirrors `ModelMenu.tsx`'s popover (`absolute bottom-full left-0 z-50 mb-1.5 ... border`) — the caller (`InputArea.tsx`) renders this inside a `position: relative` wrapper.

```tsx
export interface DropdownItem {
  id: string;
  label: string;
  sublabel?: string;
  badge?: string;
}

interface Props {
  items: DropdownItem[];
  activeIndex: number;
  onHover: (index: number) => void;
  onSelect: (id: string) => void;
}

/** Live "/" or "@" trigger dropdown — positioned above the composer like ModelMenu. */
export function TriggerDropdown({ items, activeIndex, onHover, onSelect }: Props) {
  if (items.length === 0) return null;

  return (
    <div
      className="anim-rise absolute bottom-full left-0 z-50 mb-1.5 max-h-56 w-[280px] overflow-y-auto rounded-[10px] border p-1"
      style={{ background: "var(--color-surface)", borderColor: "var(--color-border-strong)" }}
      role="listbox"
    >
      {items.map((item, i) => (
        <div
          key={item.id}
          data-testid={`trigger-item-${item.id}`}
          data-active={i === activeIndex ? "true" : "false"}
          role="option"
          aria-selected={i === activeIndex}
          onMouseEnter={() => onHover(i)}
          onClick={() => onSelect(item.id)}
          className="menu-item flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs"
          style={{
            color: "var(--color-text-2)",
            background: i === activeIndex ? "var(--accent-bg)" : "transparent",
          }}
        >
          <span className="min-w-0 flex-1 truncate" style={{ color: "var(--color-text)" }}>{item.label}</span>
          {item.sublabel && (
            <span className="min-w-0 flex-1 truncate text-[10.5px]" style={{ color: "var(--color-text-3)" }}>
              {item.sublabel}
            </span>
          )}
          {item.badge && (
            <span
              className="shrink-0 rounded px-1.5 py-px text-[9.5px] font-semibold"
              style={{ background: "var(--color-surface-3)", color: "var(--color-text-2)" }}
            >
              {item.badge}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/TriggerDropdown.test.tsx`
Expected: PASS

- [ ] **Step 9: Typecheck**

Run: `cd apps/vscode-extension/webview-ui && npm run typecheck`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/composerTrigger.ts apps/vscode-extension/webview-ui/src/composerTrigger.test.ts apps/vscode-extension/webview-ui/src/components/TriggerDropdown.tsx apps/vscode-extension/webview-ui/src/components/TriggerDropdown.test.tsx
git commit -m "feat(webview): shared composer trigger-detection hook + dropdown"
```

---

### Task 4: Unified `/`-autocomplete in `InputArea.tsx`

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/slash.ts`
- Modify: `apps/vscode-extension/webview-ui/src/components/InputArea.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/InputArea.test.tsx`

**Interfaces:**
- Consumes: `detectTrigger` (Task 3), `TriggerDropdown`/`DropdownItem` (Task 3), existing `listSkills`/`skillList` round trip, existing `listPrompts`/`promptList` round trip (host already implements both — this task only adds the UI + a `listPrompts` request InputArea did not previously make).
- Produces: no new exported interfaces — internal to `InputArea`.

- [ ] **Step 1: Write the failing `slash.ts` test**

`apps/vscode-extension/webview-ui/src/slash.test.ts` already exists (covering `parseSlashCommand`/`resolveSkillCommand`). Update its import line and append a new `describe` block:

```ts
import { describe, expect, it } from "vitest";
import { parseSlashCommand, resolveSkillCommand, buildSlashDropdownItems } from "./slash";

// ... existing describe("parseSlashCommand", ...) and describe("resolveSkillCommand", ...) blocks unchanged ...

describe("buildSlashDropdownItems", () => {
  it("badges prompts and skills, filtered by query", () => {
    const items = buildSlashDropdownItems(
      "rev",
      ["review", "changelog"],
      [{ name: "git-commit", description: "Commit staged changes" }],
    );
    expect(items).toEqual([{ id: "review", label: "review", badge: "Prompt" }]);
  });

  it("prompt wins on name collision with a skill", () => {
    const items = buildSlashDropdownItems(
      "",
      ["shared-name"],
      [{ name: "shared-name", description: "a skill" }],
    );
    expect(items).toEqual([{ id: "shared-name", label: "shared-name", badge: "Prompt" }]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/slash.test.ts`
Expected: FAIL — `buildSlashDropdownItems` is not exported from `./slash` yet.

- [ ] **Step 3: Add the name-collision-aware merge helper to `slash.ts`**

Add to `apps/vscode-extension/webview-ui/src/slash.ts`:

```ts
export interface SlashDropdownItem {
  id: string;
  label: string;
  sublabel?: string;
  badge: "Prompt" | "Skill";
}

/**
 * Merge prompt names + skill catalog into one filtered, badged list for the
 * unified "/" dropdown. Prompt-file-wins-on-collision (mirrors resolveSkillCommand):
 * a name present in both lists renders only its Prompt row.
 */
export function buildSlashDropdownItems(
  query: string,
  promptNames: string[],
  skills: { name: string; description: string }[],
): SlashDropdownItem[] {
  const q = query.toLowerCase();
  const promptSet = new Set(promptNames);
  const prompts: SlashDropdownItem[] = promptNames
    .filter((n) => n.toLowerCase().includes(q))
    .map((n) => ({ id: n, label: n, badge: "Prompt" as const }));
  const skillItems: SlashDropdownItem[] = skills
    .filter((s) => !promptSet.has(s.name) && s.name.toLowerCase().includes(q))
    .map((s) => ({ id: s.name, label: s.name, sublabel: s.description, badge: "Skill" as const }));
  return [...prompts, ...skillItems];
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/slash.test.ts`
Expected: PASS

- [ ] **Step 5: Wire the dropdown into `InputArea.tsx`**

In `apps/vscode-extension/webview-ui/src/components/InputArea.tsx`:

Add imports:

```tsx
import { parseSlashCommand, resolveSkillCommand, buildSlashDropdownItems } from "../slash";
import { detectTrigger } from "../composerTrigger";
import { TriggerDropdown } from "./TriggerDropdown";
```

Add state (alongside the existing `skillNames`/`skillsRequestedRef` state):

```tsx
  // Unified "/" + "@" dropdown state.
  const [trigger, setTrigger] = useState<{ kind: "slash" | "file"; query: string; start: number; end: number } | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [promptNames, setPromptNames] = useState<string[]>([]);
  const [skillCatalog, setSkillCatalog] = useState<{ name: string; description: string }[]>([]);
  const promptsRequestedRef = useRef(false);
```

Extend the existing `skillList` listener effect to also handle `promptList` (the host already implements both `listPrompts`/`promptList` for Task doSend's expand flow — this just also captures the full catalog for the dropdown; rename the local var to keep both):

```tsx
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const m = e.data as Record<string, unknown>;
      if (m?.["type"] === "skillList") {
        const skills = (m["skills"] as { name: string; description: string }[] | undefined) ?? [];
        setSkillNames(skills.map((s) => s.name));
        setSkillCatalog(skills);
      } else if (m?.["type"] === "promptList") {
        setPromptNames((m["names"] as string[] | undefined) ?? []);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);
```

Replace `maybeRequestSkills` with a combined lazy-fetch that also requests prompts, and recompute the trigger on every keystroke — update `handleInput`:

```tsx
  function maybeRequestCatalogs(value: string) {
    if (value.startsWith("/")) {
      if (!skillsRequestedRef.current) {
        skillsRequestedRef.current = true;
        vscode.postMessage({ type: "listSkills" });
      }
      if (!promptsRequestedRef.current) {
        promptsRequestedRef.current = true;
        vscode.postMessage({ type: "listPrompts" });
      }
    }
  }

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const value = e.target.value;
    onDraftChange(value);
    maybeRequestCatalogs(value);
    const cursor = e.target.selectionStart ?? value.length;
    const t = detectTrigger(value, cursor);
    setTrigger(t);
    setActiveIndex(0);
    autoGrow();
  }
```

(Remove the old `maybeRequestSkills` function and its lone call site — replaced above.)

Compute the dropdown's items just before the `return` (slash-only for this task; Task 6 adds the file-kind branch):

```tsx
  const dropdownItems =
    trigger?.kind === "slash"
      ? buildSlashDropdownItems(trigger.query, promptNames, skillCatalog).map((i) => ({
          id: i.id, label: i.label, sublabel: i.sublabel, badge: i.badge,
        }))
      : [];

  function applyTriggerSelection(id: string) {
    if (!trigger) return;
    const el = textareaRef.current;
    const before = draft.slice(0, trigger.start);
    const after = draft.slice(trigger.end);
    const insertion = trigger.kind === "slash" ? `/${id} ` : `@${id} `;
    const next = `${before}${insertion}${after}`;
    onDraftChange(next);
    setTrigger(null);
    requestAnimationFrame(() => {
      if (!el) return;
      const pos = before.length + insertion.length;
      el.focus();
      el.setSelectionRange(pos, pos);
    });
  }

  function handleTriggerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): boolean {
    if (!trigger || dropdownItems.length === 0) return false;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % dropdownItems.length);
      return true;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + dropdownItems.length) % dropdownItems.length);
      return true;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      applyTriggerSelection(dropdownItems[activeIndex].id);
      return true;
    }
    if (e.key === "Escape") {
      setTrigger(null);
      return true;
    }
    return false;
  }
```

Wire it into the existing `handleKeyDown`:

```tsx
  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (handleTriggerKeyDown(e)) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
  }
```

Wrap the existing outer `<div>` (the one with `className={["rounded-[10px] border px-3 pt-2 pb-1.5", ...]}`) so it has `position: relative`, and render the dropdown inside it, just before the closing `</div>` of that root element:

```tsx
    <div
      className={[
        "relative rounded-[10px] border px-3 pt-2 pb-1.5",
        "transition-opacity duration-150",
        availability.disabled ? "opacity-55" : "opacity-100",
      ].join(" ")}
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border-strong)",
      }}
    >
      {trigger && dropdownItems.length > 0 && (
        <TriggerDropdown
          items={dropdownItems}
          activeIndex={activeIndex}
          onHover={setActiveIndex}
          onSelect={applyTriggerSelection}
        />
      )}
      {/* Textarea */}
      ...
```

(Only the `className` string of the existing root `<div>` changes — `"rounded-[10px] ..."` becomes `"relative rounded-[10px] ..."` — plus the new `{trigger && ...}` block right after the opening tag.)

- [ ] **Step 6: Write the `InputArea` dropdown tests**

Add to `apps/vscode-extension/webview-ui/src/components/InputArea.test.tsx`:

```tsx
describe("InputArea unified / dropdown", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows prompt and skill rows after typing / and requests both catalogs", () => {
    render(<Harness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/" } });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({ type: "listSkills" });
    expect(calls).toContainEqual({ type: "listPrompts" });

    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "promptList", names: ["review"] } }));
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "skillList", skills: [{ name: "git-commit", description: "Commit staged changes" }] },
      }));
    });
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.getByText("git-commit")).toBeTruthy();
    expect(screen.getByText("Prompt")).toBeTruthy();
    expect(screen.getByText("Skill")).toBeTruthy();
  });

  it("Enter on a dropdown row inserts the name without sending", () => {
    render(<Harness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/" } });
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "promptList", names: ["review"] } }));
      window.dispatchEvent(new MessageEvent("message", { data: { type: "skillList", skills: [] } }));
    });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("/review ");
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls.find((c) => c.type === "sendMessage")).toBeUndefined();
  });
});
```

The existing `Harness` component in this file pins `draft` to a fixed initial value (`useState("/review src/a.py")`), which doesn't fit a clean `/`-type-through test. Add a second harness variant right below it:

```tsx
function EmptyHarness() {
  const [draft, setDraft] = useState("");
  return <InputArea availability={availability} draft={draft} onDraftChange={setDraft} />;
}
```

and use `<EmptyHarness />` (not `<Harness />`) in this new `describe` block.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/InputArea.test.tsx`
Expected: PASS (all tests in this file, including the pre-existing slash-expansion ones — the dropdown is purely additive: a `/name args` typed and Enter'd without ever navigating the dropdown must still hit `doSend()`'s existing `parseSlashCommand`/`expandPrompt` path unchanged, since `handleTriggerKeyDown` returns `false` once the trigger no longer matches after a space). If any assertion fails, fix the wiring from Step 5 rather than the test.

- [ ] **Step 8: Typecheck**

Run: `cd apps/vscode-extension/webview-ui && npm run typecheck`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/slash.ts apps/vscode-extension/webview-ui/src/slash.test.ts apps/vscode-extension/webview-ui/src/components/InputArea.tsx apps/vscode-extension/webview-ui/src/components/InputArea.test.tsx
git commit -m "feat(webview): unified /-autocomplete dropdown for prompts + skills"
```

---

### Task 5: Extension host — workspace file search + open-file wiring

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/types.ts` (new message types)
- Modify: `apps/vscode-extension/src/chat-panel.ts` (new handler types + routing + constructor params)
- Modify: `apps/vscode-extension/src/extension.ts` (implement the two closures with `vscode.workspace`/`vscode.window`)

**Interfaces:**
- Produces: `ListWorkspaceFilesHandler = () => Promise<string[]>`, `OpenFileHandler = (relativePath: string) => void` — both new `ChatPanel` constructor params after `onOpenMemoryPanel`.
- Consumed by: Task 6 (file list for the `@`-dropdown) and Task 7's rendering half (open-on-click).

- [ ] **Step 1: Add message types to `types.ts`**

In `apps/vscode-extension/webview-ui/src/types.ts`, extend `WebviewMessage`:

```ts
  | { type: "openMemoryPanel" }
  // @-mention composer: workspace file listing + click-to-open.
  | { type: "listWorkspaceFiles" }
  | { type: "openFile"; path: string };
```

Extend `ExtensionMessage`:

```ts
  | { type: "promptExpanded"; name: string; found: boolean; text: string }
  | { type: "workspaceFileList"; paths: string[] };
```

- [ ] **Step 2: Add handler types + routing to `chat-panel.ts`**

Add types near `OpenMemoryPanelHandler`:

```ts
export type ListWorkspaceFilesHandler = () => Promise<string[]>;
export type OpenFileHandler = (relativePath: string) => void;
```

Add two more constructor params after `onOpenMemoryPanel`:

```ts
    private readonly onOpenMemoryPanel: OpenMemoryPanelHandler = () => {},
    private readonly onListWorkspaceFiles: ListWorkspaceFilesHandler = async () => [],
    private readonly onOpenFile: OpenFileHandler = () => {}
  ) {}
```

The existing `listSkills` branch (unchanged, shown for exact insertion context) is:

```ts
      } else if (m["type"] === "listSkills") {
        p = (async () => {
          const skills = await this.onListSkills();
          this.panel?.webview.postMessage({ type: "skillList", skills });
        })();
      } else if (m["type"] === "expandPrompt") {
```

Insert two new branches between `listSkills` and `expandPrompt`, matching the same `p = (async () => {...})();` style, and a third branch (`openFile`, fire-and-forget like `openSettings`) right before the existing `openMemoryPanel` branch added in Task 2 Step 7:

```ts
      } else if (m["type"] === "listSkills") {
        p = (async () => {
          const skills = await this.onListSkills();
          this.panel?.webview.postMessage({ type: "skillList", skills });
        })();
      } else if (m["type"] === "listWorkspaceFiles") {
        p = (async () => {
          const paths = await this.onListWorkspaceFiles();
          this.panel?.webview.postMessage({ type: "workspaceFileList", paths });
        })();
      } else if (m["type"] === "openFile") {
        this.onOpenFile(m["path"] as string);
        return;
      } else if (m["type"] === "expandPrompt") {
```

(`openFile` is inserted here, right after `listSkills`/`listWorkspaceFiles` and before `expandPrompt` — anywhere among the `else if` chain works since order doesn't matter for string-equality branches; grouping it next to `listWorkspaceFiles` keeps the two new `@`-mention-related branches together.)

- [ ] **Step 3: Implement the two closures in `extension.ts`**

Add two more arguments to the `new ChatPanel(...)` call, after the `openMemoryPanel` closure added in Task 2 Step 8:

```ts
    () => {
      void vscode.commands.executeCommand("aiEditor.openMemoryPanel");
    },
    async () => {
      const ws = vscode.workspace.workspaceFolders?.[0]?.uri;
      if (!ws) return [];
      const uris = await vscode.workspace.findFiles(
        "**/*",
        "{**/node_modules/**,**/.git/**,**/dist/**,**/target/**,**/__pycache__/**,**/.venv/**,**/.agentd/**,**/.ai-editor/**}",
        5000
      );
      return uris.map((u) => vscode.workspace.asRelativePath(u, false));
    },
    (relativePath: string) => {
      const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!ws) return;
      void vscode.window.showTextDocument(vscode.Uri.file(path.join(ws, relativePath)));
    }
  );
```

(`path` is Node's `path` module — check the top of `extension.ts` for an existing `import * as path from "node:path"`; add it if not already imported.)

- [ ] **Step 4: Typecheck**

Run: `npm run -w ai-editor-vscode-extension typecheck`
Expected: PASS

- [ ] **Step 5: Run the extension test suite**

Run: `cd apps/vscode-extension && npx vitest run`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/types.ts apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/src/extension.ts
git commit -m "feat(extension): workspace file search + open-file host wiring for @-mentions"
```

---

### Task 6: `@`-file mention dropdown in `InputArea.tsx`

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/InputArea.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/InputArea.test.tsx`

**Interfaces:**
- Consumes: `trigger`/`detectTrigger`/`TriggerDropdown` (Task 3), `listWorkspaceFiles`/`workspaceFileList` (Task 5).
- Produces: on send, `sendMessage` gains `mentionedPaths?: string[]` — the ordered list of paths the user actually selected via the dropdown (not a blind `@`-regex scan). Consumed by Task 7.

- [ ] **Step 1: Add file-list state + tracked-mentions bookkeeping**

In `InputArea.tsx`, add state alongside `promptNames`/`skillCatalog`:

```tsx
  const [workspaceFiles, setWorkspaceFiles] = useState<string[]>([]);
  const filesRequestedRef = useRef(false);
  // Ordered set of paths inserted via the @ dropdown in the CURRENT draft — only
  // these resolve to a real mention on send (not any stray "@text" the user typed).
  const trackedMentionsRef = useRef<string[]>([]);
```

Extend the message listener to also capture `workspaceFileList`:

```tsx
      } else if (m?.["type"] === "workspaceFileList") {
        setWorkspaceFiles((m["paths"] as string[] | undefined) ?? []);
      }
```

- [ ] **Step 2: Request the file list lazily on the first `@`**

Extend `maybeRequestCatalogs` (rename conceptually to cover both triggers — keep the name, just add a branch):

```tsx
  function maybeRequestCatalogs(value: string) {
    if (value.startsWith("/")) {
      if (!skillsRequestedRef.current) {
        skillsRequestedRef.current = true;
        vscode.postMessage({ type: "listSkills" });
      }
      if (!promptsRequestedRef.current) {
        promptsRequestedRef.current = true;
        vscode.postMessage({ type: "listPrompts" });
      }
    }
    if (value.includes("@") && !filesRequestedRef.current) {
      filesRequestedRef.current = true;
      vscode.postMessage({ type: "listWorkspaceFiles" });
    }
  }
```

- [ ] **Step 3: Compute file-kind dropdown items and extend `applyTriggerSelection`**

Update the `dropdownItems` computation to branch on `trigger.kind`:

```tsx
  const dropdownItems =
    trigger?.kind === "slash"
      ? buildSlashDropdownItems(trigger.query, promptNames, skillCatalog).map((i) => ({
          id: i.id, label: i.label, sublabel: i.sublabel, badge: i.badge,
        }))
      : trigger?.kind === "file"
      ? workspaceFiles
          .filter((p) => p.toLowerCase().includes(trigger.query.toLowerCase()))
          .slice(0, 20)
          .map((p) => ({ id: p, label: p }))
      : [];
```

Update `applyTriggerSelection` to record file mentions:

```tsx
  function applyTriggerSelection(id: string) {
    if (!trigger) return;
    const el = textareaRef.current;
    const before = draft.slice(0, trigger.start);
    const after = draft.slice(trigger.end);
    const insertion = trigger.kind === "slash" ? `/${id} ` : `@${id} `;
    const next = `${before}${insertion}${after}`;
    if (trigger.kind === "file" && !trackedMentionsRef.current.includes(id)) {
      trackedMentionsRef.current = [...trackedMentionsRef.current, id];
    }
    onDraftChange(next);
    setTrigger(null);
    requestAnimationFrame(() => {
      if (!el) return;
      const pos = before.length + insertion.length;
      el.focus();
      el.setSelectionRange(pos, pos);
    });
  }
```

- [ ] **Step 4: Send tracked mentions still present in the draft, then clear**

Update `doSend()` to compute and send `mentionedPaths`, filtering to mentions whose `@path` token still appears in the final text (the user may have deleted one after inserting it):

```tsx
  function doSend() {
    if (availability.disabled) return;
    const trimmed = draft.trim();
    if (!trimmed) return;
    const slash = parseSlashCommand(trimmed);
    if (slash) {
      pendingSlashRef.current = trimmed;
      vscode.postMessage({ type: "expandPrompt", name: slash.name, args: slash.args });
      trackedMentionsRef.current = [];
      return;
    }
    const mentionedPaths = trackedMentionsRef.current.filter((p) => draft.includes(`@${p}`));
    vscode.postMessage({
      type: "sendMessage",
      text: trimmed,
      stepReview,
      ...(mentionedPaths.length ? { mentionedPaths } : {}),
    });
    trackedMentionsRef.current = [];
    onDraftChange("");
    const el = textareaRef.current;
    if (el) el.style.height = "auto";
  }
```

(The `promptExpanded` listener's fallback send path — the `found === false` branch that sends `original`/skill messages — does not carry mentions: a message that resolved as a slash/skill command is not also a file-mention message. No change needed there.)

- [ ] **Step 5: Add `mentionedPaths` to the `sendMessage` webview message type**

In `types.ts`:

```ts
  | { type: "sendMessage"; text: string; stepReview?: boolean; forcedSkills?: string[]; mentionedPaths?: string[] }
```

- [ ] **Step 6: Write the failing `@`-mention tests**

Add to `InputArea.test.tsx`:

```tsx
describe("InputArea @-file mentions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows matching files after typing @ and requests the file list once", () => {
    render(<EmptyHarness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "look at @src" } });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({ type: "listWorkspaceFiles" });

    act(() => {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "workspaceFileList", paths: ["src/foo.py", "src/bar.py", "readme.md"] },
      }));
    });
    expect(screen.getByText("src/foo.py")).toBeTruthy();
    expect(screen.getByText("src/bar.py")).toBeTruthy();
    expect(screen.queryByText("readme.md")).toBeNull();
  });

  it("selecting a file inserts @path and sending includes mentionedPaths", () => {
    render(<EmptyHarness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "@src" } });
    act(() => {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "workspaceFileList", paths: ["src/foo.py"] },
      }));
    });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("@src/foo.py ");

    fireEvent.change(ta, { target: { value: "@src/foo.py look here" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({
      type: "sendMessage", text: "@src/foo.py look here", stepReview: true,
      mentionedPaths: ["src/foo.py"],
    });
  });

  it("does not send mentionedPaths for a mention the user deleted before sending", () => {
    render(<EmptyHarness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "@src" } });
    act(() => {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "workspaceFileList", paths: ["src/foo.py"] },
      }));
    });
    fireEvent.keyDown(ta, { key: "Enter" });
    fireEvent.change(ta, { target: { value: "never mind" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    const sent = calls.find((c) => c.type === "sendMessage");
    expect(sent.mentionedPaths).toBeUndefined();
  });
});
```

(Reuses the `EmptyHarness` component added in Task 4 Step 5.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/InputArea.test.tsx`
Expected: PASS — Steps 1–5 already wired the implementation these tests exercise. If any assertion fails, fix the wiring, not the test.

- [ ] **Step 8: Typecheck**

Run: `cd apps/vscode-extension/webview-ui && npm run typecheck`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/InputArea.tsx apps/vscode-extension/webview-ui/src/components/InputArea.test.tsx apps/vscode-extension/webview-ui/src/types.ts
git commit -m "feat(webview): @-file mention dropdown and tracked-mention send payload"
```

---

### Task 7: Read + cap mentioned file content in `controller.ts`, thread through to the backend contract

**Files:**
- Modify: `apps/vscode-extension/src/chat-panel.ts` (`ChatMessageHandler` type + routing)
- Modify: `apps/vscode-extension/src/extension.ts` (pass `mentionedPaths` through to `controller.sendChatMessage`)
- Modify: `apps/vscode-extension/src/controller.ts` (read + cap files, extend `sendChatMessage`)
- Create: `apps/vscode-extension/src/mentioned-files.ts` (pure read+cap helper, unit-testable without a live workspace)
- Create: `apps/vscode-extension/test/mentioned-files.test.ts`
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`
- Modify: `apps/editor-client/src/client/http-backend-client.ts`
- Modify: `apps/editor-client/test/http-backend-client.test.ts`

**Interfaces:**
- Produces: `readMentionedFiles(workspacePath: string, relativePaths: string[]): Promise<{path: string; content: string}[]>` in `mentioned-files.ts` — skips unreadable files with a `(file not found or unreadable)` content marker rather than throwing.
- Produces: `sendChatMessage(client, threadId, message, signal, options)` where `options.mentionedFiles?: {path: string; content: string}[]` (editor-client), serializing to body field `mentioned_files: [{path, content}]`.
- Consumed by: Task 8 (backend route + `ChatController`).

- [ ] **Step 1: Write the failing `mentioned-files.ts` test**

```ts
// apps/vscode-extension/test/mentioned-files.test.ts
import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { readMentionedFiles, MENTION_FILE_MAX_CHARS } from "../src/mentioned-files.js";

describe("readMentionedFiles", () => {
  it("reads and returns file content for each path", () => {
    const ws = fs.mkdtempSync(path.join(os.tmpdir(), "mention-"));
    fs.writeFileSync(path.join(ws, "a.txt"), "hello world");
    const result = readMentionedFiles(ws, ["a.txt"]);
    expect(result).toEqual([{ path: "a.txt", content: "hello world" }]);
  });

  it("caps content at MENTION_FILE_MAX_CHARS", () => {
    const ws = fs.mkdtempSync(path.join(os.tmpdir(), "mention-"));
    const big = "x".repeat(MENTION_FILE_MAX_CHARS + 500);
    fs.writeFileSync(path.join(ws, "big.txt"), big);
    const result = readMentionedFiles(ws, ["big.txt"]);
    expect(result[0].content.length).toBeLessThanOrEqual(MENTION_FILE_MAX_CHARS + 50); // + truncation marker
    expect(result[0].content.startsWith("x".repeat(100))).toBe(true);
  });

  it("marks a missing file as unreadable instead of throwing", () => {
    const ws = fs.mkdtempSync(path.join(os.tmpdir(), "mention-"));
    const result = readMentionedFiles(ws, ["does-not-exist.txt"]);
    expect(result).toEqual([{ path: "does-not-exist.txt", content: "(file not found or unreadable)" }]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/vscode-extension && npx vitest run test/mentioned-files.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `mentioned-files.ts`**

```ts
import * as fs from "node:fs";
import * as path from "node:path";

export const MENTION_FILE_MAX_CHARS = 20_000;

/**
 * Reads @-mentioned files for a chat turn (host pre-reads, per the composer
 * intelligence design — the model sees the content this turn regardless of
 * whether it would have chosen to read_file on its own). A fixed cap, not an
 * env var: this is a UI-side convenience limit, not a backend policy knob.
 */
export function readMentionedFiles(
  workspacePath: string,
  relativePaths: string[]
): { path: string; content: string }[] {
  return relativePaths.map((relativePath) => {
    try {
      const raw = fs.readFileSync(path.join(workspacePath, relativePath), "utf8");
      const content =
        raw.length > MENTION_FILE_MAX_CHARS
          ? `${raw.slice(0, MENTION_FILE_MAX_CHARS)}\n... (truncated)`
          : raw;
      return { path: relativePath, content };
    } catch {
      return { path: relativePath, content: "(file not found or unreadable)" };
    }
  });
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/vscode-extension && npx vitest run test/mentioned-files.test.ts`
Expected: PASS

- [ ] **Step 5: Extend the editor-client contract**

In `apps/editor-client/src/contracts/task-contracts.ts`, update the `sendChatMessage` interface method (line ~428):

```ts
  sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: { stepReview?: boolean; forcedSkills?: string[]; mentionedFiles?: { path: string; content: string }[] }): AsyncIterable<StreamEvent>;
```

- [ ] **Step 6: Write the failing editor-client test**

Add to `apps/editor-client/test/http-backend-client.test.ts`, in the `describe("HttpBackendClient skills", ...)` block (or a new describe — either is fine, matching the file's existing style is preferred):

```ts
  test("sendChatMessage includes mentioned_files in the body", async () => {
    let sentBody = "";
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async (_url, init) => {
        sentBody = (init?.body as string) ?? "";
        return new Response("", { status: 200, headers: { "content-type": "text/event-stream" } });
      },
    });
    const iter = client.sendChatMessage("t1", "hi", undefined, {
      mentionedFiles: [{ path: "src/a.py", content: "x = 1" }],
    });
    await iter[Symbol.asyncIterator]().next();
    expect(JSON.parse(sentBody).mentioned_files).toEqual([{ path: "src/a.py", content: "x = 1" }]);
  });

  test("sendChatMessage omits mentioned_files when not provided", async () => {
    let sentBody = "";
    const client = new HttpBackendClient({
      baseUrl: "http://localhost:8000",
      fetchFn: async (_url, init) => {
        sentBody = (init?.body as string) ?? "";
        return new Response("", { status: 200, headers: { "content-type": "text/event-stream" } });
      },
    });
    const iter = client.sendChatMessage("t1", "hi");
    await iter[Symbol.asyncIterator]().next();
    expect(JSON.parse(sentBody).mentioned_files).toBeUndefined();
  });
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `cd apps/editor-client && npx vitest run test/http-backend-client.test.ts`
Expected: FAIL — `mentioned_files` not yet serialized.

- [ ] **Step 8: Update `http-backend-client.ts`**

In `apps/editor-client/src/client/http-backend-client.ts`, update `sendChatMessage`'s signature and body:

```ts
  async *sendChatMessage(threadId: string, message: string, signal?: AbortSignal, options?: { stepReview?: boolean; forcedSkills?: string[]; mentionedFiles?: { path: string; content: string }[] }): AsyncIterable<StreamEvent> {
    const response = await this.fetchFn(
      `${this.options.baseUrl}/v1/chat/threads/${encodeURIComponent(threadId)}/message`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          content: message,
          ...(options?.stepReview !== undefined ? { step_review: options.stepReview } : {}),
          ...(options?.forcedSkills && options.forcedSkills.length
            ? { forced_skills: options.forcedSkills }
            : {}),
          ...(options?.mentionedFiles && options.mentionedFiles.length
            ? { mentioned_files: options.mentionedFiles }
            : {}),
        }),
        signal: signal ?? null,
      }
    );
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd apps/editor-client && npx vitest run test/http-backend-client.test.ts`
Expected: PASS

- [ ] **Step 10: Build editor-client (vscode-extension typechecks against its compiled output)**

Run: `npm run -w @ai-editor/editor-client build`
Expected: PASS

- [ ] **Step 11: Extend `controller.ts`'s `sendChatMessage`**

In `apps/vscode-extension/src/controller.ts`, add the import at the top:

```ts
import { readMentionedFiles } from "./mentioned-files.js";
```

Update the method signature and body:

```ts
  async sendChatMessage(
    text: string, stepReview?: boolean, forcedSkills?: string[], mentionedPaths?: string[]
  ): Promise<void> {
    const workspacePath = this.ui.getWorkspacePath() ?? "";
    const client = this.createClient(this.settings.getBackendBaseUrl());

    if (!this.activeThreadId) {
      try {
        const thread = await client.createChatThread(workspacePath);
        this.activeThreadId = thread.threadId;
        this.ui.openChatPanel();
      } catch (error) {
        this.ui.showError(`Failed to create chat thread: ${formatError(error)}`);
        return;
      }
    }

    const threadId = this.activeThreadId;
    const mentionedFiles =
      mentionedPaths && mentionedPaths.length && workspacePath
        ? readMentionedFiles(workspacePath, mentionedPaths)
        : undefined;

    this.ui.appendChatMessage({
      role: "user",
      content: text,
      type: "text",
      timestamp: this.now(),
      metadata: {},
    });

    this.ui.setChatInputEnabled(false);
    this.turnAbort = new AbortController();
    await this.streamTurn(
      client.sendChatMessage(
        threadId,
        text,
        this.turnAbort.signal,
        stepReview !== undefined || forcedSkills?.length || mentionedFiles?.length
          ? {
              ...(stepReview !== undefined ? { stepReview } : {}),
              ...(forcedSkills?.length ? { forcedSkills } : {}),
              ...(mentionedFiles?.length ? { mentionedFiles } : {}),
            }
          : undefined,
      ),
    );
  }
```

- [ ] **Step 12: Thread `mentionedPaths` through `chat-panel.ts` and `extension.ts`**

In `chat-panel.ts`, update the `ChatMessageHandler` type and the `sendMessage` routing branch:

```ts
export type ChatMessageHandler = (
  message: string,
  stepReview?: boolean,
  forcedSkills?: string[],
  mentionedPaths?: string[]
) => Promise<void>;
```

```ts
      } else if (m["type"] === "sendMessage") {
        const forcedSkills = Array.isArray(m["forcedSkills"])
          ? (m["forcedSkills"] as string[])
          : undefined;
        const mentionedPaths = Array.isArray(m["mentionedPaths"])
          ? (m["mentionedPaths"] as string[])
          : undefined;
        p = this.onMessage(m["text"] as string, m["stepReview"] === true, forcedSkills, mentionedPaths);
```

In `extension.ts`, update the `ChatPanel` constructor's first argument:

```ts
    (message, stepReview, forcedSkills, mentionedPaths) =>
      controller.sendChatMessage(message, stepReview, forcedSkills, mentionedPaths),
```

- [ ] **Step 13: Typecheck everything**

Run: `npm run -w @ai-editor/editor-client build && npm run -w ai-editor-vscode-extension typecheck`
Expected: PASS

- [ ] **Step 14: Run the extension test suite**

Run: `cd apps/vscode-extension && npx vitest run`
Expected: PASS

- [ ] **Step 15: Commit**

```bash
git add apps/vscode-extension/src/mentioned-files.ts apps/vscode-extension/test/mentioned-files.test.ts apps/vscode-extension/src/controller.ts apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/src/extension.ts apps/editor-client/src/contracts/task-contracts.ts apps/editor-client/src/client/http-backend-client.ts apps/editor-client/test/http-backend-client.test.ts
git commit -m "feat(editor-client,extension): mentioned-files contract + host-side read/cap"
```

---

### Task 8: Backend — accept `mentioned_files`, fold into the current turn's content

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py`
- Modify: `services/agentd-py/agentd/chat/controller.py`
- Modify: `services/agentd-py/tests/test_controller_route_detach.py` (update `_SlowController.handle_message` signature)
- Create: `services/agentd-py/tests/test_mentioned_files.py`

**Interfaces:**
- Produces: `ChatController.handle_message(..., mentioned_files: list[dict[str, str]] | None = None)`. The augmented text is folded into `_run_loop`'s `goal` argument (→ `plan_context["goal"]`) for this turn only; the persisted/display `ChatMessage.content` stays the original short text, tagged with `metadata["mentioned_files"] = [path, ...]` (paths only).

- [ ] **Step 1: Write the failing controller test**

```python
# services/agentd-py/tests/test_mentioned_files.py
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


class _CapturingEngine(ScriptedReasoningEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.captured_goals: list[str] = []

    async def create_controller_step(self, plan_context, history, tool_definitions, *, phase, on_thinking=None):
        self.captured_goals.append(plan_context["goal"])
        return await super().create_controller_step(
            plan_context, history, tool_definitions, phase=phase, on_thinking=on_thinking)


@pytest.mark.asyncio
async def test_mentioned_file_content_folds_into_turn_goal_only(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    engine = _CapturingEngine(
        None, [], controller_step_responses=[
            {"type": "answer", "thought": "t", "answer": "ok"}])
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=engine,
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)

    await ctrl.handle_message(
        thread.thread_id, "what does this do", channel_id="c1",
        mentioned_files=[{"path": "src/a.py", "content": "x = 1"}])

    # The model saw the file content this turn.
    assert any("src/a.py" in g and "x = 1" in g for g in engine.captured_goals)

    reloaded = store.get_thread(thread.thread_id)
    assert reloaded is not None
    user_msg = next(m for m in reloaded.messages if m.role == "user")
    # The persisted/display message stays the short original text — no file
    # content duplicated into chat storage.
    assert user_msg.content == "what does this do"
    assert user_msg.metadata.get("mentioned_files") == ["src/a.py"]


@pytest.mark.asyncio
async def test_no_mentioned_files_is_byte_identical_to_today(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "chat.sqlite3")
    thread = store.create_thread(str(tmp_path), title="t")
    engine = _CapturingEngine(
        None, [], controller_step_responses=[
            {"type": "answer", "thought": "t", "answer": "ok"}])
    ctrl = ChatController(
        workspace_path=str(tmp_path),
        reasoning_engine=engine,
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)

    await ctrl.handle_message(thread.thread_id, "hello", channel_id="c1")

    assert engine.captured_goals == ["hello"]
    reloaded = store.get_thread(thread.thread_id)
    user_msg = next(m for m in reloaded.messages if m.role == "user")
    assert user_msg.content == "hello"
    assert user_msg.metadata == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/agentd-py && pytest tests/test_mentioned_files.py`
Expected: FAIL — `handle_message` doesn't accept `mentioned_files`.

- [ ] **Step 3: Update `ChatController.handle_message`**

In `services/agentd-py/agentd/chat/controller.py`, replace the `handle_message` body (lines ~271-316):

```python
    async def handle_message(
        self, thread_id: str, message: str, channel_id: str, step_review: bool | None = None,
        forced_skills: list[str] | None = None,
        mentioned_files: list[dict[str, str]] | None = None,
    ) -> None:
        thread = self._store.get_thread(thread_id)
        if thread is None:
            raise ValueError(f"Thread {thread_id!r} not found")
        # A new turn can never leave a stale gate rendered: clear it at the start so a
        # late decision on a superseded card hits `gate is None` and no-ops (resolve_mode/
        # resolve_edit already guard on this). A clarify sets no gate, so this is a no-op
        # on the clarify/EDIT-clarify resume path — no conflict.
        self._store.set_controller_gate(thread_id, None)
        # Auto-name the thread from its first user message (mirrors ChatAgent).
        if not any(m.role == "user" for m in thread.messages):
            title = message.strip().replace("\n", " ")[:50]
            self._store.update_title(thread_id, title)
            self._broadcaster.broadcast(channel_id, {
                "type": "thread_title_updated",
                "payload": {"thread_id": thread_id, "title": title},
            })
        # @-mentions are turn-scoped: the model sees the referenced file content
        # only for THIS turn (folded into turn_message below, which feeds
        # _run_loop's goal / plan_context["goal"]). The persisted/display message
        # and conversation history keep the short original text, tagged with the
        # mentioned paths only (never content) so the transcript can render
        # clickable mentions without duplicating file content into chat storage.
        turn_message = message
        mentioned_paths: list[str] = []
        if mentioned_files:
            mentioned_paths = [f["path"] for f in mentioned_files if f.get("path")]
            blocks = "\n\n".join(
                f"### {f['path']}\n```\n{f['content']}\n```"
                for f in mentioned_files if f.get("path")
            )
            if blocks:
                turn_message = f"{message}\n\n---\nReferenced files:\n{blocks}"
        self._store.append_message(thread_id, ChatMessage(
            role="user", content=message,
            metadata={"mentioned_files": mentioned_paths} if mentioned_paths else {}))
        # A new turn invalidates any prior in-flight pills marker (a stopped/orphaned
        # earlier turn). Drop it so this turn's switch-back dedup is scoped to its own
        # message (finding 5); the orphan's pills stay as a normal message.
        self._store.clear_inflight_markers(thread_id)
        # Remember this turn's review toggle so a propose_mode → "edit" re-entry
        # (resolved via /mode-decision, which carries no step_review) honors it.
        self._step_review_by_thread[thread_id] = step_review

        seed = self._seed_for(thread_id)
        # On a continued turn (discuss), append the user's reply to the prior history
        # and replay it as the cache prefix (spec §12 clarify resume).
        seed_history = (seed + [{"role": "user", "content": turn_message}]) if seed else None
        # Clarify-resume is now driven by resolve_clarify (the gate carries resume_phase),
        # not a fresh user message: the main composer is disabled while a clarify gate is
        # pending, so the answer arrives via the card. A plain message here always
        # supersedes any pending gate (cleared above) and re-enters DECIDE.
        resume_phase = None
        # One id for this turn's in-flight pills message — lets the loop upsert it per
        # tool result and _finish finalize the SAME message (no duplicate). Finding 5.
        turn_id = uuid4().hex
        outcome = await self._run_loop(
            thread_id, channel_id, turn_message, seed_history=seed_history,
            step_review=step_review, phase=resume_phase, turn_id=turn_id,
            edit_is_resume=(resume_phase == "EDIT"), forced_skills=forced_skills)
        await self._finish(thread_id, channel_id, outcome, step_review, turn_id=turn_id)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd services/agentd-py && pytest tests/test_mentioned_files.py`
Expected: PASS

- [ ] **Step 5: Update the route to parse `mentioned_files`**

In `services/agentd-py/agentd/api/routes.py`, in `post_chat_message` (line ~1349), add parsing right after the existing `forced_skills` parsing:

```python
            _raw_forced = request.get("forced_skills")
            forced_skills = (
                [str(s) for s in _raw_forced] if isinstance(_raw_forced, list) else None
            )
            _raw_mentioned = request.get("mentioned_files")
            mentioned_files = (
                [
                    {"path": str(f.get("path", "")), "content": str(f.get("content", ""))}
                    for f in _raw_mentioned
                    if isinstance(f, dict) and f.get("path")
                ]
                if isinstance(_raw_mentioned, list) else None
            ) or None
```

Update the controller-path `handle_message` call (the `_active is not None` branch) to pass it through:

```python
                _chat_agent.launch_turn(  # type: ignore[attr-defined]
                    thread_id,
                    _chat_agent.handle_message(
                        thread_id, message, channel_id=channel_id,
                        step_review=step_review, forced_skills=forced_skills,
                        mentioned_files=mentioned_files),
                    channel_id=channel_id,
                )
```

(The legacy `ChatAgent` path below is unchanged — `mentioned_files` is controller-only, matching the existing convention for `write_doc`/MCP/skills.)

- [ ] **Step 6: Update `test_controller_route_detach.py`'s `_SlowController` signature**

In `services/agentd-py/tests/test_controller_route_detach.py`, update `_SlowController.handle_message` to accept the new kwarg (it currently has a fixed signature that routes.py's unconditional `mentioned_files=mentioned_files` kwarg would otherwise break):

```python
    async def handle_message(self, thread_id, message, channel_id, step_review=None,
                             forced_skills=None, mentioned_files=None):
        await self._gate.wait()
        self._broadcaster.broadcast(channel_id, {"type": "chat_done", "payload": {}})
```

- [ ] **Step 7: Run the route-detach test suite to confirm no regression**

Run: `cd services/agentd-py && pytest tests/test_controller_route_detach.py`
Expected: PASS

- [ ] **Step 8: Run the full backend test suite**

Run: `cd services/agentd-py && pytest`
Expected: PASS (check the summary line per CLAUDE.md's pytest gotcha — plain `pytest`, no extra `-q`).

- [ ] **Step 9: Commit**

```bash
git add services/agentd-py/agentd/api/routes.py services/agentd-py/agentd/chat/controller.py services/agentd-py/tests/test_controller_route_detach.py services/agentd-py/tests/test_mentioned_files.py
git commit -m "feat(chat): fold @-mentioned file content into the current turn only"
```

---

### Task 9: Clickable mention rendering in the transcript

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/UserMessage.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/messages/UserMessage.test.tsx` (create if it doesn't exist)
- Modify: `apps/vscode-extension/webview-ui/src/components/MessageRow.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/vscodeApi.ts` usage — no change needed, `vscode.postMessage` already imported where needed

**Interfaces:**
- Consumes: `ChatMessage.metadata.mentioned_files: string[]` (Task 8), `openFile` webview message (Task 5).
- Produces: `<UserMessage content={string} mentionedFiles={string[]}>` — extends the existing component's props (backward compatible: `mentionedFiles` defaults to `[]`).

- [ ] **Step 1: Write the failing `UserMessage` test**

```tsx
// apps/vscode-extension/webview-ui/src/components/messages/UserMessage.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { UserMessage } from "./UserMessage";
import { vscode } from "../../vscodeApi";

vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

describe("UserMessage mention rendering", () => {
  it("renders a tracked mention as clickable and posts openFile on click", () => {
    render(<UserMessage content="check @src/foo.py please" mentionedFiles={["src/foo.py"]} />);
    const link = screen.getByText("@src/foo.py");
    fireEvent.click(link);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFile", path: "src/foo.py" });
  });

  it("does not linkify an @ token that isn't a tracked mention", () => {
    render(<UserMessage content="email me @not-a-file" mentionedFiles={[]} />);
    expect(screen.queryByRole("button", { name: "@not-a-file" })).toBeNull();
  });

  it("still renders backtick code spans unchanged", () => {
    render(<UserMessage content="run `ls -la` please" mentionedFiles={[]} />);
    expect(screen.getByText("ls -la").tagName).toBe("CODE");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/messages/UserMessage.test.tsx`
Expected: FAIL — `mentionedFiles` prop doesn't exist.

- [ ] **Step 3: Update `UserMessage.tsx`**

```tsx
import { vscode } from "../../vscodeApi";

/**
 * Right-aligned user bubble.
 * Matches .ubub in the hi-fi mockup.
 *
 * Inline backtick spans rendered as <code> (mono, text-code) — no markdown engine.
 * mentionedFiles are the paths the composer's @ dropdown actually inserted for this
 * message (not a blind @-regex scan) — only those "@path" tokens render clickable.
 * Arbitrary border-radius matches the mockup's 12px 12px 4px 12px shape.
 */
export function UserMessage({
  content,
  mentionedFiles = [],
}: {
  content: string;
  mentionedFiles?: string[];
}) {
  const codeParts = content.split(/(`[^`]+`)/);

  function renderTextSegment(text: string, keyPrefix: string) {
    if (mentionedFiles.length === 0) return <span key={keyPrefix}>{text}</span>;
    const mentionTokens = mentionedFiles.map((p) => `@${p}`);
    const pattern = new RegExp(`(${mentionTokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`);
    const pieces = text.split(pattern);
    return (
      <span key={keyPrefix}>
        {pieces.map((piece, i) =>
          mentionTokens.includes(piece) ? (
            <span
              key={i}
              role="button"
              tabIndex={0}
              onClick={() => vscode.postMessage({ type: "openFile", path: piece.slice(1) })}
              style={{ color: "var(--color-accent)", cursor: "pointer", textDecoration: "underline" }}
            >
              {piece}
            </span>
          ) : (
            <span key={i}>{piece}</span>
          ),
        )}
      </span>
    );
  }

  return (
    <div
      className="self-end max-w-[86%] px-3 py-2 text-xs leading-relaxed text-text whitespace-pre-wrap break-words"
      style={{
        background: "linear-gradient(180deg, var(--color-surface-2), var(--color-surface))",
        border: "1px solid var(--color-border-strong)",
        boxShadow: "inset 0 1px 0 var(--hairline)",
        borderRadius: "12px 12px 4px 12px",
      }}
    >
      {codeParts.map((part, i) => {
        if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
          return (
            <code key={i} className="mono text-code">
              {part.slice(1, -1)}
            </code>
          );
        }
        return renderTextSegment(part, `seg-${i}`);
      })}
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run src/components/messages/UserMessage.test.tsx`
Expected: PASS

- [ ] **Step 5: Pass `mentionedFiles` through `MessageRow.tsx`**

In `apps/vscode-extension/webview-ui/src/components/MessageRow.tsx`, update the `msg.role === "user"` branch:

```tsx
      if (msg.role === "user") {
        return (
          <UserMessage
            content={msg.content}
            mentionedFiles={msg.metadata?.mentioned_files as string[] | undefined}
          />
        );
      }
```

- [ ] **Step 6: Run the full webview test suite**

Run: `cd apps/vscode-extension/webview-ui && npx vitest run`
Expected: PASS

- [ ] **Step 7: Typecheck**

Run: `cd apps/vscode-extension/webview-ui && npm run typecheck`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add apps/vscode-extension/webview-ui/src/components/messages/UserMessage.tsx apps/vscode-extension/webview-ui/src/components/messages/UserMessage.test.tsx apps/vscode-extension/webview-ui/src/components/MessageRow.tsx
git commit -m "feat(webview): clickable @-mention rendering in the transcript"
```

---

### Task 10: Full-suite verification + live smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the full TypeScript suite**

Run: `npm run build && npm run test && npm run typecheck`
Expected: all PASS across `editor-client` + `vscode-extension` (+ `webview-ui`, run separately per Task steps above if not covered by the root scripts — check `package.json` workspaces list first).

- [ ] **Step 2: Run the full Python suite**

Run: `cd services/agentd-py && pytest`
Expected: PASS — check the summary line, not the exit code of any piped command (per CLAUDE.md's pytest gotchas).

- [ ] **Step 3: Live smoke in the dev host**

Follow CLAUDE.md's "Opening the VS Code extension development host" + "Starting the backend for local testing" recipes with `CRUCIBLE_CHAT_CONTROLLER=1` and `CRUCIBLE_MEMORY_ENABLED=1` set. Verify:
- The memory-inspector header icon opens the panel (and, with `CRUCIBLE_MEMORY_ENABLED` unset, shows the graceful info message instead).
- The memory inspector's Recall Trace / Browser tabs render on the shared dark palette (no visibly different slate tone from chat/settings).
- Typing `/` in the composer shows a live dropdown with prompt + skill rows, badged; arrow keys + Enter insert the name without sending; a `/name args` typed blind (no dropdown navigation) still sends/expands as before.
- Typing `@` shows a live file dropdown; selecting a file inserts `@path`; sending a message with a mention produces a response that reflects the file's content; the sent message renders `@path` as a clickable link that opens the file in the editor.

- [ ] **Step 4: Report results to the user**

Summarize pass/fail per suite and note any live-smoke findings before considering the plan complete — do not claim success without having run these commands and observed their output (per superpowers:verification-before-completion).
