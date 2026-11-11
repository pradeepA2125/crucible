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

/** Credits for the voice components on Windows and Linux, written beside the voice helpers. */
export function thirdPartyNotices(pocketAttribution) {
  return [
    "Crucible local voice: third-party components",
    "",
    "whisper.cpp (speech recognition and Silero VAD), MIT License. See LICENSE.whisper.cpp.",
    "https://github.com/ggml-org/whisper.cpp",
    "",
    "sherpa-onnx and sherpa-onnx-node (spoken replies), Apache-2.0.",
    "https://github.com/k2-fsa/sherpa-onnx",
    "",
    "ONNX Runtime (bundled with sherpa-onnx), MIT License.",
    "https://github.com/microsoft/onnxruntime",
    "",
    "Downloaded on request, not bundled:",
    pocketAttribution,
    "",
  ].join("\n");
}
