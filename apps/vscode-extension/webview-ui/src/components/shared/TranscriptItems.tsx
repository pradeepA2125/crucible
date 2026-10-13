import { Fragment, useMemo, type ReactNode } from "react";
import type { ChatMsg, ToolEventView } from "../../types";
import { groupTranscript } from "../../transcript/groupTranscript";
import { MessageRow } from "../MessageRow";
import { ActivityGroup } from "../activity/ActivityGroup";
import { TurnChanges } from "../activity/TurnChanges";

const EMPTY_PLAN_VERSIONS = new Map<number, number | undefined>();
const NO_REWIND = () => {};

/**
 * The transcript as grouped items. Live tool steps (the streaming bubble's finished tool events)
 * join the grouping as a trailing tools-only message, so work shown live sits in the very
 * group it is sealed into (same index, same key) and nothing jumps when the bubble seals.
 * `renderTail` draws the live elements after the transcript.
 */
export interface TranscriptItemsProps {
  messages: ChatMsg[]; turnActive?: boolean; queuedIds?: string[];
  /** The turn is still running (turnActive or input disabled): its changes card waits for the end. */
  turnRunning?: boolean;
  planVersions?: Map<number, number | undefined>; onRewindRequest?: (id: string) => void;
  streamingToolEvents?: ToolEventView[];
  renderTail?: () => ReactNode;
  renderMessage?: (msg: ChatMsg, index: number) => ReactNode;
  /** Team chapters already list edits in their own section. */
  showChanges?: boolean;
}

export function TranscriptItems({ messages, turnActive = false, turnRunning = false, queuedIds = [], planVersions = EMPTY_PLAN_VERSIONS, onRewindRequest = NO_REWIND, streamingToolEvents, renderTail, renderMessage, showChanges = true }: TranscriptItemsProps) {
  const grouped = useMemo(() => {
    const live = streamingToolEvents ?? [];
    const all = live.length
      ? [...messages, { role: "agent", type: "text", content: "", timestamp: "", metadata: { tool_events: live } } as ChatMsg]
      : messages;
    return groupTranscript(all);
  }, [messages, streamingToolEvents]);
  const running = turnActive || turnRunning;
  // Under a running turn the changes card would sit above the live line and the streaming answer, then jump below it.
  const items = running && grouped[grouped.length - 1]?.kind === "changes" ? grouped.slice(0, -1) : grouped;
  return (
    <>
      {items.map((item) => {
        if (item.kind === "changes" && !showChanges) return null;
        if (item.kind === "activity" || item.kind === "changes") {
          return (
            <div key={item.key} className="min-w-0">
              {item.kind === "activity" ? <ActivityGroup steps={item.steps} /> : <TurnChanges files={item.files} />}
            </div>
          );
        }
        return (
          <Fragment key={item.msg.id ?? `m-${item.index}`}>
            {renderMessage ? renderMessage(item.msg, item.index) : <MessageRow msg={item.msg} planVersion={planVersions.get(item.index)}
              turnActive={turnActive} queued={item.msg.id != null && queuedIds.includes(item.msg.id)}
              onRewindRequest={onRewindRequest} />}
          </Fragment>
        );
      })}
      {renderTail?.()}
    </>
  );
}

