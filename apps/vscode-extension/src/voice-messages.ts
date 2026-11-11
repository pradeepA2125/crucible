import type { ChatMessage } from "@crucible/editor-client";

/** A spoken exchange or narration saved for the user to read (backend `is_voice_message`).
 *  A spoken request that started work carries `voice: true` and is an ordinary message. */
export function isVoiceMessage(message: ChatMessage): boolean {
  const kind = message.metadata?.["voice"];
  return kind === "exchange" || kind === "narration";
}
