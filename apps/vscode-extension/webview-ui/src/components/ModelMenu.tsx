import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { ClaudeLogo } from "./shared/ClaudeBrand";
import { CLAUDE_USAGE_URL } from "../claude";
import { ChatGPTLogo } from "./shared/ChatGPTBrand";
import { CHATGPT_USAGE_URL } from "../chatgpt";
import type { ModelOption } from "../types";
import { vscode } from "../vscodeApi";
import { effortStops, EFFORT_LABEL, type EffortLevel, type EffortView } from "../effort";
import { EffortSlider } from "./EffortSlider";
import { usePopover } from "./shared/usePopover";
const optionKey = (opt: { backend: string; model: string }) => `${opt.backend}:${opt.model}`;
/** One header per provider, in first-appearance order: repeating it on every row doubled the list. */
function groupByProvider(options: ModelOption[]): [string, ModelOption[]][] {
  const groups = new Map<string, ModelOption[]>();
  for (const opt of options) groups.set(opt.label, [...(groups.get(opt.label) ?? []), opt]);
  return [...groups];
}

/** Model and effort share a popover; provider acknowledgements own the displayed state. */
export function ModelMenu() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<{ backend: string; model: string } | null>(null);
  const [options, setOptions] = useState<ModelOption[]>([]);
  const [swapping, setSwapping] = useState<string | null>(null);
  const [plan, setPlan] = useState<"chatgpt" | "claude" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [effort, setEffort] = useState<EffortView>({ level: null, support: null });
  const [effortPending, setEffortPending] = useState<EffortLevel | "default" | null>(null);
  const [effortError, setEffortError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const chip = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const modelRequest = useRef<string | null>(null);
  const effortRequest = useRef<EffortLevel | "default" | null>(null);
  const close = useCallback(() => setOpen(false), []);
  const position = usePopover(root, open, close);
  useEffect(() => {
    vscode.postMessage({ type: "listModels" });
    const onMessage = (e: MessageEvent) => {
      const m = e.data;
      if (m?.type === "modelList") {
        setCurrent(m.current ?? null); setOptions(m.options ?? []);
        setPlan(m.current?.backend === "claude_subscription" && m.usesClaudePlan ? "claude"
          : m.current?.backend === "chatgpt" && m.usesChatgptPlan ? "chatgpt" : null);
        setEffort(m.effort ?? { level: null, support: null });
        setError(null); setEffortError(null);
        if (modelRequest.current !== null) { modelRequest.current = null; setSwapping(null); setOpen(false); chip.current?.focus(); }
        if (effortRequest.current !== null) { effortRequest.current = null; setEffortPending(null); }
      } else if (m?.type === "modelSwapError") {
        modelRequest.current = null; setSwapping(null); setError(m.message);
      } else if (m?.type === "effortSwapError") {
        effortRequest.current = null; setEffortPending(null); setEffortError(m.message);
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);
  useEffect(() => {
    if (open) list.current?.querySelector<HTMLElement>('[aria-pressed="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [open]);
  const busy = swapping !== null || effortPending !== null;
  function chooseModel(opt: ModelOption) {
    if (opt.active || modelRequest.current !== null || effortRequest.current !== null) return;
    modelRequest.current = optionKey(opt); setSwapping(modelRequest.current); setError(null);
    vscode.postMessage({ type: "setModel", backend: opt.backend, model: opt.model });
  }
  function chooseEffort(level: EffortLevel | null) {
    if (modelRequest.current !== null || effortRequest.current !== null || !effortStops(effort.support).some(row => row.value === level)) return;
    effortRequest.current = level ?? "default"; setEffortPending(effortRequest.current); setEffortError(null);
    vscode.postMessage({ type: "setReasoningEffort", level });
  }
  const name = current ? options.find(opt => opt.active && opt.model === current.model)?.display ?? current.model : null;
  const rows = effortStops(effort.support);
  const value = effortPending === "default" ? null : effortPending ?? effort.level;
  const effortLabel = rows.length === 0 ? null : value === null ? "Default" : rows.find(row => row.value === value)?.label ?? EFFORT_LABEL[value];
  return (
    <div ref={root} className="relative min-w-0 max-w-full">
      <button ref={chip} type="button" onClick={() => setOpen(!open)} aria-haspopup="dialog" aria-expanded={open}
        aria-label={name ? `Model: ${name}${effortLabel ? ` · ${effortLabel}` : ""}` : "Select model"}
        title={name ? `${current?.backend} / ${name}` : "Select model"}
        className="flex h-7 min-w-0 max-w-full items-center gap-1 rounded-control px-1.5 text-meta text-text-2 hover:bg-surface-2 hover:text-text">
        <Icon name="spark" size={12} /><span className="min-w-0 max-w-[150px] truncate">{name ?? "Model"}</span>
        {effortLabel && <span className="shrink-0">· {effortLabel}</span>}
        {busy ? <span aria-label="Checking with provider">…</span> : <Icon name="chev-d" size={10} />}
      </button>
      {open && <div role="dialog" aria-label="Model and reasoning effort" style={position}
        className="absolute bottom-full z-50 mb-2 flex max-h-[65vh] flex-col overflow-hidden rounded-card border border-border-strong bg-surface-3 text-meta [overflow-wrap:anywhere]">
        {plan && <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border px-3 py-2 text-caption text-text-3">
          {plan === "claude" ? <ClaudeLogo size={12} /> : <ChatGPTLogo size={12} />}
          <span>{plan === "claude" ? "Using Claude plan" : "Using ChatGPT plan"}</span>
          <a href={plan === "claude" ? CLAUDE_USAGE_URL : CHATGPT_USAGE_URL} onClick={e => { e.preventDefault(); vscode.postMessage({ type: "settings/openExternal", url: plan === "claude" ? CLAUDE_USAGE_URL : CHATGPT_USAGE_URL }); }}
            className="text-accent underline hover:text-text">Manage usage</a>
        </div>}
        <div ref={list} data-testid="model-list" role="group" aria-label="Models"
          className="min-h-0 max-h-[240px] overflow-y-auto overscroll-contain p-1">
          {groupByProvider(options).map(([label, group]) => <div key={label} role="group" aria-label={label}>
            <div aria-hidden="true" className="truncate px-2 pb-0.5 pt-2 text-caption text-text-3">{label}</div>
            {group.map(opt => <button key={optionKey(opt)} type="button" disabled={busy} aria-pressed={opt.active} onClick={() => chooseModel(opt)}
              title={opt.display ?? opt.model}
              className="flex w-full min-w-0 items-center gap-2 rounded-control px-2 py-1 text-left text-body text-text hover:bg-surface-2 disabled:opacity-50">
              <span className="sr-only">{label}: </span>
              <span className="min-w-0 flex-1 truncate">{opt.display ?? opt.model}</span>
              {swapping === optionKey(opt) && <span aria-hidden="true">…</span>}
              {opt.active && <Icon name="check" size={12} />}
            </button>)}
          </div>)}
          {!options.length && <p className="p-2 text-caption text-text-3">No validated providers yet — add one in Settings.</p>}
        </div>
        {error && <p role="alert" className="shrink-0 border-t border-border px-3 py-2 text-caption text-red">{error}</p>}
        {/* Pinned below the scrolling list, so effort never needs a scroll to reach. */}
        <div className="shrink-0">
          <EffortSlider effort={effort} pending={effortPending} error={effortError} busy={swapping !== null} onChoose={chooseEffort} />
          <button type="button" onClick={() => { vscode.postMessage({ type: "openSettings" }); setOpen(false); }}
            className="flex w-full items-center gap-1.5 border-t border-border px-3 py-2 text-meta text-text-2 hover:bg-surface-2">
            <Icon name="gear" size={12} />Provider settings…
          </button>
        </div>
      </div>}
    </div>
  );
}
