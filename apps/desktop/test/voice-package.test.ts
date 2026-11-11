import { describe, expect, it } from "vitest";
// @ts-expect-error -- plain ESM module shared with package.mjs
import { thirdPartyNotices, voicePayload } from "../voice-payload.mjs";

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
});
