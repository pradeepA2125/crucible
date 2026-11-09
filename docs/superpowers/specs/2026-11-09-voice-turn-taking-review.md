# Voice agent review: turn-taking and narration

Reviewed 2026-11-09. The voice feature is **uncommitted** in the Codex worktree
`~/.codex/worktrees/crucible-live-voice/AI editor` (detached HEAD at `3aed6eb5`). Paths
below are relative to that worktree. Commit it to a branch before anything else, or a
worktree cleanup can lose it.

## Why it feels amateurish

Two problems explain most of what you hit. Both are design problems, not tuning bugs.

1. **The end of a turn is a timer, with no sense of what you're saying.** After 2.5 s of
   quiet, the transcript is sent if two Whisper decodes match. Nothing checks whether the
   thought is finished.
2. **The voice speaks the screen answer.** The written Markdown answer from the coding
   agent is read out almost verbatim. No step rewrites it for listening.

A third issue makes both worse. Voice talks straight to the coding agent. A fragment sent
early starts a real coding turn, and the rest of your sentence then lands mid-turn as a
"correction", where a classifier may ask you to repeat yourself.

## Findings

### Turn-taking (P1: these cause the early sends and the repeat prompts)

| # | What happens | Where | Effect on you |
|---|---|---|---|
| T1 | Fixed schedule: Whisper decode at 560 ms of quiet, a recheck at 1.5 s, sent at **2.5 s** if both decodes match. | `webview-ui/src/desktop/voice/speechSegments.ts:10-13,135-148`, `voiceSession.ts:327-349` | Any thinking pause over 2.5 s splits the request. A matching transcript only shows Whisper agrees with itself. It is still a faithful prefix of an unfinished sentence. Codex's own replay found this: "Fix the long-pause split in the shared send policy before choosing a detector." |
| T2 | The "is it finished?" check is a 15-word list of trailing words (`and`, `but`, `um` …). | `speechText.ts:9-15` | Whisper removes "um"/"uh" and ends almost every decode with a period, so the check rarely fires. "I want you to review the whole feature." looks finished. |
| T3 | No real VAD. Speech means RMS above `noise × 3.5` to start and above `noise × 2` to continue, after browser AGC and noise suppression. | `speechSegments.ts:96,120`, `voiceAudio.ts:38` | Soft trailing syllables count as silence, so the pause clock starts early. Breaths, keyboard noise and the fan can reset it or start false utterances. |
| T4 | Hard 30 s cap per utterance. Whisper is re-run on the whole buffer every 480 ms with beam size 5. | `speechSegments.ts:18,131-134`, `protocol.ts:10-11`, `voice-native/whisper.cpp:55` | Natural rambling with pauses passes 30 s and gets the error "longer than 30 seconds". Cost per decode grows with length. The Whisper window ends up defining the turn. |
| T5 | At 4.5 s of quiet the draft becomes a "review" chip. | `speechSegments.ts:13,147`, `voiceSession.ts:351-361` | Waiting longer is the right instinct, but the session gives up instead of listening on. You have to tap or repeat. |
| T6 | Once sent, a turn is final. Speech that resumes starts a new message. | `voiceSession.ts:343-349,430-447` | The continuation of your sentence becomes a separate message. |
| T7 | When idle, a sent fragment immediately becomes a full coding turn. | `apps/desktop/src/voice/host.ts:220-230` → `controller.sendChatMessage` | The agent starts working on half the request. |
| T8 | During a busy turn, anything the classifier calls `unclear` gets "Do you want a quick status update, or should I change what I'm doing?" | `services/agentd-py/agentd/chat/conversation.py:55-57,415-416` | This is the "keeps asking me to repeat" prompt. A continuation fragment ("…and also the narration") is exactly what the classifier calls unclear. |
| T9 | Speech over playback needs a correction cue or one of a fixed set of verbs, otherwise it is kept for review. | `speechText.ts:30-35`, `voiceSession.ts:318-320` | Natural barge-in ("no, I meant the other file") is held back. |
| T10 | Barge-in waits for a Whisper partial with at least 2 words. Interruptions that turn out to be false are never resumed. | `voiceSession.ts:278-281` | Stopping playback waits for a decode. A cough can kill the reply for good. |
| T11 | Whisper has no project vocabulary prompt (`no_context = true`, no `initial_prompt`). | `voice-native/whisper.cpp:55-63` | The spec measured "controller dot pi" and "SQ light". A prompt from the project index recovered them. |

### Narration (P1: this is why it reads code and paths badly)

| # | What happens | Where | Effect on you |
|---|---|---|---|
| N1 | Every `chat_response` chunk of the written answer is piped to TTS. | `apps/vscode-extension/src/controller.ts` `streamTurn` → `voice.chunk`, `host.ts:298-319` | It reads the screen answer: bullets, headings, paths, identifiers, tables. |
| N2 | The only clean-up is a regex that **deletes** `` ` * _ # `` and links. | `host.ts:322-323` | `is_memory_enabled` → "ismemoryenabled". `ControllerLoop._iterate` → "ControllerLoopiterate". Table pipes and list numbers are read out. |
| N3 | Sentences are split on `[.!?]` followed by a space. | `host.ts:306` | "1. Fix X" becomes "one", then "Fix X". "e.g. " splits mid-thought. |
| N4 | Code fences are skipped without a word, and speech stops without notice at 3,500 chars. | `host.ts:307-317,53` | You can't tell whether it finished or skipped something. |
| N5 | Busy-turn replies are fixed strings about "the coding model". | `conversation.py:47-57` | "The coding model received your correction." sounds like a system log. |
| N6 | Nothing in the coding agent's prompt knows the user is listening. | `controller_prompts.py` (only `goal_corrections` added) | The agent writes for the screen every time. |

### Lower priority (P3)

- Partial decodes and the pause decode share one serial queue (`voiceSession.ts:262-268`), so the pause check can wait behind an in-flight partial.
- No sound cue for state (listening / got it / thinking). Silence after you stop is ambiguous, so people repeat themselves.

## How real-time systems do it

All of them treat silence as **evidence**, not as the decision. A model judges whether the
thought is finished, and the silence it waits for depends on that judgement.

| System | Mechanism | What to take |
|---|---|---|
| OpenAI Realtime `semantic_vad` | A classifier judges from your words whether you're done. "Trailing-off speech gets a longer timeout than a definitive statement." `eagerness` low/medium/high caps the wait at 8 / 4 / 2 s (per the Codex spec's API reference check). | A variable wait driven by completeness, with a long cap (8 s) for people who think out loud. |
| GPT-Live / ChatGPT Voice with Codex | A separate full-duplex voice model fronts the agents. It decides to listen, respond or delegate. The prompting guide says "Keep listening while the user pauses to think", "For routine questions, give one or two short sentences", and "Report only supplied tool state and verified results". Stopping speech does not cancel backend work. | The **front-man** architecture. The voice layer is its own conversational agent. It delegates whole requests to the coding agent and summarizes results for listening. |
| Pipecat | Smart Turn v3 (an audio model scoring the last 8 s for prosody) runs at every VAD pause. An optional **LLM incomplete-turn filter** makes the reply LLM emit a marker as its first character: `●` complete, `◐` cut off (wait 5 s), `○` thinking (wait 10 s). The reply is suppressed unless `●`. | Using the LLM you already have costs no extra latency. The completeness judgement rides on the reply call you were going to make anyway. |
| LiveKit Agents | Silero VAD, plus an open-weights text turn detector (Qwen2.5-0.5B, using conversation context). Endpointing defaults to min 0.5 s / max 3 s, with a **dynamic** mode that adapts to your pause habits. **Preemptive generation** starts the LLM on the provisional transcript and discards it if the transcript changes. False interruptions resume playback after 2 s. An adaptive model tells backchannels from barge-ins. | Speculate during the pause and cancel on resume. Learn each user's pause length. Resume after false interruptions. Their model's licence only allows use inside LiveKit Agents, so we can borrow the design but not the weights. |
| Deepgram Flux | Events: `EagerEndOfTurn` (prepare), `TurnResumed` (cancel), `EndOfTurn` (commit). | A clean state machine for speculate → cancel → commit, whatever recognizer you use. |
| Kyutai Unmute (open source) | A streaming STT with a semantic VAD head that predicts "finished or just pausing" from text and audio. | Confirms the pattern. Their models run on CUDA servers, not a Mac app. |

Sources: [OpenAI Realtime VAD](https://developers.openai.com/api/docs/guides/realtime-vad) ·
[GPT-Live prompting](https://developers.openai.com/api/docs/guides/live-prompting) ·
[Pipecat incomplete-turn filter](https://docs.pipecat.ai/server/utilities/turn-management/filter-incomplete-turns) ·
[Pipecat Smart Turn](https://docs.pipecat.ai/server/utilities/turn-detection/smart-turn-overview) ·
[LiveKit turn tuning](https://docs.livekit.io/agents/logic/turns/tuning/) ·
[LiveKit turn detector](https://docs.livekit.io/agents/logic/turns/turn-detector) ·
[Deepgram Flux eager EOT](https://developers.deepgram.com/docs/flux/voice-agent-eager-eot) ·
[Kyutai Unmute](https://kyutai.org/2025/05/22/unmute.html) ·
[TEN VAD](https://github.com/TEN-framework/ten-vad) ·
[Voice prompting (Deepgram)](https://developers.deepgram.com/docs/prompting-voice-agents)

Codex's own voice is a hosted full-duplex model (GPT-Live) in the desktop app. The CLI
uses the Realtime API. Neither is a local pipeline we can copy code from. The patterns
above are the transferable part.

## Recommended design

### 1. A voice front-man between you and the coding agent

Every finished utterance goes to a small **conversation agent**, whether the coding turn
is idle or busy. It uses your configured provider. Prefer the fastest model you have, since
it sits on the latency path. It does four things:

- **Turn completeness.** Its reply starts with `●` / `◐` / `○` (Pipecat pattern). For
  `◐`/`○` the reply is dropped and the mic keeps listening with a longer wait.
- **Answers and acknowledgements.** It handles status, small talk and "what did you
  change?" in the spoken style below.
- **Delegation.** It calls an explicit tool, `start_task(request)` or `steer(correction)`,
  with a **consolidated request** written from the whole exchange, never a raw fragment.
  This replaces `sendIdle`'s raw transcript and the busy-turn classifier with its
  `CLARIFY_TEXT`. The existing steering path (`TurnSteering`, receipts,
  `expected_turn_id`) stays as the safety boundary. It is good and matches Codex's
  `turn/steer`.
- **Narration.** When the coding turn ends, or a verified milestone arrives, it gets the
  final answer and the `run_summary`/`task_narrative`, and it **writes** what to say. The
  full answer stays on screen.

### 2. Patient endpointing, in layers

1. **Real VAD.** Swap the RMS gate for Silero VAD (ONNX, about 2 MB) or TEN VAD, with
   speech-probability hysteresis. Run it in the worklet or the native helper. This fixes
   T3 and gives every later stage clean pause boundaries.
2. **Fast acoustic and text scores at every pause of about 300 ms or more.** Use Smart
   Turn v3.2 (BSD-2, already wired once in the eval) for prosody, such as flat or rising
   intonation and a trailing "and…". Use the existing trailing-word heuristic as a weak
   signal.
3. **Adaptive wait instead of 2.5 s.** Roughly: confident complete → 0.7 s; unsure →
   2 s; trailing off or `◐`/`○` → up to **8 s** (OpenAI's low-eagerness cap). Learn the
   floor from this user's pauses within the session (LiveKit dynamic mode). You pause a
   lot, so the floor should move up within a minute.
4. **Speculate, then commit.** At a provisional end, start the front-man call
   (`EagerEndOfTurn`). If speech resumes, cancel it (`TurnResumed`) and keep accumulating.
   Only `●` with no resumption commits (`EndOfTurn`). The LLM's semantic judgement then
   adds no latency on the happy path.
5. **A grace window after commit.** If you resume within about 3 s and the front-man hasn't
   delegated yet, merge the continuation into the same request. Once it has delegated, the
   continuation goes through `steer`, phrased from the whole exchange.
6. **Never "say that again" because of a pause.** Delete the 4.5 s review fallback (T5)
   and the status-or-change question (T8). Keep listening, and show the live draft with a
   "still listening" state. Ask a question only when a specific detail is ambiguous, and
   ask about that detail ("host.ts or session.ts?").

### 3. Accumulating transcript (removes the 30 s cap)

Decode VAD-bounded chunks once, commit their text, and keep only the live tail for
partials. The request is the committed chunks plus the tail, with no length limit. Feed
the previous committed text and a **project vocabulary prompt** (file stems and symbols
from the index snapshot) as Whisper's `initial_prompt`. This fixes T4 and T11, and decode
cost stays flat as the request grows.

### 4. Speaking for the ear

Front-man prompt rules, from OpenAI's live prompting guide plus coding-specific ones:

- One to three short sentences. Lead with the outcome ("Done. Tests pass.").
- No Markdown, lists or parentheses. Structure through sentence flow.
- Name files by their role or stem ("the voice session file"). Say an exact identifier
  only once, and only when you need it. Never read paths, code, diffs or logs aloud. Say
  "the diff is on screen".
- Numbers and units in spoken form. Report only verified state, and don't guess while
  work runs.
- Progress during long turns: quiet by default. Speak one brief line at real milestones
  (a todo finished, an approval waiting, tests passed or failed), rate-limited.

Keep a **deterministic safety net** before TTS for anything that still leaks. Split
`snake_case`/`camelCase` into words instead of deleting `_` (N2). Reduce paths to their
stem. Drop table and list syntax. Replace a skipped code block with "there's a code
example on screen". Use sentence splitting that ignores list numbers and abbreviations
(N3). Replace the fixed receipt strings (N5) with short earcons (listening / got it /
thinking) plus front-man phrasing.

## Hosted options

- **OpenAI Realtime** (`semantic_vad`, eagerness low) does layers 1–4 of endpointing in
  one service and can be the front-man itself. It needs an **API key with metered
  billing**. Whether the ChatGPT-plan sign-in Crucible uses can reach the Realtime or
  GPT-Live endpoints is untested. Treat it as "probably not" until it's tried.
- **Deepgram Flux** and **AssemblyAI Universal-Streaming** provide hosted STT with built-in
  end-of-turn events. They would replace Whisper plus our endpointing, but your audio
  leaves the Mac.
- **Local** (recommended default): Silero VAD + Smart Turn v3.2 + Whisper with a
  vocabulary prompt + a front-man on your provider. Everything except the front-man stays
  on-device, and there's no new licence problem.

## Suggested order

1. Commit the Codex worktree to a branch.
2. Front-man with the `●/◐/○` marker, plus consolidated delegation (fixes T6–T8, N1, N5,
   N6). This is the biggest change you'll hear.
3. Adaptive wait with an 8 s cap and no review fallback (T1, T2, T5).
4. Narration safety net (N2–N4). Small and independent.
5. Silero VAD (T3), then the accumulating transcript and vocabulary prompt (T4, T11).
6. Speculative generation with cancel, a learned pause floor, and resuming after false
   interruptions (T10).

For evaluation, reuse Codex's harness (`scripts/eval/voice-endpoint-*.py`), but add
**your own recordings** with 3–5 s thinking pauses. The LiveKit corpus it used tops out
at 2 s edited pauses, which is why the replay passed 8/8 while live use doesn't. Score
early sends per mid-turn pause, repeat prompts, and the time from the true end of speech
to the start of the reply.
