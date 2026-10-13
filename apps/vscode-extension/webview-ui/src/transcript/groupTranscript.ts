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

// A pending diff card is interactive (Apply/Discard) and must stay a message; only resolved cards are activity.
function isResolvedDiffCard(msg: ChatMsg): boolean {
  return msg.type === "diff_card" && !!msg.metadata?.resolved;
}

export function isActivityMember(msg: ChatMsg): boolean {
  return isResolvedDiffCard(msg) || isOutcome(msg) || isPillsOnly(msg);
}

// The live memory_compacted line is not persisted, so it must not split a group that
// a reloaded thread (without it) would show as one.
function isCompactionNotice(msg: ChatMsg): boolean {
  return msg.role === "agent" && msg.metadata?.breadcrumb === true && msg.content.startsWith("🗜️");
}

function toSteps(msg: ChatMsg): ActivityStep[] {
  if (msg.type === "diff_card") {
    const steps: ActivityStep[] = [];
    // A legacy inline-change card carries the pills of the run that produced it.
    const events = toolEvents(msg);
    if (events.length) steps.push({ type: "tools", events });
    steps.push({
      type: "edit",
      entries: (msg.metadata?.diff_entries as DiffEntry[] | undefined) ?? [],
      applied: msg.metadata?.resolved !== "discarded",
    });
    return steps;
  }
  if (isOutcome(msg)) return [{ type: "outcome", text: msg.content }];
  const events = toolEvents(msg);
  return events.length ? [{ type: "tools", events }] : [];
}

// Keyed by the original message so a regroup hands MessageRow's memo the same object.
const answerWithoutPills = new WeakMap<ChatMsg, ChatMsg>();
function withoutPills(msg: ChatMsg): ChatMsg {
  let split = answerWithoutPills.get(msg);
  if (!split) {
    const { tool_events: _te, thinking_log: _tl, ...rest } = msg.metadata ?? {};
    split = { ...msg, metadata: rest };
    answerWithoutPills.set(msg, split);
  }
  return split;
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

  // Notices that arrived inside a group render right after it, so the group stays whole.
  let deferred: TranscriptItem[] = [];
  const closeGroup = () => {
    if (group && group.steps.length) items.push({ kind: "activity", key: group.key, steps: group.steps });
    group = null;
    items.push(...deferred);
    deferred = [];
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
      const steps = toSteps(msg);
      if (!steps.length) return;
      group ??= { key: msg.id ?? `activity-${index}`, steps: [] };
      group.steps.push(...steps);
      turnSteps.push(...steps);
      return;
    }
    if (isCompactionNotice(msg)) {
      if (group) deferred.push({ kind: "message", msg, index });
      else items.push({ kind: "message", msg, index });
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
      items.push({ kind: "message", msg: withoutPills(msg), index });
      return;
    }
    closeGroup();
    items.push({ kind: "message", msg, index });
  });
  closeTurn();
  return items;
}
