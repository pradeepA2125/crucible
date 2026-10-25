import { Fragment, useContext, useMemo, type ReactNode } from "react";
import type { ChatMsg, ToolEventView } from "../../types";
import { groupTranscript } from "../../transcript/groupTranscript";
import { groupWork, type WorkItem } from "../../transcript/groupWork";
import { MessageRow } from "../MessageRow";
import { ActivityGroup } from "../activity/ActivityGroup";
import { ChangesRoute } from "../activity/changesRoute";
import { TurnChanges } from "../activity/TurnChanges";
import { WorkSection } from "../activity/WorkSection";

const EMPTY_PLAN_VERSIONS = new Map<number, number | undefined>();
const NO_REWIND = () => {};

/**
 * The transcript as grouped items. Live tool steps (the streaming bubble's finished tool events)
 * join the grouping as a trailing tools-only message, so work shown live sits in the very
 * group it is sealed into (same index, same key) and nothing jumps when the bubble seals.
 * Progress and tool batches nest inside a turn's work section. `renderWorkTail`
 * draws its current status; `renderTail` keeps streamed answer prose outside it.
 */
export interface TranscriptItemsProps {
  messages: ChatMsg[]; turnActive?: boolean; queuedIds?: string[];
  readOnly?: boolean;
  /** The turn is still running (turnActive or input disabled): its changes card waits for the end. */
  turnRunning?: boolean;
  planVersions?: Map<number, number | undefined>; onRewindRequest?: (id: string) => void;
  streamingToolEvents?: ToolEventView[];
  renderTail?: () => ReactNode;
  /** Current tool/thinking status belongs inside the active work section. */
  renderWorkTail?: () => ReactNode;
  renderMessage?: (msg: ChatMsg, index: number) => ReactNode;
  /** Team chapters already list edits in their own section. */
  showChanges?: boolean;
  /** Only the host main transcript may route its exact applied records into Changes. */
  recordEditSources?: boolean;
}

export function TranscriptItems({ messages, readOnly = false, turnActive = false, turnRunning = false, queuedIds = [], planVersions = EMPTY_PLAN_VERSIONS, onRewindRequest = NO_REWIND, streamingToolEvents, renderTail, renderWorkTail, renderMessage, showChanges = true, recordEditSources = false }: TranscriptItemsProps) {
  const route = useContext(ChangesRoute), sources = recordEditSources && !!route;
  const grouped = useMemo(() => {
    const live = streamingToolEvents ?? [];
    const all = live.length
      ? [...messages, { role: "agent", type: "text", content: "", timestamp: "", metadata: { tool_events: live } } as ChatMsg]
      : messages;
    return groupTranscript(all, sources);
  }, [messages, streamingToolEvents, sources]);
  const running = turnActive || turnRunning;
  // Under a running turn the changes card would sit above the live line and the streaming answer, then jump below it.
  const items = running && grouped[grouped.length - 1]?.kind === "changes" ? grouped.slice(0, -1) : grouped;
  const sections = groupWork(items, running, !!renderWorkTail);
  const renderItem = (item: WorkItem) => item.kind === "activity" ? (
    <div key={item.key} className="min-w-0"><ActivityGroup steps={item.steps} /></div>
  ) : (
    <Fragment key={item.msg.id ?? `m-${item.index}`}>
      {renderMessage ? renderMessage(item.msg, item.index) : <MessageRow msg={item.msg} readOnly={readOnly} planVersion={planVersions.get(item.index)}
        turnActive={turnActive} queued={item.msg.id != null && queuedIds.includes(item.msg.id)}
        onRewindRequest={readOnly ? undefined : onRewindRequest} />}
    </Fragment>
  );
  return (
    <>
      {sections.map((item) => {
        if (item.kind === "changes" && !showChanges) return null;
        if (item.kind === "work") return (
          <WorkSection key={item.key} running={item.running}>
            {item.items.map(renderItem)}
            {item.running && renderWorkTail?.()}
          </WorkSection>
        );
        if (item.kind === "changes") {
          return (
            <div key={item.key} className="min-w-0">
              <TurnChanges files={item.files} />
            </div>
          );
        }
        return renderItem(item);
      })}
      {!running && renderWorkTail?.()}
      {renderTail?.()}
    </>
  );
}

