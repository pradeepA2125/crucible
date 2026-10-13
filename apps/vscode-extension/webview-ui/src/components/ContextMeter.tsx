import { useCallback, useId, useRef, useState } from "react";
import type { ContextUsage, ThreadUsageView } from "../types";
import { formatTokens } from "../statusWords";
import { threadBreakdown, usageText } from "../usage";
import { usePopover } from "./shared/usePopover";
const capacityNames = { chatgpt_catalog: "ChatGPT catalog", claude_catalog: "Claude catalog", configured: "Configured", unknown: "Unknown" };
const qualityNames = { estimated: "Estimated", provider_reported: "Provider reported", unavailable: "Unavailable" };

/** One compact ring; its card keeps per-request context separate from thread/team totals. */
export function ContextMeter({ usage, stale, threadUsage = null, teamNames = {} }: {
  usage: ContextUsage | null; stale: boolean; threadUsage?: ThreadUsageView | null; teamNames?: Record<string, string>;
}) {
  const root = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const pinned = useRef(false);
  const id = useId();
  const close = useCallback(() => { pinned.current = false; setOpen(false); }, []);
  const position = usePopover(root, open, close, 320, false);
  const input = usage?.input_tokens ?? null, windowSize = usage?.context_window ?? null;
  const ratio = input !== null && windowSize !== null && windowSize > 0 ? input / windowSize * 100 : null;
  const fill = ratio === null ? 0 : Math.min(100, Math.max(0, ratio));
  const prefix = usage?.status === "last_request" ? "Last context" : "Context";
  const estimate = usage?.input_quality === "estimated" ? "~" : "";
  const missing = usage?.status === "awaiting_request" ? "awaiting request" : usage?.status === "invalidated" ? "invalidated" : "unavailable";
  const awaiting = usage?.status === "awaiting_request" && input === null;
  const initial = windowSize === null ? missing : awaiting ? `ready · ${formatTokens(windowSize)} limit` : `${formatTokens(windowSize)} limit · ${missing}`;
  const value = input === null ? initial : ratio === null ? `${estimate}${formatTokens(input)} tokens · limit unknown` : `${estimate}${Math.round(ratio)}%`;
  const label = `${prefix} ${value}${stale ? " · stale" : ""}`;
  const rawRatio = ratio === null ? null : ratio.toLocaleString("en-US", { maximumFractionDigits: 2 });
  const total = threadUsage?.total;
  return (
    <span ref={root} className="relative shrink-0" data-testid="context-meter"
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => { if (!pinned.current && !root.current?.contains(document.activeElement)) setOpen(false); }}>
      <button type="button" aria-label={`${label}; context details`} aria-expanded={open} aria-describedby={open ? id : undefined}
        onFocus={() => setOpen(true)} onBlur={e => { if (!pinned.current && !root.current?.contains(e.relatedTarget as Node)) setOpen(false); }}
        onClick={() => { pinned.current = true; setOpen(true); }}
        className="flex h-7 w-7 items-center justify-center rounded-control text-text-3 hover:bg-surface-2 hover:text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--focus-ring)]">
        <svg width="16" height="16" viewBox="0 0 16 16" role={ratio === null ? undefined : "meter"} aria-hidden={ratio === null ? true : undefined} aria-label="Main request context occupancy"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={fill} aria-valuetext={ratio === null ? missing : `${estimate}${rawRatio}%${stale ? ", stale" : ""}`}>
          <circle cx="8" cy="8" r="6" fill="none" stroke="var(--border-strong)" strokeWidth="2" />
          <circle data-testid="context-meter-fill" cx="8" cy="8" r="6" fill="none" stroke="var(--accent)" strokeWidth="2"
            strokeDasharray={`${fill / 100 * 37.7} 37.7`} transform="rotate(-90 8 8)" />
        </svg>
      </button>
      {open && <div id={id} role="tooltip" data-testid="context-details" style={position}
        className="absolute bottom-full z-50 mb-2 max-h-[65vh] overflow-y-auto overscroll-contain rounded-card border border-border-strong bg-surface-3 p-3 text-meta leading-relaxed text-text-2 [overflow-wrap:anywhere]">
        <div className="font-semibold text-text">{label}</div>
        <div>Main request input: {input === null ? awaiting ? "Not measured yet" : "Unavailable" : `${input.toLocaleString("en-US")} tokens`}</div>
        <div>Input source: {awaiting ? "Awaiting first request" : qualityNames[usage?.input_quality ?? "unavailable"]}</div>
        <div>Context window: {windowSize === null ? "Unknown" : `${windowSize.toLocaleString("en-US")} tokens`}</div>
        <div>Capacity source: {capacityNames[usage?.capacity_source ?? "unknown"]}</div>
        {rawRatio !== null && <div>Input / window: {estimate}{rawRatio}%{ratio! > 100 ? " (over capacity; ring capped at 100%)" : ""}</div>}
        <div>Model: {usage ? `${usage.provider} / ${usage.model}` : "Unknown"}</div>
        <div>Observed: {usage?.observed_at ?? (awaiting ? "No request yet" : "Unknown")}</div>
        {stale && <div>Stale snapshot — may not describe the next request.</div>}
        {awaiting && <p className="mt-1 text-caption text-text-3">Usage appears when Crucible prepares the first model request, including instructions and tool definitions.</p>}
        <p className="mt-1 text-caption text-text-3">Main model request input only, not cumulative thread tokens or account-plan usage. This is not a prediction of the next message.</p>
        {total && total.requests > 0 && <div data-testid="usage-breakdown" className="mt-2 grid gap-1 border-t border-border pt-2">
          <h3 className="font-medium text-text">This thread</h3><p>{usageText(total)}</p>
          {threadBreakdown(threadUsage!, teamNames).map(row => <div key={row.key} className="grid gap-0.5"><span className="text-text-2">{row.label}</span><span className="text-caption text-text-3">{usageText(row.usage)}</span></div>)}
        </div>}
      </div>}
    </span>
  );
}
