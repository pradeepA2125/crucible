import type { VoiceSessionState } from "./voiceSession";

/** The panel's one-line status: what the voice session is doing right now. */
export function voicePhase(state: VoiceSessionState, pushToTalk: boolean): string {
  if (state.waiting) return "Still listening…";
  if (state.deciding) return state.slowDecision ? "Still working that out…" : "Thinking about that…";
  if (state.checking) return "Transcribing…";
  if (state.capturing) return "Hearing you";
  if (state.speaking) return "Speaking";
  if (state.microphone === "starting") return "Opening microphone";
  if (state.pending) return "Processing message";
  if (state.microphone === "off") return "Microphone off";
  return pushToTalk ? "Push to talk ready" : "Listening";
}

/** The label in front of the words heard so far, so they never read "Hearing" while processed. */
export function heardLabel(state: VoiceSessionState): string {
  if (state.deciding) return state.slowDecision ? "Still thinking" : "Thinking";
  if (state.checking) return "Transcribing";
  if (state.waiting) return "Heard so far";
  return "Hearing";
}
