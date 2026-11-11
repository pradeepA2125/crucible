# Voice on every desktop platform

Follows voice plans 1 and 2 (`docs/superpowers/plans/2026-11-09-voice-front-man.md`,
`2026-11-11-voice-plan-2.md`). Voice works today only on macOS Apple Silicon. This project
brings it to **Windows x64 and Linux x64**. Intel macOS shares the same path and is a
follow-up (see Non-goals).
The user asked for the open questions to be researched and decided rather than asked; the
decisions below are the designer's, with the evidence for each.

## Why

The desktop app is meant to run on all three systems ("macOS first; Windows/Linux later",
desktop app design, 2026-10-21), and `package.mjs` already builds `win32-x64`, `linux-x64` and
`darwin-x64`. Voice is the one feature hard-wired to one machine: `VoiceHost.status()`
returns "unavailable" unless the platform is `darwin-arm64`, and `package.mjs` bundles the
voice helpers only there. Everything above the native helpers is already portable: microphone
capture, echo cancellation and playback run in Chromium, and the voice agent, turn-taking,
narration, project words and saved voice messages are TypeScript and Python.

## What exists, per piece

| Piece | Today | Portable? |
|---|---|---|
| Microphone, Chromium echo cancellation, playback, echo gate | `voiceAudio.ts`, `echoGate.ts` (`getUserMedia` with `echoCancellation: true`) | Yes; needs a live check per system |
| Speech-to-text and Silero VAD | `crucible-whisper` (whisper.cpp v1.9.5, Metal), `build.sh` macOS-only | whisper.cpp builds with CMake on Windows and Linux; CPU everywhere |
| Text-to-speech, system | `crucible-tts` (Swift, macOS voices) | No |
| Text-to-speech, Pocket | `crucible-pocket-tts` (Swift, FluidAudio CoreML pack, CC-BY-4.0) | No (CoreML is Apple-only) |
| Helper processes | `workers.ts` spawns with `PATH=/usr/bin:/bin`, POSIX permission checks | Needs Windows handling |
| Packaging and CI | `desktop-package.yml` runs on `macos-15` only; `release.yml` already builds the indexer on Ubuntu and Windows | Needs Windows and Linux jobs |

## Decisions (researched 2026-11-11)

### 1. Spoken replies everywhere: Pocket TTS through sherpa-onnx

Off Apple Silicon, spoken replies use **Pocket TTS** (Kyutai's ~100M-parameter CPU model, the
same model family as the Mac's Pocket voice) run by **sherpa-onnx**:

- **sherpa-onnx** is Apache-2.0 and supports Pocket TTS through its C API since 1.12.24–1.12.26.
  Its Node package `sherpa-onnx-node` 2.0.1 (published 2026-10-26) ships prebuilt binaries for
  `win-x64` and `linux-x64` (and macOS and arm64 variants), so nothing has to be compiled per
  system for speech output.
- **Model:** `sherpa-onnx-pocket-tts-int8-2026-01-26.tar.bz2` from sherpa-onnx's `tts-models`
  GitHub release (int8 ONNX: `lm_flow`, `lm_main`, `encoder`, `decoder`, `text_conditioner`,
  `vocab.json`, `token_scores.json`). Downloaded file by file from the sherpa-onnx author's
  Hugging Face mirror (`csukuangfj2/sherpa-onnx-pocket-tts-int8-2026-01-26`), about 199 MB. The weights
  are **CC-BY-4.0** (Kyutai's model card), so redistribution is allowed with attribution. The
  Kyutai Hugging Face repository is gated; the sherpa-onnx release is not.
- **Voice:** sherpa-onnx picks the voice from a short reference recording. Ship one reference
  voice from a **CC0 or CC-BY** source in `kyutai/tts-voices`; the default is `alba`
  (alba-mackenna, CC-BY-4.0), the voice the Mac uses. Voices from Expresso and EARS are
  non-commercial and must never be used.
- **Speed:** Kyutai reports ~200 ms to first audio and ~6× real time on two CPU cores (M4);
  int8 ONNX on x86 is expected to be in the same range. The builder measures it on CI hardware.

Alternatives rejected:

| Option | Why not |
|---|---|
| Piper | The maintained engine (`OHF-Voice/piper1-gpl`) is GPL-3.0 and embeds espeak-ng (GPL); the MIT original is archived; voice licences vary |
| Kokoro-82M | Weights Apache-2.0, but its phonemizer depends on espeak-ng (GPL-3.0), and it is a different voice from the Mac |
| Windows "natural" voices | Restricted to Narrator; no supported API for other apps |
| Windows SAPI / Linux speech-dispatcher voices | Robotic, and Linux has none installed by default |
| Kyutai's official Rust binary | Official and cross-platform, but a CLI/server rather than our streaming protocol; we would build and ship a Rust helper per system. It is the **fallback** if sherpa-onnx's Pocket port falls behind |

Apple Silicon keeps its two engines unchanged: the CoreML Pocket helper and the system voice.

### 2. Speech-to-text: the same whisper.cpp helper, built per system

Build `crucible-whisper` (same pinned whisper.cpp v1.9.5, same protocol, same `--vad` and
`--prompt` arms) for `win32-x64` and `linux-x64`, **CPU only** at first, keeping the current
single static binary (`GGML_NATIVE OFF`, `GGML_BACKEND_DL OFF`). That build assumes **AVX2**
(x86-64-v3, every mainstream x64 CPU since about 2013); the capability check detects a CPU
without it and the panel says voice needs a newer processor, instead of the helper crashing.
Designer's call: one binary and a clear message over ggml's per-CPU variant libraries. Whisper
models and the Silero VAD model download exactly as on the Mac. GPU acceleration on Windows and
Linux (Vulkan, `GGML_VULKAN`, available since whisper.cpp 1.8.3) is a later phase: it needs the
Vulkan SDK in CI and has a known Windows pitfall where the D3D12-on-Vulkan driver is picked
instead of the real GPU.

Keeping whisper.cpp everywhere (rather than switching to sherpa-onnx's ASR) preserves the
project-words prompt, the same models and the same VAD the Mac was tuned on.

### 3. "No voice installed" means speech in, text out

Voice no longer requires a text-to-speech engine to start. Without one, everything works except
speaking: the decision, the chat messages and the panel's live line are the same, replies are
not spoken, and the panel offers the spoken-reply download ("Install spoken replies (about
200 MB)": the pinned files are downloaded one by one, not as the ~94 MB compressed archive). This is the state of a fresh Windows or Linux install until the user installs Pocket.

### 4. Capabilities, not platform names

`VoiceHost.status()` reports what this machine can do, from what is bundled and installed:
speech-to-text (whisper helper present and, on x64, a CPU with AVX2), voice detection (VAD
model), and the available
text-to-speech engines (`system` on macOS when its helper is bundled, `pocket` when its engine
and model are present). The `darwin-arm64` check goes. The renderer's existing status flow shows
each missing piece with its install action.

### 5. Windows and Linux process details

- Spawn helpers with an environment each system needs (Windows: `SystemRoot`, `TEMP`/`TMP`,
  a `PATH` holding the helper directory), never the POSIX `/usr/bin:/bin`.
- Helper names carry `.exe` on Windows; the executable check uses the file's existence there
  (no POSIX execute bit).
- Helper output may end lines with `\r\n` on Windows; the host already trims line ends, and
  the protocol tests cover it.
- Paths with spaces and non-ASCII user names are normal on Windows and must work.

### 6. Packaging and CI

- A new `voice-platforms.yml` workflow (the release-candidate `desktop-package.yml` is manually
  dispatched with version inputs, so preview jobs do not belong there) runs `ubuntu` and
  `windows` jobs that build `crucible-whisper` from
  source in the job (as the job already builds the indexer), package a **preview** (`--preview`)
  `.tar.gz` / `.zip` with the voice helper and that target's `sherpa-onnx` prebuilt, and run the
  headless voice smoke (Verification). They are not release candidates: the candidate pipeline
  (`desktop-runtime-release.py`, `desktop-release-bundle.mjs`) stays Apple Silicon only until
  signing and updates come (Non-goals). Designer's call.
- `release.yml` and the runtime manifest do not change, so nothing voice-related reaches the
  VS Code extension's runtime list.
- `package.mjs` bundles the voice pieces for `win32-x64` and `linux-x64` as well as `darwin-arm64`.
- The Apple Silicon CI job is unchanged (it does not build voice today; that stays as is).

## Goals

1. Voice starts and works on Windows x64 and Linux x64 packages, with speech-to-text,
   voice detection, the voice agent and saved voice messages working as on Apple Silicon.
2. Spoken replies on those systems with Pocket TTS once its model is installed; speech in, text
   out until then.
3. Every native piece is built and smoke-tested in CI on its own system.
4. Apple Silicon behaves exactly as today.

## Non-goals (follow-ups)

- GPU speech-to-text on Windows and Linux (Vulkan).
- Intel macOS. It takes the same sherpa-onnx and CPU whisper path, plus the system voice built for
  x86_64, but needs an Intel CI runner for its smoke test. Designer's call to keep this project
  to Windows and Linux.
- x64 CPUs without AVX2 (named in the panel, not supported).
- Code signing on Windows (Azure Artifact Signing is available to individuals only in the US and
  Canada, and signing alone does not clear SmartScreen warnings) and Linux distribution formats
  (AppImage, deb).
- Auto-updates for Windows and Linux (`update-manifest.ts` is macOS-only).
- ARM64 Windows and Linux (the packager does not target them yet).
- More Pocket voices or a voice picker; other languages.
- Voice in the VS Code extension.

## Anti-goals

- No GPL or non-commercial component is bundled or downloaded (this rules out Piper,
  espeak-ng data, and the Expresso/EARS voices).
- The Apple Silicon voice path does not change or slow down.
- Voice is never silently broken: a missing piece is named in the panel with the action that
  fixes it.
- No new required setting or install step for existing Mac users.

## Constraints

- Test-first, in the style of `voice-host.test.ts`, `voice-assets.test.ts` and the webview voice
  tests.
- Every download is pinned by URL, byte size and SHA-256, like the Whisper and VAD models.
- Attribution for Pocket TTS (CC-BY-4.0) and the reference voice: a `THIRD_PARTY_NOTICES` file
  in each package (beside the existing `LICENSE.*` helper files), and an `ATTRIBUTION` file
  written into the downloaded model folder. No new screen. Designer's call.
- Live voice testing on Windows and Linux needs a real machine or VM; this Mac cannot run them.
  CI proves the pieces start and produce output; a live session proves the experience.

## Verification

- Unit: capability reporting per bundled/installed combination; speech-in-text-out when no TTS
  is available; Windows spawn environment and `.exe` naming; the TTS worker's protocol (ready,
  audio chunks, cancel, done, error) with a fake engine; Pocket asset install and verification.
- CI smoke per system: `crucible-whisper` transcribes a fixture WAV and the VAD arm returns
  probabilities; the Pocket worker synthesizes one sentence and returns non-empty PCM.
- The project is complete at the CI smoke tests. A live session on real Windows and Linux machines
  (a spoken question, an interruption over a reply, a reply spoken by Pocket, recorded in an
  evidence doc like the plan 2 one) is a follow-up for when a machine or VM is available; this Mac
  cannot run them. Designer's call.

## Left to the builder

- Whether the sherpa-onnx engine runs in an Electron `utilityProcess` speaking the existing TTS
  protocol, or in a small native helper linked against sherpa-onnx's C library. The protocol and
  behavior are fixed; the hosting is not.
- How sherpa-onnx's generation callback maps onto streamed `audio` events, and thread count.
- The default recognizer (base or small) on CPU-only systems, chosen from measured latency.
- Where the reference voice file is pinned (downloaded with the model or bundled), within the
  licence rule above.
- How the Windows spawn environment is assembled.

## Sources

- Kyutai Pocket TTS: https://github.com/kyutai-labs/pocket-tts and model card
  https://huggingface.co/kyutai/pocket-tts (CC-BY-4.0, gated download); voice licences
  https://huggingface.co/kyutai/tts-voices
- sherpa-onnx (Apache-2.0): https://github.com/k2-fsa/sherpa-onnx; Pocket C API example
  `c-api-examples/pocket-tts-en-c-api.c`; `sherpa-onnx-node` 2.0.1 on npm (prebuilt targets
  checked with `npm view`)
- Piper licensing: https://github.com/OHF-Voice/piper1-gpl (GPL-3.0)
- Kokoro-82M: https://huggingface.co/hexgrad/Kokoro-82M (Apache-2.0, espeak-ng GPL dependency)
- Windows natural voices: https://github.com/gexgd0419/NaturalVoiceSAPIAdapter (Narrator-only)
- whisper.cpp Vulkan: https://github.com/ggml-org/whisper.cpp; https://phoronix.com/news/whisper-cpp-1.8.3-12x-perf
- Windows signing: https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options
