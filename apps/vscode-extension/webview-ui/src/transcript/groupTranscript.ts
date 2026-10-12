import type { ChatMsg, DiffEntry, ToolEventView } from "../types";

export type ActivityStep =
  | { type: "tools"; events: ToolEventView[] }
  | { type: "edit"; entries: DiffEntry[]; applied: boolean }
  | { type: "outcome"; text: string };

export interface FileChange { path: string; additions: number; deletions: number; applied: boolean; diff?: string }

export type TranscriptItem =
  | { kind: "message"; msg: ChatMsg; index: number }
  | { kind: "activity"; key: string; steps: ActivityStep[] }
  | { kind: "changes"; key: string; files: FileChange[] };

// Edit outcomes written before metadata.edit_outcome existed are only recognisable by text.
const LEGACY_OUTCOME_PREFIXES = ["✓ Edit accepted:", "✗ Edit rejected:", "✗ Not applied:"];

function toolEvents(msg: ChatMsg): ToolEventView[] {
  return (msg.metadata?.tool_events as ToolEventView[] | undefined) ?? [];
}

function isOutcome(msg: ChatMsg): boolean {
  if (msg.metadata?.breadcrumb !== true) return false;
  return msg.metadata?.edit_outcome === true || LEGACY_OUTCOME_PREFIXES.some((p) => msg.content.startsWith(p));
}

function isPillsOnly(msg: ChatMsg): boolean {
  return msg.role === "agent" && msg.type === "text" && !msg.content.trim()
    && msg.metadata?.progress !== true && msg.metadata?.breadcrumb !== true;
}

export function isActivityMember(msg: ChatMsg): boolean {
  return msg.type === "diff_card" || isOutcome(msg) || isPillsOnly(msg);
}

function toStep(msg: ChatMsg): ActivityStep | null {
  if (msg.type === "diff_card") {
    return {
      type: "edit",
      entries: (msg.metadata?.diff_entries as DiffEntry[] | undefined) ?? [],
      applied: msg.metadata?.resolved !== "discarded",
    };
  }
  if (isOutcome(msg)) return { type: "outcome", text: msg.content };
  const events = toolEvents(msg);
  return events.length ? { type: "tools", events } : null;
}

// Sums a turn's applied edits per file; a file that only ever had rejected edits is listed as not applied.
function turnChanges(steps: ActivityStep[]): FileChange[] {
  const files = new Map<string, FileChange>();
  for (const step of steps) {
    if (step.type !== "edit") continue;
    for (const entry of step.entries) {
      const current = files.get(entry.path) ?? { path: entry.path, additions: 0, deletions: 0, applied: false, diff: undefined };
      if (step.applied) {
        current.additions += entry.additions;
        current.deletions += entry.deletions;
        current.applied = true;
        current.diff = entry.unified_diff ?? current.diff;
      }
      files.set(entry.path, current);
    }
  }
  return [...files.values()];
}

export function groupTranscript(messages: ChatMsg[]): TranscriptItem[] {
  const items: TranscriptItem[] = [];
  let group: { key: string; steps: ActivityStep[] } | null = null;
  let turnSteps: ActivityStep[] = [];
  let turnKey = "start";

  const closeGroup = () => {
    if (group && group.steps.length) items.push({ kind: "activity", key: group.key, steps: group.steps });
    group = null;
  };
  const closeTurn = () => {
    closeGroup();
    const files = turnChanges(turnSteps);
    // A diff card with no entries has nothing to list; an empty changes card would read "No changes applied".
    if (files.length) items.push({ kind: "changes", key: `changes-${turnKey}`, files });
    turnSteps = [];
  };

  messages.forEach((msg, index) => {
    if (msg.role === "user") {
      closeTurn();
      turnKey = msg.id ?? String(index);
      items.push({ kind: "message", msg, index });
      return;
    }
    if (isActivityMember(msg)) {
      const step = toStep(msg);
      if (!step) return;
      group ??= { key: msg.id ?? `activity-${index}`, steps: [] };
      group.steps.push(step);
      turnSteps.push(step);
      return;
    }
    // An answer finalizes its in-flight pills message: the pills are activity, the text is a boundary after them.
    const events = toolEvents(msg);
    if (msg.role === "agent" && msg.type === "text" && msg.content.trim() && events.length
      && msg.metadata?.progress !== true && msg.metadata?.breadcrumb !== true) {
      const step: ActivityStep = { type: "tools", events };
      group ??= { key: msg.id ? `${msg.id}-activity` : `activity-${index}`, steps: [] };
      group.steps.push(step);
      turnSteps.push(step);
      closeGroup();
      const { tool_events: _te, thinking_log: _tl, ...rest } = msg.metadata ?? {};
      items.push({ kind: "message", msg: { ...msg, metadata: rest }, index });
      return;
    }
    closeGroup();
    items.push({ kind: "message", msg, index });
  });
  closeTurn();
  return items;
}
