import { describe, expect, it } from "vitest";
import { INITIAL_VOICE_STATE, type VoiceSessionState } from "./voiceSession";
import { heardLabel, voicePhase } from "./voiceStatus";

const state = (patch: Partial<VoiceSessionState>): VoiceSessionState =>
  ({ ...INITIAL_VOICE_STATE, microphone: "on", capturing: true, partial: "use sqlite", ...patch });

describe("voice status labels", () => {
  it.each([
    [{}, "Hearing you", "Hearing"],
    [{ checking: true }, "Transcribing…", "Transcribing"],
    [{ deciding: true }, "Thinking about that…", "Thinking"],
    [{ deciding: true, slowDecision: true }, "Still working that out…", "Still thinking"],
    [{ waiting: true }, "Still listening…", "Heard so far"],
  ] as const)("%j shows %j and labels the words %j", (patch, phase, label) => {
    expect(voicePhase(state(patch), false)).toBe(phase);
    expect(heardLabel(state(patch))).toBe(label);
  });

  it("keeps the idle phases", () => {
    expect(voicePhase({ ...INITIAL_VOICE_STATE }, false)).toBe("Microphone off");
    expect(voicePhase({ ...INITIAL_VOICE_STATE, microphone: "on" }, true)).toBe("Push to talk ready");
  });
});
