import { Fragment } from "react";
import { Icon } from "../Icon";
import { BtnGhost } from "../shared/buttons";
import { UserMessage } from "../messages/UserMessage";
import { SpokenLine } from "../messages/SpokenLine";
import { heardLabel } from "../../desktop/voice/voiceStatus";
import type { VoiceReceipt } from "../../desktop/voice/voiceBridge";
import type { VoiceComposerModel } from "./VoiceContext";

const RECEIPT_NOTES: Partial<Record<VoiceReceipt["status"], { icon: "retry" | "x"; text: string }>> = {
  queued: { icon: "retry", text: "Correction queued" },
  expired: { icon: "x", text: "Correction expired" },
};

/**
 * The part of a voice session the saved transcript cannot show yet, drawn at the bottom of the
 * conversation: turns in flight (the spoken bubble and the reply being spoken), the words heard
 * so far, a correction's receipt, and the session's last error.
 */
export function VoiceLiveLines({ voice }: { voice: VoiceComposerModel }) {
  const { turns, state, error, pushToTalk } = voice;
  if (turns.length === 0 && !state.partial && !error) return null;
  return <>
    {turns.map(turn => {
      const note = turn.receipt ? RECEIPT_NOTES[turn.receipt.status] : undefined;
      return <Fragment key={turn.id}>
        <UserMessage content={turn.text} spoken={!turn.typed} />
        {turn.reply && <div aria-live={turn.final ? "polite" : "off"} className="min-w-0">
          <SpokenLine label={turn.final ? "Spoken reply" : "Speaking"} content={turn.reply} />
        </div>}
        {note && turn.receipt && <div role="status" className="flex items-center gap-2 text-caption text-text-2">
          <Icon name={note.icon} size={11} className={note.icon === "x" ? "text-red" : "text-accent"} />
          <span>{note.text}{turn.receipt.text && turn.receipt.text !== turn.reply ? ` · ${turn.receipt.text}` : ""}</span>
        </div>}
      </Fragment>;
    })}
    {state.partial && <div data-testid="voice-hearing" className="flex min-w-0 flex-col items-end gap-1.5"
      style={{ opacity: 0.85, animation: state.deciding ? "pulse 1.5s ease-in-out infinite" : undefined }}>
      <UserMessage content={state.partial} spoken spokenLabel={heardLabel(state)} />
      {!pushToTalk && state.capturing && <BtnGhost onClick={voice.sendNow}>Send now</BtnGhost>}
    </div>}
    {error && <div role="alert" className="flex items-center gap-2 text-caption" style={{ color: "var(--danger)" }}>
      <Icon name="warn" size={11} /><span className="[overflow-wrap:anywhere]">{error}</span>
    </div>}
  </>;
}
