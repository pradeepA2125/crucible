// What each desktop package carries for local voice, relative to dist/. Shared by package.mjs
// and its tests.

/** Apple Silicon ships the whole voice-native folder (built by build.sh); Windows and Linux ship
 *  the whisper helper (build-portable.mjs), the Pocket worker and sherpa-onnx's prebuilt addon. */
export function voicePayload(platform) {
  if (platform === "darwin-arm64") return ["voice-native"];
  if (platform !== "win32-x64" && platform !== "linux-x64") return [];
  const windows = platform === "win32-x64";
  return [
    `voice-native/crucible-whisper${windows ? ".exe" : ""}`,
    "voice-native/LICENSE.whisper.cpp",
    "voice-pocket-worker.cjs",
    "node_modules/sherpa-onnx-node",
    `node_modules/sherpa-onnx-${windows ? "win" : "linux"}-x64`,
  ];
}

/** Full licence texts (voice-native/licenses/, pinned to sherpa-onnx v2.0.1 and the ONNX Runtime
 *  1.28.2 it bundles) copied beside the voice helpers on Windows and Linux. */
export function voiceLicenses(platform) {
  if (platform !== "win32-x64" && platform !== "linux-x64") return [];
  return ["LICENSE.sherpa-onnx", "LICENSE.onnxruntime", "ThirdPartyNotices.onnxruntime.txt"];
}

/** Credits for the voice components on Windows and Linux, written beside the voice helpers. */
export function thirdPartyNotices(pocketAttribution) {
  return [
    "Crucible local voice: third-party components",
    "",
    "whisper.cpp (speech recognition and Silero VAD), MIT License. See LICENSE.whisper.cpp.",
    "https://github.com/ggml-org/whisper.cpp",
    "",
    "sherpa-onnx and sherpa-onnx-node 2.0.1 (spoken replies), Apache-2.0. See LICENSE.sherpa-onnx.",
    "https://github.com/k2-fsa/sherpa-onnx",
    "",
    "ONNX Runtime 1.28.2 (bundled with sherpa-onnx), MIT License. See LICENSE.onnxruntime and",
    "ThirdPartyNotices.onnxruntime.txt.",
    "https://github.com/microsoft/onnxruntime",
    "",
    "Downloaded on request, not bundled:",
    pocketAttribution,
    "",
  ].join("\n");
}

/** The Apple Silicon voice helpers a release candidate must carry (voice-native/build.sh builds
 *  whisper and the system voice, build-pocket.sh builds Pocket TTS). `has` answers whether a
 *  helper file exists. Without them packaging used to leave voice out silently. */
export function missingReleaseVoice(platform, has) {
  if (platform !== "darwin-arm64") return [];
  return ["crucible-whisper", "crucible-tts", "crucible-pocket-tts"].filter(name => !has(name));
}
