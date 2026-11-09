# Crucible local voice benchmark and optional install

## What runs on the Mac

Speech recognition and speech synthesis run on the Mac. The conversation responder can use the user's configured text provider. While a coding turn is active, the responder answers status questions from the latest progress snapshot and sends corrections through the turn-scoped steering path. Voice and typed messages use the same routing. The app starts speaking as soon as it has a short response segment. When the user interrupts, it stops playback and keeps listening.

A voice session keeps its recognizer, synthesizer, and optional end-of-turn model warm. Run first inference during session startup so the first spoken exchange avoids paying initialization after the user finishes. Measure simultaneous footprint during an active coding turn before setting the supported memory minimum. Model download size, maximum resident set size, and macOS physical footprint are separate measurements.

## How a spoken turn starts and ends

- During an open voice session, run local voice activity detection on echo-cancelled microphone frames. Keep about 250 ms of audio in a rolling buffer. When speech is sustained for roughly 100 ms, pass the buffered lead-in and new frames to the recognizer. These are initial tuning values.
- Stream partial text while the user speaks. At a likely pause, the voice session checks whether two Whisper transcriptions agree and discards the check if speech resumes. The endpoint models evaluated below were part of the earlier experiment.
- Route the finalized utterance to conversation or turn-scoped steering. Partial text can update the display. It cannot send a correction or approve an action. While Crucible speaks, confirmed user speech cancels playback and starts a new utterance. The coding turn continues until its own steering path accepts a change.
- Replay hesitation, short and long pauses, a clipped first syllable, speaker echo, and interruptions. Score early cutoffs, missed starts, false barge-ins and the time from the true end of speech to a final transcript. The offline pilot below contains none of these timing measurements.

## Pilot on a 36 GiB M3 Pro MacBook Pro

macOS 26.6.2. Six synthetic, clean English clips, totaling 19.78 seconds, with three macOS voices and 16 kHz mono PCM. This is a smoke test for transcripts, startup and process footprint. The pilot used prepared files with no active Crucible coding workload. It does not measure latency from a live microphone or establish the minimum memory for voice.

| Candidate | Model assets | Peak physical footprint | Median six-clip batch | Transcript matches |
| --- | ---: | ---: | ---: | ---: |
| Whisper.cpp Base | 141.1 MiB | 408.7 MiB | 0.65 s | 4/6 |
| Whisper.cpp Base with project vocabulary | Same weights | 406.2 MiB | 0.72 s | 5/6 |
| Whisper.cpp Small | 465.0 MiB | 881.2 MiB | 1.63 s | 6/6 |

Whisper.cpp v1.9.5 used the multilingual weights with English decoding, Metal, four threads, beam size five and no VAD. Each trial used a fresh process. The six-clip trial loaded the model once and processed all clips sequentially. Five trials of each shape ran under `/usr/bin/time -l`. Maximum RSS for Base, prompted Base and Small was 377.2, 374.8 and 849.7 MiB, respectively. [Whisper.cpp release](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.5) · [Pinned weights](https://huggingface.co/ggerganov/whisper.cpp/tree/5359861c739e955e79d9a303bcbc70fb988958b1)

| Candidate | First launch, 1.7 s clip | Next four launches, same clip |
| --- | ---: | ---: |
| Base | 5.00 s | 0.26–0.32 s |
| Base with project vocabulary | 0.52 s | 0.28–0.30 s |
| Small | 0.76 s | 0.50–0.52 s |

These times run from process launch to exit with the model already downloaded. The cause of the first Base launch delay was not isolated. The runs do not include audio capture, endpoint detection, the text provider or playback.

Transcript scoring ignored case and punctuation and treated spoken “dot py” as `.py`. Base returned “SQ light” and “postgurs” for SQLite and Postgres in the correction, and “controller dot pi” for “controller dot py” in the filename clip. A prompt listing SQLite, Postgres, `controller.py` and API client recovered the correction and filename but still returned “steering cue.” The prompt deliberately included terms already known from the corpus. Test the approach on held-out tasks before attributing a general accuracy gain. Small recovered all the terms and peaked 472.5 MiB above Base. All three runs preserved the migration negation. Check corrections and file references separately from average word error rate because a single wrong token can change the intended action.

A separate check used four new synthetic clips about Crucible files on `main`. The original hint did not list these files. Base with and without the hint matched two of four, and both variants rendered “read draft vault” as “RedraftVolt” and “native find” as “native fine.” Small matched all four after normalizing spoken extensions, case and punctuation. In this check the filename score concerns the spoken stem. Resolving spaces to hyphens still needs workspace context.

### Claude Code lane

Claude Code ran FluidAudio v0.18.3 with Parakeet EOU 120M, 160 ms chunks, Core ML `.all` compute units and a 1,280 ms end-of-utterance debounce. It used Pocket TTS Core ML `v2.1/english_2026-10`, FP16, with the default GPU placement. Both lanes used the same corpus, Mac and exclusive benchmark lock. Claude ran each configuration twice in a fresh process. The shared-process test included a short interval of recognition and synthesis in parallel. [FluidAudio](https://github.com/FluidInference/FluidAudio) · [Parakeet assets](https://huggingface.co/FluidInference/parakeet-realtime-eou-120m-coreml) · [Pocket TTS Core ML assets](https://huggingface.co/FluidInference/pocket-tts-coreml)

| Configuration | Model files | Highest physical footprint | Highest RSS |
| --- | ---: | ---: | ---: |
| Parakeet EOU | 213.7 MiB | 354.9 MiB | 463.7 MiB |
| Pocket TTS | 504.4 MiB | 874.4 MiB | 754.5 MiB |
| Both in one helper | 718.0 MiB | 1,066.1 MiB | 965.4 MiB |

Both engines together used about 555 MiB just after loading and peaked at 1.04 GiB after running inference. These figures describe the test helper. The coding agent, desktop app, microphone capture, speaker playback and echo cancellation were absent. Both runs returned success and emitted a Core ML runtime diagnostic at exit. Check teardown in the integrated test.

Parakeet matched two of six clips, with 8 word errors over 55 reference words using case- and punctuation-insensitive scoring. It preserved the database names and migration negation, but lost some opening words and the filename extension. Its endpoint fired on all six clips after 1,280–1,920 ms of zero-valued audio supplied after each clip. The harness supplied frames as fast as possible. This measures the amount of silence consumed at that debounce setting, while wall-clock reply latency remains unmeasured. Warm chunk compute was around 16 ms median per 160 ms of audio.

In a follow-up using the same harness, prepending 250 ms of silence recovered several opening words. Overall word errors went from 8 to 10 of 55, including new errors on SQLite. Leading audio and frame alignment need a real-time replay test. Padding these prepared files did not solve recognition quality.

Pocket TTS produced its first generated audio chunk in 45 ms median after warmup in the combined helper (43–62 ms, 14 replies). Its very first synthesis took 3.99 seconds after a 3.35-second first model load in the standalone run. The first synthesis on the next standalone process took 0.26 seconds after a 0.34-second model load. Playback and network delays are outside these timings.

## Experiment to qualify a supported Mac

1. Record the Mac, unified memory, macOS, model revisions, backend, runtime settings, power state, and installed bytes. Run each lane by itself. Use the same replay schedule, provider and coding workload for paired comparisons.
2. Replay recordings at real-time speed. Use consented human recordings with accents, background noise, short pauses, speech over playback, and terms drawn from realistic tasks. Add control utterances for status, corrections, negation, approval and cancellation. Score word error rate, exact critical terms, intent, false end-of-turn events and whether a correction reached the intended turn.
3. Measure fresh voice startup separately from warm exchanges. Timestamp end of speech, final transcript, first response text, first generated PCM, first audible playback and playback stop on interruption. Report median and p95 with sample counts. Run one fixed-text TTS comparison and a separate live-provider run to distinguish speech latency from provider latency. Use 1.5 seconds from end of speech to audible reply and 150 ms from detected interruption to playback stop as initial p95 targets to validate in the prototype.
4. Measure four states against the same coding workload: voice off, both models loaded and idle, both active during a voice exchange including interruption, and the session closed. Record helper physical footprint and whole-system memory pressure, compression and swap deltas across the same intervals. Report CPU and sustained results alongside peak footprint. Use measurements of both live models together for the memory budget. Repeat with a clean baseline and a representative editor and browser workload.
5. Start with Apple Silicon Macs with 8 GiB and 16 GiB if available. Only list a memory tier as supported after the paired test shows a responsive coding turn, clean interruption and teardown, and no sustained memory pressure or voice-attributable swap growth during the representative workload. Compare recognition reliability before choosing the fastest model that fits each qualified tier.

Keep Whisper Small as the accuracy reference in the next round. Compare it with Parakeet and Base with hints derived from the current project before the test utterances are known. If the smaller recognizer reliably preserves steering intent and improves the combined footprint or live latency, use it for that hardware tier. If only Small meets the accuracy target, qualify tiers with Small and Pocket TTS together. A tier that fails the combined test keeps the text setup available. The pilot gives no basis for requiring a larger Mac.

For a reproducible first pass, synthesize each sentence using `say -v VOICE -o clip.aiff`, then run `afconvert -f WAVE -d LEI16@16000 -c 1 clip.aiff clip.wav`. With the pinned Whisper.cpp CLI built in Release mode, run `/usr/bin/time -l whisper-cli -m ggml-base.bin -l en -t 4 -otxt -nt` with the WAV files appended in the order below. Repeat with `ggml-small.bin` for Small. For the prompted run, add `--prompt 'Project vocabulary: SQLite, Postgres, controller.py, API client.' --carry-initial-prompt`.

| Voice | Reference |
| --- | --- |
| Samantha | How is the coding task going? |
| Daniel | Actually, use SQLite instead of Postgres. |
| Aman | Open controller dot py and inspect the steering queue. |
| Samantha | Why did you choose SQLite? Keep the current implementation for now. |
| Daniel | Do not run the migration. Just explain what it would change. |
| Aman | Check the workspace, then run the unit tests for the API client. |

## First-run and Settings behavior

The current setup on `main` at `3aed6eb5` has a checked-by-default option for local embedding and reranking models in `apps/vscode-extension/webview-ui/src/setup/SetupApp.tsx`. The desktop contract in `apps/desktop/src/contract.ts` carries that choice in `setup/install`.

Show a separate, unchecked **Enable local voice** option in first-run setup when the desktop build includes the voice helpers. For a supported release, show measured download sizes and qualified system requirements for the chosen speech bundle. The prototype downloads one recognizer and uses the installed macOS voice. Pocket TTS can be installed separately.

Skipping voice opens the text workspace without downloading speech assets. A cancelled or failed download leaves text ready and exposes Retry in Settings. Settings also supports enabling voice later, disabling it, and removing downloaded models to reclaim disk. Ask for microphone permission when starting the first voice session. Start the audio helper on demand and stop it when the session ends so voice memory returns to the system. Test first-run skip, cancel, retry, later opt-in, removal, microphone denial and reopening after session teardown.

## Desktop prototype

The Apple Silicon desktop build supports Whisper Small and Base as swappable recognizers with English decoding. Parakeet remains a benchmark candidate. System speech uses an installed macOS voice. Pocket TTS is available when its optional helper is bundled and its English pack has been downloaded. The app verifies the pinned assets before launching a session. First-run voice downloads and Settings downloads use the same installer. The helpers stop when the session, project view or desktop process closes.

From the repository root, build the desktop and the local helpers with Node 22.12 or newer and the Apple build tools. The optional Pocket build needs a recent Swift toolchain. The native worker README describes the toolchain and output.

```sh
npm ci
npm run build
npm run -w @crucible/desktop build:voice
# Optional, on a recent Swift toolchain
(cd apps/desktop && bash voice-native/build-pocket.sh dist/voice-native)
npm run -w @crucible/desktop start
```

The desktop needs a project runtime built from this checkout to expose the conversation and steering endpoints. Use the desktop README’s development workflow with `CRUCIBLE_DEV_RUNTIME` and `CRUCIBLE_DESKTOP_PROFILE` to isolate the runtime and application storage. Open a conversation and use **Voice** in its toolbar. Install Whisper Small if prompted, select a voice, and start the session. Test a status question during a coding turn, a correction while it is busy, and a spoken interruption of a reply. Compare the queued and incorporated receipts. Keep any approval card on screen so you can confirm that speaking does not approve it.

Hands-free capture starts a Whisper check after about half a second of quiet and keeps recording while transcription runs. After at least 2.5 seconds of quiet, a clear transcript can send if a second decode with more trailing audio agrees. Resumed speech invalidates the earlier check. If the pause remains unresolved, the voice panel offers review when it has a transcript. Short or garbled interruptions stay out of coding chat, and a correction that began over playback needs a clear instruction and enough speech after playback stops. **Send now** finishes the capture explicitly. In **Push to talk** mode, hold to capture and release to send. **Stop speaking** controls playback. The voice panel shows an estimated stop time based on the renderer and audio output clocks. A physical loopback measurement is still needed to qualify audible interruption latency.

During an active coding turn, begin a spoken instruction with “Correction” or “Change of plan” and pause briefly after the cue. An explicit cue goes straight to the active turn without waiting for a model to classify the message. The receipt confirms delivery to the coding model.

Use the same scenario with both recognizers and both installed synthesis options. Roughly 500 MiB of extra memory or up to a second of extra reply latency may be worth better recognition or speech quality if the machine stays responsive. Assess interruption separately against the shorter playback-stop target. The pilot has not qualified a hardware minimum or a default model bundle.

The integrated smoke test used a sandboxed Electron renderer, a synthetic audio clip, the local workers, and a scripted coding turn on the actual Python routes. It transcribed the status question, generated audio through both synthesis choices, queued and incorporated a correction, and skipped the superseded action. A physical-microphone session with a configured provider is the next acceptance check. The smoke test does not measure live reply latency or qualify a memory tier.

## End-of-turn release comparison

The earlier preview served as the baseline. Its hands-free send waited at least 2.5 seconds. Smart Turn v3.2 scored two finished synthetic paragraphs at 0.086 and 0.122 during worker replay. Both were sent after the preview verified that a second Whisper decode agreed. In a separate replay, a clipped filename scored 0.933 and stayed in review because the filename changed between decodes. Any score threshold low enough to accept the paragraphs also accepts the clipped request's score.

| Lane | What it tests |
| --- | --- |
| Whisper Small with Smart Turn v3.2 | The previous acoustic model and session policy. |
| Whisper Small with Namo v1 English | Whether a local text classifier can recognize a finished thought from the accumulating transcript. The quantized model uses ONNX and is Apache 2.0 licensed. [Model card](https://huggingface.co/videosdk-live/Namo-Turn-Detector-v1-English) |
| Whisper Small with silence and transcript stability | The control. Wait through the protected pause, check the transcript again, and measure the same outcomes with no completion model. |
| Parakeet Realtime EOU 120M | A separate streaming recognizer and endpoint track. Score transcription and critical terms as well as endpointing. [Model card](https://huggingface.co/FluidInference/parakeet-realtime-eou-120m-coreml) |

LiveKit v1-mini is another local audio reference. Its model license requires LiveKit Agents. Include it as a framework-based comparison if that dependency is acceptable. In LiveKit's English validation benchmark, with a 600 ms mean delay budget, v1-mini cut off 12.1% of scored mid-turn pauses and Smart Turn v3.2 cut off 14.8%. The hosted v1 scored 4.5% in the same comparison. These rates describe the vendor's test settings and corpus. [Benchmark](https://github.com/livekit/eot-bench/blob/main/output/livekit__eot-bench-data__validation__min_silence_100ms/en/comparison/report.md) · [Model license](https://huggingface.co/livekit/turn-detector/blob/main/LICENSE)

In a synthetic Parakeet replay, two voices each read a short question. Each continuation case added a longer passage after 1, 2, or 3 seconds of silence. At the existing 1,280 ms debounce setting, Parakeet had already signaled an endpoint when the recording ended in 0/2 one-second cases, 2/2 two-second cases, and 2/2 three-second cases. Both standalone short questions signaled after 1,600 ms of silence was fed. These are helper events from prepared audio, with no Crucible send policy or real-time playback. The sandbox emitted Core ML diagnostics, so this run provides no timing or resource comparison.

1. Replay the same consented human audio through each lane at microphone speed. Label the true end and internal pauses. Include finished 15–30 second requests, an apparently complete sentence followed by a 2-second thinking pause and a continuation, a correction after playback starts, stop-only speech, background speech, clipped filenames, and negations. Include requests that approach the current capture limit and report length-limit failures separately.
2. Tune each model on a development set. Hold out speakers and scripts for the reported comparison. Keep the same capture, playback cutoff, and turn-scoped send rules. The local voice session decides whether to wait, clarify, or send. A resumed utterance invalidates the earlier decision. Use the same provider for end-to-end runs.
3. Report early sends per labeled mid-turn pause, needless repeat prompts per clear finished request, missed requests, p50 and p95 from the true end to handoff, and p95 audible playback cutoff. Report counts by scenario, exact filenames and negations, and incremental CPU and peak physical memory with an active coding turn. Compare error rates at similar handoff delays and include the share of true ends accepted at each setting.
4. Require the two-second continuation cases to arrive as one request. Require stop-only speech and playback echo to stay out of chat. Ship a model only if it improves on the control at comparable delay and stays within the measured resource budget. Keep push-to-talk available as an explicit way to finish a request.

### Public-speech replay setup

The replay selects eight development turns and 24 evaluation turns from distinct English conversations. It extends one annotated pause in each of eight evaluation turns to two seconds and includes those variants alongside the originals. The test split is by conversation ID. The dataset supplies no speaker IDs to enforce a speaker split. The audio is from human-to-agent conversations and covers conversational timing. A separate corpus is needed for coding corrections, filenames, and physical playback overlap. [LiveKit benchmark dataset, CC BY 4.0](https://huggingface.co/datasets/livekit/eot-bench-data)

The three-way renderer replay used the prior Smart Turn worker and scorer protocol. The desktop build now uses the transcript stability lane without the model scorer. The Namo experiment used `tokenizer.json` and `model_quant.onnx` from revision `4dcc9713be5071ef43e510f72b1eefcb2f99cb04`, with `onnxruntime==1.23.2` and `tokenizers==0.22.2`. [Pinned Namo artifacts](https://huggingface.co/videosdk-live/Namo-Turn-Detector-v1-English/tree/4dcc9713be5071ef43e510f72b1eefcb2f99cb04)

```sh
python3 scripts/eval/voice-endpoint-corpus.py "$BENCH_DIR/corpus"
```

The replay feeds all three Whisper lanes the same 20 ms frames at microphone speed and shares identical Whisper decodes. Smart Turn uses the preview threshold. Namo chooses its threshold from the development pause scores before evaluation. The no-model control uses the existing transcript stability fallback. All three run the same Crucible voice-session code and stop at the coding handoff. They simulate the microphone frames and capture sends without speaking replies or calling a provider. The summary treats a send more than 150 ms before the labeled end as early to account for annotation and frame alignment.

For Parakeet, point `PARAKEET_160MS_MODELS` at the downloaded Core ML model directory. Use a Swift toolchain that builds FluidAudio v0.18.3 and run the helper separately. It reports the first endpoint by frame position and continues through the full clip for recognition inspection. Its delay is in audio time. Crucible's send policy and device latency are measured separately.

```sh
swift run --package-path scripts/eval/voice-parakeet --scratch-path "$BENCH_DIR/swift" \
  -c release VoiceParakeetReplay "$BENCH_DIR/corpus/manifest.json" \
  "$PARAKEET_160MS_MODELS" 1280 "$BENCH_DIR/parakeet-1280.json"
python3 scripts/eval/voice-endpoint-summary.py "$BENCH_DIR/replay.json" \
  --parakeet "$BENCH_DIR/parakeet-1280.json" --parakeet-manifest "$BENCH_DIR/corpus/manifest.json"
```

### Public-speech replay results

Apple M3 Pro, 36 GiB, macOS 26.6.2. There were 32 evaluation cases, including the eight edited pause variants. Namo used a 0.95 threshold selected from the eight development turns. The paired replay used Whisper's CPU path in the sandbox. Handoff delay is relative to the dataset's labeled end, which can differ from when the app starts counting quiet.

| Whisper Small configuration | Sent once after the end | Early sends | Two-second pauses kept intact | Repeat prompts | Handoff p50 / p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Smart Turn v3.2 | 31/32 | 1 | 8/8 | 0 | 2.42 / 3.45 s |
| Namo v1 English | 31/32 | 1 | 8/8 | 0 | 2.40 / 3.45 s |
| Pause and transcript stability | 31/32 | 1 | 8/8 | 0 | 2.42 / 3.51 s |

All three split the same long recording during about 3.4 seconds of quiet. At that pause Smart Turn scored about 0.009. The transcript stability check cleared the send anyway. Namo scored the same pause above 0.99 and sent about 380 ms sooner. In the eight long cases each lane sent seven intact. For those seven, median handoff was 2.70 s with Smart Turn, 2.40 s with Namo, and 3.06 s for the control. Namo avoided one repeat prompt in the development set. That sample informed the threshold and is separate from the held-out results.

| Parakeet 120M debounce | Early endpoints on original turns | Missed endpoints on original turns | Endpoints before speech resumed in pause variants | Median audio fed after end on on-time original turns |
| --- | ---: | ---: | ---: | ---: |
| 1,280 ms | 5/24 | 0/24 | 5/8 | 1.60 s |
| 2,560 ms | 2/24 | 2/24 | 1/8 | 2.88 s |

The Parakeet helper ran the same audio in 160 ms frames and allowed up to four seconds of trailing silence. The table measures the raw model endpoint in audio time. The two-second variants can also have early endpoints after speech resumes and before the final end. The longer debounce traded earlier cutoffs for slower or missing endpoints.

On the 24 original evaluation turns, full-clip Whisper Small had 91 word errors over 556 reference words (16.4%). Parakeet had 100 (18.0%). Parakeet continued receiving audio after any early endpoint for this recognition check. The result measures transcript quality with the entire clip available and includes the dataset's reference and normalization differences.

Each worker was profiled alone with macOS `time -l`. Peak physical footprint was 812 MiB for Whisper Small on CPU, 66 MiB for the Smart Turn helper, 368 MiB for the Python and ONNX Namo helper, and 350–351 MiB for the combined Parakeet recognizer and endpoint helper. The Whisper and detector batches used the 24 original evaluation recordings or their Whisper transcripts. The Parakeet batch included the development turns and edited pause variants. These are standalone process peaks and cannot be added to estimate an active Crucible session. The Namo footprint includes its Python runtime.

Keep the current preview as the reference and hold the release default. Namo's modest latency improvement did not change the held-out errors, and Parakeet's raw endpoint missed the pause target at both settings. Fix the long-pause split in the shared send policy before choosing a detector. All 36 existing voice-session, segmentation, and capture tests passed, including mocked stop-only speech and playback overlap. Physical-mic echo, audible interruption cutoff, and memory pressure with an active coding turn still need release measurements.

### Follow-up on live repeat prompts

The instrumented live run recorded seven clarification events. Five followed below-threshold scores with the transcript checks clear, and two were blocked by the playback-overlap rule despite scores of 0.897 and 0.981. The live trace retained scores and decision flags. Exact audio and rejected transcripts were unavailable for replay.

The follow-up used ten saved synthetic files. These included three voices for the longer live-test question and a shorter rewrite, plus two voices for a finished paragraph and its short final question. With the current transcript fallback, Smart Turn, Namo at 0.95, and the control sent each of the ten once and asked for zero repeats. Namo's score crossed its threshold on all three longer-question variants, but on neither finished paragraph and none of the five shorter questions. Smart Turn's score crossed its threshold on six of ten. These scores are a diagnostic separate from the app's send result.

Parakeet emitted a raw endpoint after all ten clips. With 1,280 ms debounce it took 1.44–2.24 seconds of trailing audio, and with 2,560 ms debounce it took 2.72–3.52 seconds. It dropped or changed some words, including mishearing “greeting” as “breeding” in one question. The Parakeet helper did not run Crucible's send policy. The synthetic fixtures contain no playback overlap, so this run does not retest the two overlap-triggered repeats.

## Realtime conversation research

Research checked November 9, 2026. The endpoint replay established that a stable transcript can be a faithful prefix of an unfinished request. It also ended at the coding handoff. The next comparison should exercise the full exchange, including periods when Crucible is speaking and coding work is still running.

| Model or harness | Relevant mechanism |
| --- | --- |
| OpenAI GPT-Live 1 | A full-duplex voice model listens during its own speech. Its prompt can shape backchannels, patience, yielding and when to delegate. Client delegation emits a timestamp and ID. The app builds the backend request from streaming transcripts and verified application state. Stopping the spoken reply and canceling backend work are separate operations. Prompting can encourage yielding, while the actual cutoff timing needs measurement. [Model](https://developers.openai.com/api/docs/models/gpt-live-1) · [Prompting](https://developers.openai.com/api/docs/guides/live-prompting) · [Delegation](https://developers.openai.com/api/docs/guides/live-delegation) |
| OpenAI Realtime API | Semantic VAD adjusts how long it waits based on whether speech sounds finished. Low, medium and high eagerness cap the wait at 8, 4 and 2 seconds respectively. An application can receive detection events and independently control response creation and interruption. Playback truncation keeps the conversation aligned with the audio the user actually heard. [VAD](https://developers.openai.com/api/docs/guides/realtime-vad) · [Wait limits](https://developers.openai.com/api/reference/resources/realtime/client-events) · [Conversation events](https://developers.openai.com/api/docs/guides/realtime-conversations#interruption-and-truncation) |
| Pipecat | Its optional LLM turn-completion strategy can suppress a response and wait when a phrase sounds cut off or when a speaker needs time to think. The example waits about five and ten seconds respectively before prompting. The audio turn detector and the LLM completion check serve different purposes. [Example](https://github.com/pipecat-ai/pipecat/blob/main/examples/turn-management/turn-management-filter-incomplete-turns.py) · [Turn completion source](https://reference-server.pipecat.ai/en/latest/_modules/pipecat/turns/user_turn_completion_mixin.html) |
| Deepgram Flux | `EagerEndOfTurn` allows speculative preparation. `TurnResumed` cancels that preparation. `EndOfTurn` is the release point for the response. Crucible can use this event pattern even with a different recognizer. [Eager end-of-turn guide](https://developers.deepgram.com/docs/flux/voice-agent-eager-eot) |
| LiveKit Agents | Dynamic endpointing can adjust delay based on pause patterns during the session. A separate adaptive interruption model distinguishes overlapping acknowledgments from genuine barge-ins. The harness can resume a reply after a false interruption and discard preemptive generation when the final transcript changes. [Turn tuning](https://docs.livekit.io/agents/logic/turns/tuning/) · [Adaptive interruption](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/) |
| Kyutai Moshi | A local full-duplex reference. It models input and output audio continuously, including overlap, and publishes a quantized MLX build tested by its authors on an M3 MacBook Pro. Crucible would need to measure its footprint, interaction quality and handoff behavior separately. [Paper](https://arxiv.org/abs/2410.00037) · [MLX implementation](https://github.com/kyutai-labs/moshi#mlx-implementation-for-local-inference-on-macos) |

Codex's public app-server protocol has `turn/steer` for attaching input to an in-flight turn and identifies the expected turn. This is the coding-side boundary to preserve in Crucible. GPT-Live's public client delegation gives a separate reference for the voice-side boundary. [Codex turn steering](https://learn.chatgpt.com/docs/app-server#steer-an-active-turn) · [GPT-Live client delegation](https://developers.openai.com/api/docs/guides/live-delegation)

### A comparison that tests the conversation

Run one session on a continuous clock. Stream input through pauses and while the assistant plays audio. Feed a scripted coding backend with delayed status and results. Record user audio, audible assistant output, and ordered events on separate tracks. Label when the user yields the floor, resumes a thought, gives a backchannel, or intentionally takes over. Check the actual chat or steering write independently of the transcript, spoken acknowledgment and playback stop. TurnBench provides annotated human turn and interruption cases. Full-Duplex-Bench covers pause handling, backchannels, turn-taking and interruption. The GPT-Live evaluation guide shows how to score task actions alongside those interactions. [TurnBench](https://turnbench.sesame.com/) · [Full-Duplex-Bench](https://arxiv.org/abs/2503.04721) · [GPT-Live evaluation](https://developers.openai.com/cookbook/examples/audio/voice_agent_evaluation)

- Pair the same spoken prefix with different endings. One speaker stops after a complete sentence. Another pauses for two, three or four seconds and adds a constraint or a negation. Include a longer request spanning recognizer windows, a hesitation mid-filename, a request to wait, and a resumed answer after a clarification. Judge whether the complete request reaches the backend exactly once.
- While the assistant speaks, test a listener's “mm-hmm,” a stop-only command, a long correction, speaker echo, and unrelated speech. Score false cutoffs, missed barge-ins, audible cutoff delay and whether any unintended text becomes a chat message. Then test a correction that arrives as a coding turn ends and verify it can never land in a different turn.
- Compare the current preview with a local policy that treats a pause as provisional and lets a context-aware model suggest continue listening, respond in voice, clarify, or propose a coding handoff. A provisional result is canceled when speech resumes. In this lane, test rolling recognition windows with a single accumulating request so the recognizer's window length does not define a conversation boundary.
- Use GPT-Live 1 in client-delegation mode as a hosted reference, using public or consented test audio and a recording-only backend. Optionally run Moshi MLX as a local reference for conversational behavior. Measure time from the true end to first useful audible reply and to coding handoff separately. Record missed and premature sends, duplicate or stale-turn writes, false repeat prompts, and CPU and peak footprint for local lanes. Run each against the same scenarios and report each type of error separately.

For a prototype, let the voice model interpret the developing conversation and propose what to do. Let the voice session enforce what becomes audible or reaches coding. A tentative answer can be prepared during a pause. A coding handoff should still need a current utterance revision, sufficient evidence the user yielded, clear intent and the same coding turn that the session observed. A playback stop remains local. An ambiguous detail can lead to a focused clarification that preserves the rest of the request. At a wait limit, keep the accumulated draft available for explicit send or continuation.
