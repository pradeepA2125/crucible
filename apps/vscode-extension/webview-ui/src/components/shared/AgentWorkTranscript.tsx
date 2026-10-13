import { useMemo, useRef } from "react";
import type { ToolEventView } from "../../types";
import { TransientActivity } from "../activity/TransientActivity";
import { TranscriptItems, type TranscriptItemsProps } from "./TranscriptItems";

const NO_TOOLS: ToolEventView[] = [];

/** Child history follows the main chat: finished work folds together, with one
 * current-step disclosure for the unfinished batch. Live work joins the same
 * group when it seals, preserving the group's open state. */
export function AgentWorkTranscript({ messages, live = NO_TOOLS, running = false, waiting = false, startedAt,
  renderMessage, showChanges = true }: Pick<TranscriptItemsProps, "messages" | "renderMessage" | "showChanges"> & {
  live?: ToolEventView[]; running?: boolean; waiting?: boolean; startedAt?: string | null;
}) {
  const mountedAt = useRef(Date.now());
  // A stopped child can retain an unfinished last call. Keep it inspectable in
  // the folded history instead of showing a step as still running.
  const finished = useMemo(() => running ? live.filter(event => event.done) : live, [live, running]);
  const pending = running && live.some(event => !event.done);
  const bubble = useMemo(() => ({ text: "", thinkingEntries: [], activeThinkingChunk: "", toolEvents: live }), [live]);
  const start = startedAt ? Date.parse(startedAt) : mountedAt.current;
  return <TranscriptItems messages={messages} turnActive={running} streamingToolEvents={finished}
    renderMessage={renderMessage} showChanges={showChanges}
    renderTail={() => pending && <TransientActivity bubble={bubble} waiting={waiting}
      startedAt={Number.isFinite(start) ? start : mountedAt.current} />} />;
}
