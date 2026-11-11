import { afterEach, describe, expect, it } from "vitest";
import {
  defaultRecognizer, readMicrophoneMode, readRecognizer, readSynthesizer, resolveSynthesizer, subscribeVoicePreferences,
  synthesizerChoices, wantsPocket, writeMicrophoneMode, writeRecognizer, writeSynthesizer,
} from "./voicePreferences";
import type { VoiceStatus } from "./voiceBridge";

const status = (overrides: Partial<VoiceStatus> = {}): VoiceStatus => ({
  available: true, installed: ["whisper-small"], tts: ["system"], pocketAvailable: true,
  pocketDownloadBytes: 199_269_026, pocketEngine: "onnx", ...overrides,
});

describe("voice preferences", () => {
  afterEach(() => localStorage.clear());

  it("reads nothing until a valid choice is saved", () => {
    expect(readRecognizer()).toBeNull();
    expect(readSynthesizer()).toBeNull();
    expect(readMicrophoneMode()).toBe("hands-free");
    localStorage.setItem("crucible.voice.recognizer", "whisper-huge");
    localStorage.setItem("crucible.voice.synthesizer", "robot");
    localStorage.setItem("crucible.voice.microphone", "shout");
    expect(readRecognizer()).toBeNull();
    expect(readSynthesizer()).toBeNull();
    expect(readMicrophoneMode()).toBe("hands-free");
  });

  it("round-trips the three choices", () => {
    writeRecognizer("whisper-base");
    writeSynthesizer("none");
    writeMicrophoneMode("push-to-talk");
    expect(readRecognizer()).toBe("whisper-base");
    expect(readSynthesizer()).toBe("none");
    expect(readMicrophoneMode()).toBe("push-to-talk");
  });

  it("defaults the recognizer to Whisper Small unless only Base is installed", () => {
    expect(defaultRecognizer(["whisper-small"])).toBe("whisper-small");
    expect(defaultRecognizer([])).toBe("whisper-small");
    expect(defaultRecognizer(["whisper-base"])).toBe("whisper-base");
    expect(defaultRecognizer(["whisper-base", "whisper-small"])).toBe("whisper-small");
  });

  it("spoken replies default to Pocket, with the system voice until Pocket is installed", () => {
    expect(resolveSynthesizer(null, status())).toBe("system");
    expect(wantsPocket(null, status())).toBe(true);
    expect(resolveSynthesizer(null, status({ tts: ["system", "pocket"] }))).toBe("pocket");
    expect(wantsPocket(null, status({ tts: ["system", "pocket"] }))).toBe(false);
    expect(resolveSynthesizer(null, status({ tts: [] }))).toBe("none");
    expect(resolveSynthesizer(null, status({ tts: [], pocketAvailable: false }))).toBe("none");
    expect(wantsPocket(null, status({ pocketAvailable: false }))).toBe(false);
  });

  it("respects an explicit choice and falls back when it is not on this machine", () => {
    expect(resolveSynthesizer("none", status({ tts: ["system", "pocket"] }))).toBe("none");
    expect(wantsPocket("none", status())).toBe(false);
    expect(resolveSynthesizer("system", status())).toBe("system");
    expect(resolveSynthesizer("system", status({ tts: ["pocket"] }))).toBe("pocket");
    expect(resolveSynthesizer("pocket", status({ tts: ["system"] }))).toBe("system");
    expect(wantsPocket("pocket", status({ tts: ["system"] }))).toBe(true);
  });

  it("offers only the spoken-reply engines this machine supports", () => {
    expect(synthesizerChoices(status()).map(choice => choice.value)).toEqual(["pocket", "system", "none"]);
    expect(synthesizerChoices(status()).find(choice => choice.value === "pocket")?.installed).toBe(false);
    expect(synthesizerChoices(status({ tts: [], pocketAvailable: false })).map(choice => choice.value)).toEqual(["none"]);
    expect(synthesizerChoices(status({ tts: ["pocket"] })).map(choice => choice.value)).toEqual(["pocket", "none"]);
  });

  it("tells a listener when another window changes a voice preference", () => {
    const seen: string[] = [];
    const stop = subscribeVoicePreferences(key => seen.push(key));
    window.dispatchEvent(new StorageEvent("storage", { key: "crucible.voice.microphone", newValue: "push-to-talk" }));
    window.dispatchEvent(new StorageEvent("storage", { key: "crucible.voiceModel.chatgpt", newValue: "gpt-6" }));
    window.dispatchEvent(new StorageEvent("storage", { key: "unrelated", newValue: "x" }));
    stop();
    window.dispatchEvent(new StorageEvent("storage", { key: "crucible.voice.microphone", newValue: "hands-free" }));
    expect(seen).toEqual(["crucible.voice.microphone", "crucible.voiceModel.chatgpt"]);
  });
});
