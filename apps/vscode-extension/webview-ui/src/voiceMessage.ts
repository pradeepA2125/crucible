import type { ChatMsg } from "./types";

/** A spoken exchange or narration saved for the user to read (backend `is_voice_message`).
 *  It is not a request: it never starts or ends a turn. A spoken request that started work
 *  carries `voice: true` and is an ordinary message. */
export function isVoiceMessage(msg: ChatMsg): boolean {
  const kind = msg.metadata?.voice;
  return kind === "exchange" || kind === "narration";
}
