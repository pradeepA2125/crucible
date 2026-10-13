import { memo, useState } from "react";
import type { ToolEventView } from "../../types";
import type { ActivityStep } from "../../transcript/groupTranscript";
import { summarizeActivity } from "../../transcript/summarizeActivity";
import { DiffPanes } from "../shared/DiffPanes";
import { ActivityLine } from "./ActivityLine";
import { Icon } from "../Icon";

const basename = (path: string) => path.split("/").pop() || path;

// A tool batch emits every member with the same step thought, so a run of events sharing
// one thought is one model step: show the thought once, above that step's lines.
function splitByThought(events: ToolEventView[]): { thought?: string; events: ToolEventView[] }[] {
  const runs: { thought?: string; events: ToolEventView[] }[] = [];
  for (const event of events) {
    const last = runs[runs.length - 1];
    if (last && (!event.thought || event.thought === last.thought)) last.events.push(event);
    else runs.push({ thought: event.thought, events: [event] });
  }
  return runs;
}

function ToolsStep({ events }: { events: ToolEventView[] }) {
  return (
    <>
      {splitByThought(events).map((run, i) => (
        <div key={i} className="grid min-w-0 gap-1">
          {run.thought && <div data-testid="activity-thought" className="text-meta text-text-3">{run.thought}</div>}
          {run.events.map((event) => <ActivityLine key={event.id} event={event} />)}
        </div>
      ))}
    </>
  );
}

function EditRow({ step }: { step: Extract<ActivityStep, { type: "edit" }> }) {
  const [open, setOpen] = useState(false);
  const label = step.entries.length === 1
    ? `Edited ${basename(step.entries[0].path)}`
    : `Edited ${step.entries.length} files`;
  const add = step.entries.reduce((s, e) => s + e.additions, 0);
  const del = step.entries.reduce((s, e) => s + e.deletions, 0);
  return (
    <div className="grid min-w-0 gap-1">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex min-w-0 items-center gap-2 text-left text-body text-text-2 hover:text-text">
        <span className="truncate">{label}</span>
        {step.applied ? (
          <span className="shrink-0">
            <span style={{ color: "var(--diff-add)" }}>+{add}</span>{" "}
            <span style={{ color: "var(--diff-del)" }}>−{del}</span>
          </span>
        ) : <span className="shrink-0 text-text-3">Not applied</span>}
        <span className="shrink-0 text-text-3">{open ? "▾" : "›"}</span>
      </button>
      {open && <DiffPanes entries={step.entries} />}
    </div>
  );
}

// groupTranscript rebuilds step objects on every regroup but reuses the message-owned events/entries,
// so comparing those references keeps an unchanged group from re-rendering.
function sameSteps(a: ActivityStep[], b: ActivityStep[]): boolean {
  return a.length === b.length && a.every((step, i) => {
    const other = b[i];
    if (step.type !== other.type) return false;
    if (step.type === "tools" && other.type === "tools") return step.events === other.events;
    if (step.type === "edit" && other.type === "edit") return step.entries === other.entries && step.applied === other.applied;
    return step.type === "outcome" && other.type === "outcome" && step.text === other.text;
  });
}

export const ActivityGroup = memo(function ActivityGroup({ steps }: { steps: ActivityStep[] }) {
  const [open, setOpen] = useState(false);
  const summary = summarizeActivity(steps);
  return (
    <div data-testid="activity-group" className="grid min-w-0 gap-1.5">
      <button type="button" aria-expanded={open} aria-label={summary} onClick={() => setOpen(!open)}
        className="flex w-fit max-w-full min-w-0 items-center gap-2 py-1 text-left text-body text-text-3 hover:text-text-2 rounded-control">
        <span aria-hidden="true" className="shrink-0"><Icon name="list" size={13} /></span>
        <span data-testid="activity-summary" className="min-w-0 truncate">{summary}</span>
        <span aria-hidden="true" className="shrink-0"><Icon name={open ? "chev-d" : "chev-r"} size={11} /></span>
      </button>
      {open && (
        <div data-testid="activity-detail" className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-1.5 overflow-y-auto overscroll-contain"
          style={{ maxHeight: "320px" }}>
          {steps.map((step, i) => step.type === "tools" ? <ToolsStep key={i} events={step.events} />
            : step.type === "edit" ? <EditRow key={i} step={step} />
            : <div key={i} className="text-body text-text-3">{step.text.replace(/^[✓✗]\s*/, "")}</div>)}
        </div>
      )}
    </div>
  );
}, (prev, next) => sameSteps(prev.steps, next.steps));
