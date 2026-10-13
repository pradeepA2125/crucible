import type { TranscriptItem } from "./groupTranscript";

export type WorkItem = Exclude<TranscriptItem, { kind: "changes" }>;
export type WorkSectionItem = TranscriptItem | { kind: "work"; key: string; items: WorkItem[]; running: boolean };

/** Keep the narration and its tool batches inside one turn's work disclosure.
 * Final answers/reports, user messages and activation dividers stay outside it.
 * Legacy interactive diff cards stay reachable without opening a disclosure. */
export function groupWork(items: TranscriptItem[], running: boolean, hasLiveWork: boolean): WorkSectionItem[] {
  const result: WorkSectionItem[] = [];
  let work: WorkItem[] = [];
  let key = "work-start";
  const flush = (active = false) => {
    const hasWork = work.some(item => item.kind === "activity"
      || item.kind === "message" && (item.msg.metadata?.progress === true || item.msg.metadata?.breadcrumb === true));
    // A roster or board card on its own is navigation, not a work history.
    if (hasWork || active && hasLiveWork) result.push({ kind: "work", key, items: work, running: active });
    else result.push(...work);
    work = [];
  };
  for (const item of items) {
    if (item.kind === "changes") {
      flush();
      result.push(item);
      continue;
    }
    if (item.kind === "message") {
      const { msg, index } = item;
      const standaloneCard = ["plan_card", "task_card", "scope_card", "validation_card", "command_card"].includes(msg.type);
      const boundary = msg.role === "user" || msg.metadata?.divider === true || msg.metadata?.report === true
        || standaloneCard
        || msg.type === "diff_card" && !msg.metadata?.resolved
        || (msg.type === "text" || msg.type === "diff_summary") && !!msg.content.trim()
          && msg.metadata?.progress !== true && msg.metadata?.breadcrumb !== true;
      if (boundary) {
        flush();
        result.push(item);
        key = `work-after-${msg.id ?? index}`;
        continue;
      }
    }
    work.push(item);
  }
  flush(running);
  return result;
}
