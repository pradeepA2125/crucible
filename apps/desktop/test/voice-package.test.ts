import { describe, expect, it } from "vitest";
// @ts-expect-error -- plain ESM module shared with package.mjs
import { missingReleaseVoice, thirdPartyNotices, voiceLicenses, voicePayload } from "../voice-payload.mjs";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

describe("voice in each desktop package", () => {
  it("Windows bundles the whisper helper, the Pocket worker and sherpa-onnx for win-x64", () => {
    expect(voicePayload("win32-x64")).toEqual([
      "voice-native/crucible-whisper.exe", "voice-native/LICENSE.whisper.cpp", "voice-pocket-worker.cjs",
      "node_modules/sherpa-onnx-node", "node_modules/sherpa-onnx-win-x64",
    ]);
  });

  it("Linux bundles the same for linux-x64", () => {
    expect(voicePayload("linux-x64")).toEqual([
      "voice-native/crucible-whisper", "voice-native/LICENSE.whisper.cpp", "voice-pocket-worker.cjs",
      "node_modules/sherpa-onnx-node", "node_modules/sherpa-onnx-linux-x64",
    ]);
  });

  it("Apple Silicon keeps today's voice-native folder; other targets get no voice", () => {
    expect(voicePayload("darwin-arm64")).toEqual(["voice-native"]);
    expect(voicePayload("darwin-x64")).toEqual([]);
  });

  it("the notices credit every bundled or downloaded voice component", () => {
    const text = thirdPartyNotices("Pocket TTS by Kyutai, CC BY 4.0.");
    for (const credit of ["whisper.cpp", "MIT", "sherpa-onnx", "Apache-2.0", "ONNX Runtime", "Pocket TTS by Kyutai, CC BY 4.0."]) {
      expect(text).toContain(credit);
    }
  });

  it("Windows and Linux packages carry the full licence texts, not links", () => {
    const files = voiceLicenses("linux-x64");
    expect(files).toEqual(["LICENSE.sherpa-onnx", "LICENSE.onnxruntime", "ThirdPartyNotices.onnxruntime.txt"]);
    expect(voiceLicenses("win32-x64")).toEqual(files);
    expect(voiceLicenses("darwin-arm64")).toEqual([]);
    const root = join(import.meta.dirname, "../voice-native/licenses");
    expect(readFileSync(join(root, "LICENSE.sherpa-onnx"), "utf8")).toContain("Apache License");
    expect(readFileSync(join(root, "LICENSE.onnxruntime"), "utf8")).toContain("Microsoft Corporation");
    expect(existsSync(join(root, "ThirdPartyNotices.onnxruntime.txt"))).toBe(true);
    const notices = thirdPartyNotices("Pocket TTS by Kyutai, CC BY 4.0.");
    for (const file of files) expect(notices).toContain(file);
  });

  it("an Apple Silicon release candidate names every voice helper it is missing", () => {
    const has = (...names: string[]) => (name: string) => names.includes(name);
    expect(missingReleaseVoice("darwin-arm64", has())).toEqual(["crucible-whisper", "crucible-tts", "crucible-pocket-tts"]);
    expect(missingReleaseVoice("darwin-arm64", has("crucible-whisper", "crucible-tts"))).toEqual(["crucible-pocket-tts"]);
    expect(missingReleaseVoice("darwin-arm64", has("crucible-whisper", "crucible-tts", "crucible-pocket-tts"))).toEqual([]);
  });

  it("other targets have no Mac voice helpers to require", () => {
    expect(missingReleaseVoice("win32-x64", () => false)).toEqual([]);
    expect(missingReleaseVoice("linux-x64", () => false)).toEqual([]);
  });
});
