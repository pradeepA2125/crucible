# Voice on Windows and Linux Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Desktop voice works on Windows x64 and Linux x64: whisper.cpp speech-to-text and VAD on the CPU, Pocket TTS spoken replies through sherpa-onnx, and speech-in/text-out until Pocket is installed.

**Architecture:** The existing `crucible-whisper` helper is built per system from the same pinned whisper.cpp, with an explicit AVX2 baseline and a `--check-cpu` arm. Off Apple Silicon, spoken replies come from a TypeScript worker (`pocket-worker.ts`) running sherpa-onnx in an Electron `utilityProcess` and speaking the existing TTS JSON protocol. `VoiceHost.status()` reports capabilities instead of checking for `darwin-arm64`, and a session may start with no TTS (`tts: "none"`).

**Tech Stack:** Electron (utilityProcess), TypeScript, Vitest, `sherpa-onnx-node` 2.0.1, whisper.cpp v1.9.5 (CMake), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-11-voice-multiplatform-design.md`

## Global Constraints

- Targets: `win32-x64` and `linux-x64`. `darwin-arm64` voice behaves exactly as today (CoreML Pocket, system voice, Metal whisper via `build.sh`). Intel macOS, ARM64 Windows/Linux, signing, Linux installers and Windows/Linux updates are out of scope.
- whisper.cpp stays pinned to v1.9.5 (`CMakeLists.txt` URL and SHA-256 unchanged).
- x64 whisper baseline: `GGML_NATIVE OFF`, `GGML_BACKEND_DL OFF`, `GGML_AVX=ON`, `GGML_AVX2=ON`, `GGML_FMA=ON`, `GGML_F16C=ON`. CPU without AVX2+FMA+F16C → panel reason exactly: `Local voice needs a processor with AVX2 (most made since 2013).`
- `sherpa-onnx-node` pinned exactly to `2.0.1` (Apache-2.0).
- Pocket ONNX pack, files from `https://huggingface.co/csukuangfj2/sherpa-onnx-pocket-tts-int8-2026-01-26/resolve/e715955cf50d18d919d37231513c0e914b83661a/` (CC-BY-4.0):

  | file | bytes | sha256 |
  |---|---|---|
  | `decoder.int8.onnx` | 22693618 | `12b0857402d31aead94df19d6783b4350d1f740e811f3a3202c70ad89ae11eea` |
  | `encoder.onnx` | 72713165 | `e8f2f6d301ffb96e398b138a7dc6d3038622d236044636b73d920bab85890260` |
  | `lm_flow.int8.onnx` | 9962530 | `8d627d235c44a597da908e1085ebe241cbbe358964c502c5a5063d18851a5529` |
  | `lm_main.int8.onnx` | 76341079 | `bfc0c7e7e3d72864fa3bb2ee499f62f21ddc1474b885f5f3ca570f8be73e787e` |
  | `text_conditioner.onnx` | 16388343 | `0b84e837d7bfaf2c896627b03e3f080320309f37f4fc7df7698c644f7ba5e6b1` |
  | `token_scores.json` | 123616 | `5be2f278caf9b9800741f0fd82bff677f4943ec764c356f907213434b622d958` |
  | `vocab.json` | 69478 | `6fb646346cf931016f70c4921aab0900ce7a304b893cb02135c74e294abfea01` |
  | `LICENSE` | 18655 | `fe7b4ce83b8381cc5b216bbb4af73c570688d1b819c73bbaed8ca401f4677cd6` |

- Reference voice (Pocket's own default "alba", CC-BY-4.0): `https://huggingface.co/kyutai/tts-voices/resolve/323332d33f997de8394f24a193e1a76df720e01a/alba-mackenna/casual.wav`, 958542 bytes, sha256 `46264e83cb99115c3d210260e029117566d9c64f20266d10daa78107759ede3e`, stored as `voice.wav`.
- Attribution text (written to `ATTRIBUTION` in the pack folder and included in `THIRD_PARTY_NOTICES`): `Pocket TTS by Kyutai (https://huggingface.co/kyutai/pocket-tts), CC BY 4.0; ONNX export by KevinAHM and sherpa-onnx, CC BY 4.0. Voice "alba" from kyutai/tts-voices (alba-mackenna), CC BY 4.0.`
- No GPL or non-commercial component is bundled or downloaded. Every download is pinned by URL, byte size and SHA-256.
- Panel install copy off Apple Silicon: `Install spoken replies (about 200 MB)`. Apple Silicon keeps `Install Pocket TTS`.
- Test-first; desktop tests `npm run -w @crucible/desktop test`, webview tests `npx vitest run --maxWorkers=2` in `apps/vscode-extension/webview-ui`.

## Review Focus

1. A Windows user folder with spaces and non-ASCII letters (`C:\Users\José Smith\AppData\Roaming\Crucible`): helpers start and models load — Task 2 test `helper paths and environment survive spaces and accents`.
2. An interrupted or corrupted Pocket download is never reported installed, and the next install starts clean — Task 3 tests `a pack without its completion marker is not installed` and `a damaged pack file fails verification`.
3. Text over 4,096 bytes, or characters Pocket cannot say (emoji, CJK), reach the Pocket worker: it answers `error` for that id and keeps serving — Task 4 test `oversized or unspeakable text errors without stopping the worker`.
4. A CPU without AVX2: no crash, the panel names the reason and start is disabled — Task 5 test `a CPU without AVX2 makes voice unavailable with the reason` and Task 6 test `shows the processor reason and disables start`.
5. A session started text-only while Pocket installs: it stays text-only and never spawns a synthesizer; the next session can speak — Task 5 test `a text-only session never starts a synthesizer`.

---

### Task 1: Portable whisper build with a CPU check

**Files:**
- Modify: `apps/desktop/voice-native/whisper.cpp` (add `--check-cpu` arm before model load)
- Modify: `apps/desktop/voice-native/CMakeLists.txt` (x64 flags; Windows MSVC static runtime)
- Create: `apps/desktop/voice-native/build-portable.mjs`
- Modify: `apps/desktop/package.json` (`"build:voice:portable": "node voice-native/build-portable.mjs dist/voice-native"`), `apps/desktop/voice-native/README.md` (protocol line)

**Interfaces:**
- Produces: `crucible-whisper --check-cpu` prints `cpu ok` and exits 0, or prints `cpu unsupported` and exits 3. On arm64 it always prints `cpu ok`. On x64 it requires AVX2, FMA and F16C (CPUID leaf 7 EBX bit 5, leaf 1 ECX bits 12 and 29) plus OS AVX state (`xgetbv` XCR0 bits 1–2); use `__builtin_cpu_supports` on GCC/Clang and `__cpuid`/`__cpuidex`/`_xgetbv` on MSVC. The check runs before any ggml call.
- Produces: `build-portable.mjs <outDir>` writes `crucible-whisper` (`crucible-whisper.exe` on Windows) and `LICENSE.whisper.cpp` to `outDir`, building with CMake Release and the Global Constraints flags (`-DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON` only when `process.arch === "x64"`), honoring `CRUCIBLE_WHISPER_SOURCE_DIR` like `build.sh`. On Windows set `CMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded` so the helper needs no VC++ redistributable.

- [ ] **Step 1:** Build with the existing Mac script: `cd apps/desktop && bash voice-native/build.sh /tmp/vn` and run `/tmp/vn/crucible-whisper --check-cpu`. Expected: usage error, exit 2 (arm not implemented).
- [ ] **Step 2:** Implement the `--check-cpu` arm and the CMake/script changes.
- [ ] **Step 3:** Rebuild with `build.sh`, run `--check-cpu`. Expected: `cpu ok`, exit 0. Then `node voice-native/build-portable.mjs /tmp/vp && /tmp/vp/crucible-whisper --check-cpu` on this Mac. Expected: `cpu ok` (the x64 flags apply only on x64; the Windows/Linux runs happen in Task 7's CI).
- [ ] **Step 4:** Commit `feat(voice): portable whisper build with an AVX2 check`.

### Task 2: Helper processes that start on Windows

**Files:**
- Modify: `apps/desktop/src/voice/workers.ts` (`LineWorker` spawns with `helperEnvironment(process.platform, process.env, dirname(path))`; export `helperEnvironment`, `helperPath`)
- Modify: `apps/desktop/src/voice/host.ts` (`helperExists`, every helper path through `helperPath`)
- Test: `apps/desktop/test/voice-workers.test.ts` (create), `apps/desktop/test/voice-host.test.ts`

**Interfaces:**
- Produces: `helperPath(helpers: string, name: string, platform: string): string` (adds `.exe` on `win32`).
- Produces: `helperEnvironment(platform: string, env: NodeJS.ProcessEnv, helpers: string): Record<string, string>` — POSIX: `{ PATH: "/usr/bin:/bin", LANG: "en_US.UTF-8", TMPDIR? }` as today; `win32`: `SystemRoot`, `windir`, `TEMP`, `TMP` copied from `env`, `PATH` = `${helpers};${SystemRoot}\System32`.
- `VoiceHost.helperExists` on `win32` checks only that the path is a file.

- [ ] **Step 1: Write the failing tests** (`voice-workers.test.ts`):
  - `win32 helpers carry .exe and get a Windows environment`: `helperPath("C:\\h", "crucible-whisper", "win32")` ends `crucible-whisper.exe`; `helperEnvironment("win32", { SystemRoot: "C:\\Windows", TEMP: "C:\\T", PATH: "x" }, "C:\\h")` equals `{ SystemRoot: "C:\\Windows", windir: "C:\\Windows", TEMP: "C:\\T", TMP: "C:\\T", PATH: "C:\\h;C:\\Windows\\System32" }`.
  - `posix helpers keep today's environment`: linux result has `PATH: "/usr/bin:/bin"` and no `SystemRoot`.
  - `helper paths and environment survive spaces and accents`: helpers dir `C:\\Users\\José Smith\\AppData\\Roaming\\Crucible\\voice-native` appears verbatim at the start of `PATH` and in `helperPath`.
  - `windows line endings parse as lines`: a `LineWorker` over a fixture helper script that prints `ready\r\n` delivers `ready` to `onLine`.
  - In `voice-host.test.ts`: `status on win32 finds .exe helpers without an execute bit` (fixture writes `crucible-whisper.exe` with mode 0o600, `platform: "win32"`).
- [ ] **Step 2:** Run `npm run -w @crucible/desktop test -- test/voice-workers.test.ts test/voice-host.test.ts`. Expected: FAIL (functions not exported; win32 status unavailable).
- [ ] **Step 3:** Implement. `LineWorker` keeps `shell: false` and passes `env` from `helperEnvironment`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit `feat(voice): helper processes start on Windows`.

### Task 3: The Pocket ONNX pack and per-platform Pocket assets

**Files:**
- Create: `apps/desktop/voice-native/pocket-onnx-assets.json` (Global Constraints table + voice; `{ "id": "onnx-e715955c", "license": "CC-BY-4.0", "files": [{ "path", "url", "bytes", "sha256" }] }`)
- Modify: `apps/desktop/src/voice/assets.ts`
- Test: `apps/desktop/test/voice-assets.test.ts`

**Interfaces:**
- Produces: `type PocketPack = { id: string; engine: "coreml" | "onnx"; root: string; files: { path: string; url: string; bytes: number; sha256: string }[]; attribution?: string }`.
- Produces: `pocketPackFor(platform: string, arch: string): PocketPack | undefined` — `darwin-arm64` → the existing `pocket-assets.json` normalized (`id` = its `revision`, `root` = its `relativeRoot`, `url` = `${source}/resolve/${revision}/${path}`); `win32-x64`/`linux-x64` → the ONNX pack (`root` = `""`, `attribution` = the Global Constraints text); otherwise `undefined`.
- Produces: `new VoiceAssets(directory, fetchModel = fetch, pack = pocketPackFor(process.platform, process.arch))`; `pocketInstalled()`, `verifiedPocketPath()` and `install("pocket")` use `pack`; the completion marker holds `pack.id`; an ONNX install writes `ATTRIBUTION`. `pocketDownloadBytes(): number` = sum of `pack.files[].bytes` (0 without a pack).

- [ ] **Step 1: Write the failing tests:**
  - `the onnx pack installs, verifies and writes its attribution` (fake fetch serving bytes whose sha256 matches a test pack passed to the constructor; after `install("pocket")`, `pocketInstalled()` is true and `ATTRIBUTION` contains `CC BY 4.0`).
  - `a pack without its completion marker is not installed` and `a damaged pack file fails verification` (flip one byte; `verifiedPocketPath` rejects with `A voice model is damaged`).
  - `pocketPackFor picks CoreML on Apple Silicon, ONNX on Windows and Linux, nothing elsewhere`, and `the shipped onnx pack matches the pinned table` (reads `pocket-onnx-assets.json`; 9 files; `pocketDownloadBytes()` = 199,269,026).
  - The existing Apple Silicon Pocket tests stay unchanged and green.
- [ ] **Step 2:** Run `npm run -w @crucible/desktop test -- test/voice-assets.test.ts`. Expected: FAIL.
- [ ] **Step 3:** Implement; downloads keep the existing HTTPS, size and hash checks.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit `feat(voice): Pocket ONNX pack for Windows and Linux`.

### Task 4: The Pocket worker (sherpa-onnx in a utilityProcess)

**Files:**
- Create: `apps/desktop/src/voice/pocket-worker.ts` (entry: reads `--models <dir>` from `process.argv`, serves `process.parentPort`)
- Create: `apps/desktop/src/voice/pocket-protocol.ts` (pure protocol handling, testable without Electron)
- Modify: `apps/desktop/src/voice/workers.ts` (`Synthesizer` takes a channel; add `UtilityChannel`), `apps/desktop/src/voice/host.ts` (construct today's helpers as `new Synthesizer((onLine, onFailure) => new LineWorker(path, args, onLine, onFailure), failed)`; no behavior change)
- Modify: `apps/desktop/package.json` (dependency `"sherpa-onnx-node": "2.0.1"`), `apps/desktop/build.mjs` (bundle `src/voice/pocket-worker.ts` → `dist/voice-pocket-worker.cjs` with `external: ["sherpa-onnx-node"]`; copy `node_modules/sherpa-onnx-node` and the installed `sherpa-onnx-<os>-<arch>` package into `dist/node_modules`, `os` = `win` for win32)
- Test: `apps/desktop/test/pocket-protocol.test.ts`

**Interfaces:**
- Produces: `interface PocketEngine { sampleRate: number; generate(text: string, onChunk: (samples: Float32Array) => boolean): Promise<void> }` (return `false` from `onChunk` to stop).
- Produces: `servePocket(engine: PocketEngine, send: (line: string) => void): (line: string) => void` — emits `{"type":"ready"}` once; handles `speak`/`cancel` per the README TTS protocol: `audio` events carry base64 mono PCM16 little-endian at `engine.sampleRate`, each ≤ 262,144 bytes before base64; a new `speak` cancels the previous id; text > 4,096 UTF-8 bytes, empty text, or an engine error → `{"type":"error","id",...}`; `done` after the last chunk or a cancel.
- Produces: `createSherpaPocket(models: string): PocketEngine` (in `pocket-worker.ts`) — `new sherpa.OfflineTts` with `model.pocket` = the seven files, reference audio `voice.wav`, `numThreads: 2`; streaming through the generation callback.
- Produces: `interface WorkerChannel { send(line: string): void; close(): Promise<void> }`; `LineWorker` implements it; `UtilityChannel(script: string, args: string[], onLine, onFailure)` wraps `utilityProcess.fork(script, args, { serviceName: "Crucible spoken replies", stdio: "ignore" })` exchanging strings over `postMessage`/`on("message")`. `Synthesizer` constructor becomes `(channel: (onLine, onFailure) => WorkerChannel, failure)`.

- [ ] **Step 1: Write the failing tests** (`pocket-protocol.test.ts`, fake engine yielding fixed Float32 chunks):
  - `announces ready, streams audio, then done`: after `speak`, outputs are `ready`, ≥1 `audio` (decoded PCM16 length = samples × 2, `sampleRate` = engine's), `done`.
  - `a new speak cancels the previous one`: the first id gets `done` without further `audio`.
  - `oversized or unspeakable text errors without stopping the worker`: 4,097-byte text → `error` for that id; engine throwing on `"🙂🙂"` → `error`; a following valid `speak` still streams.
  - `cancel stops a long generation`: `onChunk` returns `false` after `cancel`; `done` follows.
- [ ] **Step 2:** Run `npm run -w @crucible/desktop test -- test/pocket-protocol.test.ts`. Expected: FAIL.
- [ ] **Step 3:** Implement `pocket-protocol.ts`, `pocket-worker.ts`, the channel split and the build changes. The existing `Synthesizer`/`LineWorker` tests keep passing.
- [ ] **Step 4:** Run `npm run -w @crucible/desktop test` and `npm run -w @crucible/desktop typecheck`. Expected: PASS. Then `npm run -w @crucible/desktop build` and confirm `dist/voice-pocket-worker.cjs` and `dist/node_modules/sherpa-onnx-node` exist.
- [ ] **Step 5:** Commit `feat(voice): Pocket worker on sherpa-onnx`.

### Task 5: Capability status and text-only sessions

**Files:**
- Modify: `apps/desktop/src/voice/host.ts`, `apps/desktop/src/voice/protocol.ts` (`tts: z.enum(["system", "pocket", "none"])`)
- Test: `apps/desktop/test/voice-host.test.ts`

**Interfaces:**
- Consumes: `helperPath`, `helperEnvironment` (Task 2); `VoiceAssets.pocketDownloadBytes()`, `pocketPackFor` (Task 3); `UtilityChannel`, `WorkerChannel` (Task 4); `--check-cpu` (Task 1).
- Produces: `status()` → `{ available, reason?, installed, tts: ("system"|"pocket")[], pocketAvailable, pocketDownloadBytes, pocketEngine: "coreml" | "onnx" | null }` (`pocketEngine` = the platform pack's `engine`), where `available` = whisper helper present **and** `--check-cpu` printed `cpu ok` (run once per host, 5 s timeout, cached); `system` listed only on `darwin` when `crucible-tts` exists; `pocket` listed when Pocket can run (Apple Silicon: `crucible-pocket-tts` helper; win32/linux: `voice-pocket-worker.cjs` beside `main.cjs`) and its pack is installed; `pocketAvailable` = Pocket can run. The `darwin-arm64` check and its message go; reasons: CPU → the Global Constraints text; missing whisper helper → `Local voice is not included in this build.`
- Produces: `start(..., tts: "system"|"pocket"|"none", ...)`; `Session.synthesizer: Synthesizer | undefined`; with `none` no synthesizer starts, `speak()` returns without publishing `speaking`/`audio`, and `reply` text events are unchanged. On win32/linux `pocket` starts `UtilityChannel(join(__dirname-equivalent, "voice-pocket-worker.cjs"), ["--models", pocketPath])` (the script path comes from a new `Options.pocketWorker?: string`, set in `main.ts`).

- [ ] **Step 1: Write the failing tests:**
  - `a CPU without AVX2 makes voice unavailable with the reason` (fixture `crucible-whisper` script printing `cpu unsupported`, exit 3): `available` false, `reason` = Global Constraints text.
  - `linux status offers Pocket from the worker, not system` (`platform: "linux"`, `pocketWorker` set, pack installed): `tts` = `["pocket"]`, `pocketAvailable` true, `pocketDownloadBytes` > 0.
  - `a text-only session never starts a synthesizer`: `start` with `tts: "none"`; a `reply` decision publishes `reply` text and no `speaking`/`audio`; the Synthesizer mock was never constructed.
  - Apple Silicon fixtures still report `tts` `["system", "pocket"]` exactly as before.
- [ ] **Step 2:** Run `npm run -w @crucible/desktop test -- test/voice-host.test.ts`. Expected: FAIL.
- [ ] **Step 3:** Implement, and set `pocketWorker: join(__dirname, "voice-pocket-worker.cjs")` in `main.ts`.
- [ ] **Step 4:** Run `npm run -w @crucible/desktop test` and `typecheck`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(voice): capability status and text-only sessions`.

### Task 6: The panel on Windows and Linux

**Files:**
- Modify: `apps/vscode-extension/webview-ui/src/desktop/voice/voiceBridge.ts` (`VoiceSynthesizer` adds `"none"`; `VoiceStatus.pocketDownloadBytes: number`, `VoiceStatus.pocketEngine: "coreml" | "onnx" | null`), `VoicePanel.tsx`
- Test: `apps/vscode-extension/webview-ui/src/desktop/voice/VoicePanel.test.tsx`

**Interfaces:**
- Consumes: Task 5's status shape (`pocketDownloadBytes`, `pocketEngine`) and `"none"`.
- The "Spoken replies" select always offers `Text only` (value `none`); initial choice = `system` if listed, else `pocket` if listed, else `none`. Start is enabled for `none`. When `pocketAvailable` and Pocket is not installed, the install button reads `Install spoken replies (about N MB)` when `pocketEngine === "onnx"` (N = `Math.round(pocketDownloadBytes / 1e7) * 10`) and `Install Pocket TTS` when `"coreml"`. Reuse `BtnPrimary`/`BtnGhost`, the existing select styling and semantic tokens only.

- [ ] **Step 1: Write the failing tests:**
  - `offers text-only replies and starts without a voice` (status `tts: []`, `pocketAvailable: true`): option `Text only` selected, `Start voice session` enabled, button `Install spoken replies (about 200 MB)` shown.
  - `shows the processor reason and disables start` (status `available: false` with the AVX2 reason): reason text shown, no start button.
  - Apple Silicon status still shows `Install Pocket TTS`.
- [ ] **Step 2:** Run `npx vitest run --maxWorkers=2 src/desktop/voice/VoicePanel.test.tsx`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the voice tests and `npx tsc --noEmit -p .`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(voice): text-only and spoken-reply install in the panel`.

### Task 7: Packaging, notices and CI smoke on Windows and Linux

**Files:**
- Modify: `apps/desktop/package.mjs` (bundle voice for `win32-x64`/`linux-x64`: `voice-native/crucible-whisper[.exe]`, `LICENSE.whisper.cpp`, `voice-pocket-worker.cjs`, `node_modules/sherpa-onnx-node` + platform package; write `THIRD_PARTY_NOTICES` with whisper.cpp MIT, sherpa-onnx Apache-2.0, onnxruntime MIT and the attribution text)
- Create: `scripts/voice/voice-smoke.mjs`
- Create: `.github/workflows/voice-platforms.yml`
- Modify: the spec's Decision 6 and Decision 3 lines (workflow name; "about 200 MB")
- Test: `apps/desktop/test/voice-package.test.ts` for the extracted `voicePayload(platform: string): string[]` used by `package.mjs`

**Interfaces:**
- Produces: `voicePayload("win32-x64")` lists `voice-native/crucible-whisper.exe`, `voice-native/LICENSE.whisper.cpp`, `voice-pocket-worker.cjs`, `node_modules/sherpa-onnx-node`, `node_modules/sherpa-onnx-win-x64`; `linux-x64` the same without `.exe` and with `sherpa-onnx-linux-x64`; `darwin-arm64` returns today's list unchanged.
- Produces: `node scripts/voice/voice-smoke.mjs --helpers <dir> --models <dir>`: (1) `crucible-whisper --check-cpu` → `cpu ok`; (2) downloads the base Whisper model, the VAD model and the Pocket ONNX pack with the app's pins; (3) Pocket (through `servePocket` and `createSherpaPocket` in plain Node) says `Open the controller loop file.`; (4) resamples to 16 kHz PCM16, sends it to `crucible-whisper --model`, asserts the transcript contains `controller`; (5) sends one second to `--vad` and asserts probabilities in [0,1]. Exits non-zero with the failing step named.
- Workflow `voice-platforms`: on `workflow_dispatch` and pull requests touching `apps/desktop/**`; matrix `ubuntu-latest`, `windows-latest`; steps: `npm ci`, webview `npm ci`, `npm run build`, `npm run -w @crucible/desktop build:voice:portable`, `npm run -w @crucible/desktop test`, `voice-smoke.mjs`, `npm run -w @crucible/desktop package -- --preview --out $RUNNER_TEMP/package`, upload the package (14 days). Model downloads cached with `actions/cache` keyed on the pinned hashes.

- [ ] **Step 1: Write the failing test** (`voice-package.test.ts`): the three `voicePayload` cases above.
- [ ] **Step 2:** Run `npm run -w @crucible/desktop test -- test/voice-package.test.ts`. Expected: FAIL.
- [ ] **Step 3:** Implement `voicePayload`, the packaging changes, the smoke script and the workflow; amend the spec lines.
- [ ] **Step 4:** Run the desktop tests (PASS), then the smoke locally on this Mac against the `build-portable.mjs` output and the darwin-arm64 sherpa package: `node scripts/voice/voice-smoke.mjs --helpers /tmp/vp --models /tmp/vm`. Expected: all five steps pass. Push the branch and run `voice-platforms` on both runners (`gh workflow run voice-platforms.yml --ref feat/voice-multiplatform`). Expected: both jobs green with the package artifacts uploaded. Pushing needs the user's yes.
- [ ] **Step 5:** Commit `feat(voice): Windows and Linux packages with a voice smoke in CI`.
