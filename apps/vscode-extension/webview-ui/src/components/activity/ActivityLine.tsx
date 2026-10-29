import { useId, useState } from "react";
import type { ToolEventView } from "../../types";
import { toolLine } from "../../transcript/summarizeActivity";
import { Icon } from "../Icon";
import { ToolDetailPanel } from "../shared/ToolDetailPanel";
import { toolIcon } from "../shared/tool-icon";

/** One tool step as one line (spec §2): icon, verb, muted target. A native button, so the
 * step's input/output opens from the keyboard as well as the mouse. While the step runs,
 * only the verb shimmers (static under reduced motion — see .shimmer-text). */
export function ActivityLine({ event }: { event: ToolEventView }) {
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const { verb, target } = toolLine(event);
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-1">
      <button type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}
        className="flex min-w-0 items-center gap-2 text-left text-body text-text-2 hover:text-text rounded-control focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--focus-ring)]">
        <span aria-hidden="true" className="shrink-0 text-text-3"><Icon name={toolIcon(event.tool)} size={13} /></span>
        <span className={event.done ? "shrink-0" : "shrink-0 shimmer-text"}>{verb}</span>
        {target && <span className="min-w-0 truncate text-text-3">{target}</span>}
        {event.isError && <span className="shrink-0" style={{ color: "var(--danger)" }}>failed</span>}
      </button>
      {event.approvalNote && <p className="min-w-0 pl-[21px] text-caption text-text-3">{event.approvalNote}</p>}
      {open && <div id={panelId} className="min-w-0"><ToolDetailPanel event={event} /></div>}
    </div>
  );
}
