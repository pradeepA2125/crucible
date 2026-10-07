import { useState } from "react";
import { formatTokens } from "../statusWords";
import type { ThreadUsageView } from "../types";
import { threadBreakdown, usageText, usageTitle } from "../usage";

/**
 * The thread's total token usage — main agent, every agent and team — beside the
 * composer's model chip. Hover or focus opens the breakdown. The live meter above the
 * composer (WorkBar) stays the per-call view; this is the running total.
 */
export function UsageChip({ usage, teamNames }: {
  usage: ThreadUsageView | null;
  teamNames: Record<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const total = usage?.total;
  if (!usage || !total || total.requests === 0) return null;
  const rows = threadBreakdown(usage, teamNames);
  return (
    <span className="relative"
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" data-testid="usage-chip"
        aria-label={`Thread usage: ${usageTitle(total)}`} aria-expanded={open}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
        className="flex h-6 cursor-default items-center gap-1 whitespace-nowrap rounded-[7px] border-0 bg-transparent px-1.5 font-mono tabular-nums text-text-3 transition-colors duration-150 hover:bg-surface-2 hover:text-text-2"
        style={{ fontSize: "9.5px" }}>
        ↑{formatTokens(total.input)} ↓{formatTokens(total.output)}
      </button>
      {open && (
        <div role="tooltip" data-testid="usage-breakdown"
          className="anim-rise absolute bottom-full left-0 z-50 mb-1.5 w-[260px] rounded-[10px] border p-2"
          style={{ background: "var(--color-surface-2)", borderColor: "var(--color-border-strong)",
                   boxShadow: "0 10px 30px -10px rgba(0,0,0,.7), inset 0 1px 0 var(--hairline)" }}>
          <div className="mb-1 text-[9.5px] font-semibold uppercase tracking-wide text-text-3">
            This thread
          </div>
          <div className="mb-1.5 text-[11px] text-text">{usageText(total)}</div>
          <div className="grid gap-1 border-t pt-1.5" style={{ borderColor: "var(--hairline)" }}>
            {rows.map((row) => (
              <div key={row.key} className="grid gap-0.5">
                <span className="truncate text-[10.5px] text-text-2">{row.label}</span>
                <span className="font-mono text-[10px] tabular-nums text-text-3">{usageText(row.usage)}</span>
              </div>
            ))}
          </div>
          <p className="mt-1.5 text-[9.5px] leading-snug text-text-4">
            Input counts the context once per request; the cached share was served from the
            provider's prompt cache.
          </p>
        </div>
      )}
    </span>
  );
}
