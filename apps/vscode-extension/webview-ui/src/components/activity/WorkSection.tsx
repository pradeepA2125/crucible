import { useId, useState, type ReactNode } from "react";
import { Icon } from "../Icon";

/** Explicit expansion choices survive completion and live-to-history sealing.
 * Otherwise live work is open and finished work folds away. Keep the inner
 * disclosures mounted so reopening this section preserves inspected tools. */
export function WorkSection({ running, children }: { running: boolean; children: ReactNode }) {
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = choice ?? running;
  const id = useId();
  return (
    <section data-testid="work-section" className="grid min-w-0 gap-6">
      <button type="button" aria-expanded={open} aria-controls={id}
        onClick={() => setChoice(!open)}
        className="flex min-w-0 items-center gap-2 py-1 text-left text-body text-text-3 hover:text-text-2 rounded-control"
        style={open ? { borderBottom: "1px solid var(--color-border)", paddingBottom: "12px" } : undefined}>
        <span>{running ? "Working" : "Worked"}</span>
        <Icon name={open ? "chev-d" : "chev-r"} size={11} />
      </button>
      <div id={id} hidden={!open} style={!open ? { display: "none" } : undefined}
        onFocusCapture={() => setChoice(true)}
        data-testid="work-detail" className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-6">
        {children}
      </div>
    </section>
  );
}
