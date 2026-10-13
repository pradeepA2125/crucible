import { useId, useState } from "react";
import type { ContextUsage } from "../types";
import { formatTokens } from "../statusWords";

interface Props {
  usage: ContextUsage | null;
  stale: boolean;
}

const capacityNames = {
  chatgpt_catalog: "ChatGPT catalog",
  claude_catalog: "Claude catalog",
  configured: "Configured",
  unknown: "Unknown",
};
const qualityNames = {
  estimated: "Estimated",
  provider_reported: "Provider reported",
  unavailable: "Unavailable",
};

/** Occupancy of the main model request, never a cumulative or account-plan total.
 * Capacity comes exclusively from the host snapshot (not the composer model menu).
 */
export function ContextMeter({ usage, stale }: Props) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const input = usage?.input_tokens ?? null;
  const window = usage?.context_window ?? null;
  const ratio = input !== null && window !== null ? input / window * 100 : null;
  const fill = ratio === null ? 0 : Math.min(100, Math.max(0, ratio));
  const prefix = usage?.status === "last_request" ? "Last context" : "Context";
  const estimate = usage?.input_quality === "estimated" ? "~" : "";
  const missing = usage?.status === "awaiting_request" ? "awaiting request"
    : usage?.status === "invalidated" ? "invalidated" : "unavailable";
  const awaiting = usage?.status === "awaiting_request" && input === null;
  const initialValue = window === null ? missing : awaiting
    ? `ready · ${formatTokens(window)} limit`
    : `${formatTokens(window)} limit · ${missing}`;
  const value = input === null ? initialValue : ratio === null
    ? `${estimate}${formatTokens(input)} tokens · limit unknown`
    : `${estimate}${Math.round(ratio)}%`;
  const label = `${prefix} ${value}${stale ? " · stale" : ""}`;
  const rawRatio = ratio === null ? null : ratio.toLocaleString("en-US", { maximumFractionDigits: 2 });

  return (
    <span className="flex max-w-full items-center gap-1" data-testid="context-meter">
      <button
        type="button"
        aria-label={`${label}; context details`}
        aria-expanded={open}
        aria-describedby={open ? detailsId : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
          }
        }}
        className="min-h-6 min-w-0 cursor-pointer rounded-[7px] border-0 bg-transparent px-1.5 text-left font-mono text-[9.5px] tabular-nums text-text-2 hover:bg-surface-2 focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent"
      >
        {label}
      </button>
      {ratio !== null && (
        <span role="meter" aria-label="Main request context occupancy"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={fill}
          aria-valuetext={`${estimate}${rawRatio}%${stale ? ", stale" : ""}`}
          className="h-1 w-5 shrink-0 overflow-hidden rounded-full bg-surface-3">
          <span data-testid="context-meter-fill" className="block h-full bg-accent"
            style={{ width: `${fill}%` }} />
        </span>
      )}
      {open && (
        <div id={detailsId} role="tooltip" data-testid="context-details"
          className="absolute bottom-full left-0 z-40 mb-1 w-[320px] max-w-full rounded-[8px] border border-border-strong bg-surface-2 p-3 text-[11px] leading-relaxed text-text-2 shadow-lg break-words">
          <div className="font-semibold text-text">{label}</div>
          <div>Main request input: {input === null ? awaiting ? "Not measured yet" : "Unavailable" : `${input.toLocaleString("en-US")} tokens`}</div>
          <div>Input source: {awaiting ? "Awaiting first request" : qualityNames[usage?.input_quality ?? "unavailable"]}</div>
          <div>Context window: {window === null ? "Unknown" : `${window.toLocaleString("en-US")} tokens`}</div>
          <div>Capacity source: {capacityNames[usage?.capacity_source ?? "unknown"]}</div>
          {rawRatio !== null && <div>Input / window: {estimate}{rawRatio}%{ratio! > 100 ? " (over capacity; bar capped at 100%)" : ""}</div>}
          <div>Model: {usage ? `${usage.provider} / ${usage.model}` : "Unknown"}</div>
          <div>Observed: {usage?.observed_at ?? (awaiting ? "No request yet" : "Unknown")}</div>
          {stale && <div>Stale snapshot — may not describe the next request.</div>}
          {awaiting && <p className="mt-1 text-text-3">Usage appears when Crucible prepares the first model request, including instructions and tool definitions.</p>}
          <p className="mt-1 text-text-3">Main model request input only, not cumulative thread tokens or account-plan usage. This is not a prediction of the next message.</p>
        </div>
      )}
    </span>
  );
}
