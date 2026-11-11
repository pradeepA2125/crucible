import type { ChatMsg } from "../../types";
import type { VoiceReceipt } from "./voiceBridge";

export interface VoiceTurn {
  id: string;
  text: string;
  reply?: string;
  final?: boolean;
  receipt?: VoiceReceipt;
  /** The voice decision the saved exchange carries; set on live turns once decided. */
  decisionId?: string;
  /** Typed into the composer during the session (no Spoken tag). */
  typed?: boolean;
}

const MAX_TURNS = 30;
const words = (text: string) => text.toLowerCase().replace(/\s+/gu, " ").trim();

/** The voice conversation as saved in the thread: heard/said exchanges paired by decision,
 *  spoken requests that started work, and narrations attached to the request whose answer
 *  they summarize. Typed messages and written answers are not voice turns. */
export function voiceTurns(messages: ChatMsg[]): VoiceTurn[] {
  const turns: VoiceTurn[] = [];
  const byDecision = new Map<string, VoiceTurn>();
  const byMessage = new Map<string, VoiceTurn>();
  messages.forEach((message, index) => {
    const voice = message.metadata?.voice;
    if (voice === "exchange") {
      const decision = typeof message.metadata.voice_decision === "string" ? message.metadata.voice_decision : undefined;
      const existing = decision ? byDecision.get(decision) : undefined;
      if (message.role === "user") {
        const turn: VoiceTurn = { id: decision ?? message.id ?? `voice-${index}`, text: message.content, final: true,
          ...(decision ? { decisionId: decision } : {}) };
        turns.push(turn);
        if (decision) byDecision.set(decision, turn);
      } else if (existing) {
        existing.reply = message.content;
      }
    } else if (voice === true && message.role === "user" && message.id) {
      const turn: VoiceTurn = { id: message.id, text: message.content, final: true };
      turns.push(turn);
      byMessage.set(message.id, turn);
    } else if (voice === "narration") {
      const answer = messages.findIndex(m => m.id === message.metadata.answer_id);
      const asked = [...messages.slice(0, answer < 0 ? index : answer)].reverse()
        .find(m => m.role === "user" && m.metadata?.voice !== "exchange");
      const turn = asked?.id ? byMessage.get(asked.id) : undefined;
      if (turn) turn.reply = message.content;
      else turns.push({ id: message.id ?? `voice-${index}`, text: asked?.content ?? "", reply: message.content, final: true });
    }
  });
  return turns.slice(-MAX_TURNS);
}

/** The live turns the chat cannot show yet: still in flight (not saved), or a correction
 *  whose receipt is still queued or expired. A turn is saved when a saved turn has its
 *  decision, or the same words for a spoken request that started work. */
export function unsettledVoiceTurns(saved: VoiceTurn[], live: VoiceTurn[]): VoiceTurn[] {
  return live.filter(turn => {
    const status = turn.receipt?.status;
    if (status === "queued" || status === "expired") return true;
    return !saved.some(candidate => (turn.decisionId !== undefined && candidate.decisionId === turn.decisionId)
      || (!candidate.decisionId && words(candidate.text) === words(turn.text)));
  });
}
