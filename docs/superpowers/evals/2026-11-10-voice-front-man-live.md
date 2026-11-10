# Voice front-man: live evidence (2026-11-10)

Branch `feat/live-voice`. Desktop app (Electron, macOS Apple Silicon), workspace `pradeep-site`,
one speaker, a normal room with outside noise (traffic, birds, clicks). Evidence comes from the
app's diagnostics file (`~/Library/Application Support/Crucible/voice/diagnostics.jsonl`) and the
backend log, both added on this branch for this purpose.

## What the live sessions measured

Sessions after the Silero detector shipped (07:47–17:09 UTC):

| Measure | Value |
|---|---|
| Voice-agent decisions | 57 (13 dropped as stale because the speaker continued, as designed) |
| Turn verdicts | complete 36 · thinking 17 · cut_off 4 |
| Actions | reply 25 · listen 19 · start_task 7 · clarify 6 |
| Decision latency, Claude plan (Haiku 5.5) | median 1.4 s · p90 2.0 s (n=59) |
| Decision latency, ChatGPT plan (main model gpt-6.1-sol) | median 5.1 s · p90 6.0 s (n=25) |
| Quiet before the app acted on a decision | median 3.5 s · p90 6.5 s (n=31; includes the 2 s hold before starting a task) |
| Pauses ended by recognized new words | 20 (no pause ended by sound alone) |
| 8 s wait-limit check-ins | 9 |
| Speech detector fallbacks to loudness | 0 |
| TTS synthesis time | median 1.7 s (n=43) |
| Narration fallbacks (first sentences read instead of a summary) | 3 |

No early send was observed after the words-only rule shipped: every mid-thought pause was judged
`thinking` and held, and only new words resumed the turn.

## Failures found live, and what fixed them

Each was diagnosed from the logs above before changing code; commit messages carry the detail.

| Seen live | Cause | Fix |
|---|---|---|
| Voice decisions timed out (8 s) on Sonnet; prose instead of an action | Main model used for voice; Sonnet refuses a forced tool | Per-provider small model (Haiku on the Claude plan), forced tool where accepted, one correction turn (`f9aeb4f5`, `d885e805`) |
| Turn stuck on "Hearing you" for 3 minutes; 16 decisions discarded | One frame of room noise (0.0054–0.011 vs a 0.0053 bar) counted as speech resuming | Words-only resume, sustained-run and in-turn noise floor (`3b20e93c`, `730a5207`) |
| Noise in a pause delayed or blocked replies | Loudness cannot tell voice from noise | Silero VAD v6.2.0 via whisper.cpp (`834d7333`, `a86d98b5`, `f145ea11`); measured offline: noise ≤ 0.083, speech mean 0.80 |
| "repo" heard as "report", "ripple", "rapport" | Whisper vocabulary | Coding glossary prompt (`65501cfb`) — unproven on the live speaker (synthetic speech already transcribed "repo") |
| Every narration failed on the Claude plan | `on_retry` passed through the rate-limit wrapper to a `generate_text` that rejects it | Check hooks on the wrapped transport (`2cc4453c`) |
| Follow-up "explain the issues on screen" got "Should I start a new task?" | Voice agent chose steer while idle; no latest answer in context | Project name, latest answer, idle rules (`6744c321`) |
| Coding turn failed after 4 retries | Sonnet batched 12 tool calls against `maxItems: 8`; correction never said so | Schema errors name the violation; replay: fixed on the next try 3/3 (`3e157227`) |
| Request after "Awesome." over a check-in went to review | Overlap hold lasted the whole turn | Decide after 1 s of clear speech (`a7bd4c2f`) |
| Generic check-in despite the prompt rule | Haiku returned an empty say | Final-call instruction in the payload; 6/6 specific questions on the live sentence (`33a347ab`) |
| Status question during a task cancelled its narration | Each reply aborted the previous one, the task's included | Task reply kept apart from speech (`ff3462f8`) — tested, not yet confirmed live |

## Plan Task 7 coverage

| Step | Status |
|---|---|
| 1. Seven live scenarios × 3 | Partly. Thinking pauses, continuations, status questions during a task, a task start and narration were exercised many times in real use; corrections during a turn, "mm-hmm" during playback and a 40 s request were not run as scripted trials. |
| 2. Replay corpus (`scripts/eval/voice-endpoint-corpus.py`) | Not run on this branch. |
| 3. Both themes and widths for the new states | Not checked. |

## Open items

- Voice decisions on the ChatGPT plan take ~5 s because voice uses the main model; a smaller
  ChatGPT model is not verified yet.
- The slow-decision cue fired on 46 of 57 decisions: at today's latencies it plays most of the
  time, so it may be more noise than signal.
- A reply that seemed to stop on a thump is unexplained; playback stop reasons are now traced
  (`b44b2bdd`) for the next occurrence.
- Whisper glossary effect on the live speaker, the narration fix, and the overlap-release fix
  need a live confirmation.
- Voice is macOS Apple Silicon only; Linux and Windows are a separate project.
