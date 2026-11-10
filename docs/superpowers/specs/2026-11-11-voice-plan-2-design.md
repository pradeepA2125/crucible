# Voice plan 2: interruptions, ChatGPT speed, voice in the chat, project words

Follows `docs/superpowers/plans/2026-11-09-voice-front-man.md` ("Out of scope (plan 2)") and
the live evidence in `docs/superpowers/evals/2026-11-10-voice-front-man-live.md`. Voice is the
desktop app only (macOS Apple Silicon); nothing here changes the VS Code extension except that
voice exchanges become visible in a thread's transcript, which VS Code also shows.

## Why

Plan 1 made turn-taking patient: only recognized words end a pause, Silero tells voice from
noise, the voice agent decides at each pause. Four gaps remain, in the user's priority order:

1. **Interrupting a reply is crude.** Today speech over playback stops the reply only once a
   transcript has two or more words (T10), a cough that does stop it never resumes it, and a
   natural interruption ("no, I meant the other file") is held for review unless it starts with
   a fixed phrase (T9, `clearOverlapRequest`).
2. **Voice is slow on the ChatGPT plan.** Voice uses the main model there (gpt-6.1-sol): live
   median 5.1 s per decision, against 1.4 s for Haiku on the Claude plan.
3. **Voice exchanges are invisible.** A spoken question and its spoken answer live only in an
   in-memory per-thread log in the backend (`VoiceAgent._logs`): not in the chat, lost on restart.
4. **Whisper mishears project words.** A fixed coding glossary was added in plan 1; the spec
   asked for words from the project itself (file and symbol names).

## Decisions the user made

- **Interruptions:** when the user's voice is heard over a reply, the reply pauses at once. If
  no words arrive (a cough, "hmm"), it resumes **from where it paused**. Once real words arrive,
  the reply stops for good and its full text stays on screen.
- **Voice in the chat:** spoken exchanges appear in the conversation, but **only requests that
  start or change work reach the coding agent's context**; small talk and status answers do not.
- **ChatGPT voice model:** voice may run on a smaller model than the main agent, picked
  automatically, with a setting to override it.
- **Speakers and headphones:** voice must work on the laptop's built-in speakers as well as
  headphones. On speakers the reply's own sound reaches the microphone, so goal 1's pause
  trigger has to be echo-aware (below).

## Goals

### 1. Pause, resume, or stop on interruption (T10, rest of T9)

- With the speech detector running, the user's voice over playback **pauses** playback
  immediately, without discarding any audio already received or still arriving.
- **Echo-aware trigger (required for built-in speakers).** While a reply plays, "voice" alone is
  not enough: the reply's own voice reaches the microphone and Silero scores it as speech. The
  app knows exactly what it is playing, so the trigger must account for it, for example by
  learning how much of the output reaches the microphone while the user is quiet and pausing
  only on speech clearly beyond that expected echo (a double-talk check). Targets: **no pause
  caused by the reply's own sound** across a live session on built-in speakers, and a pause
  within about 250 ms when the user speaks at normal volume, on speakers and on headphones.
  How it is done is left to the builder; the targets are not.
- The paused reply **resumes from the same point** if no new words arrive within a short window
  after the sound ends. "No new words" uses the existing rules: `addsWords`, sound labels and
  fillers don't count, and the reply's own text (now announced through `speaking` events) counts
  as echo, not words.
- Spoken playback commands ("stop", "stop talking", "wait", "pause", "hold on") are real words:
  they stop the reply for good, like any other words. There is no "go on" command. (Today
  `spokenMessage()` strips them as not-words; under this plan they must stop, not resume.)
- When words arrive, the reply **stops for good** (today's interrupt path: host cancels the
  reply, app stops output). Its text is already on screen.
- The words that stopped it form an ordinary turn: the voice agent decides it like any other
  (reply, clarify, start_task, steer). The overlap hold (`turn.held`, `clearOverlapRequest`) no
  longer applies when the detector is running; it stays as the loudness-fallback guard.
- Narration of a finished task and acknowledgements behave the same way (they are replies).
- Without the speech detector (loudness fallback), keep today's behavior.

### 2. A faster voice model on the ChatGPT plan

- The smaller model applies to **voice only**. The Auto approval judge keeps its current model
  choice; luna was measured on voice decisions only.
- Measured 2026-11-11, three voice decisions each at low effort, no history:
  gpt-6-luna median 2.8 s, gpt-6.1-terra 3.1 s, gpt-6-astra 3.1 s, gpt-6.1-sol 3.9 s; all
  decided correctly. Default the ChatGPT plan's voice model to **gpt-6-luna** (a catalog entry;
  fall back to the agent's model if the account's catalog lacks it or the provider refuses it,
  as the existing fallback does).
- A **Voice model** setting in the desktop app's voice settings: "Automatic" (the per-provider
  default) or any model from the provider's catalog. It sets what `CRUCIBLE_VOICE_MODEL` sets
  today, without an environment variable, and takes effect on the next decision (no restart).
  Stored **per provider**, so a choice made for the ChatGPT plan never applies on the Claude
  plan. A `CRUCIBLE_VOICE_MODEL` environment variable, when set, still wins (development
  override). Providers without a model catalog offer only "Automatic".
- Expectation set in the doc and UI copy: voice on the ChatGPT plan stays around 3 s; Haiku on
  the Claude plan stays the fast path.

### 3. Voice exchanges in the chat

- Every voice-only exchange (a `reply` or `clarify` the voice agent answered itself, and a
  correction's spoken acknowledgement) is written to the thread as visible messages: what was
  heard (user) and what was said (assistant), marked as voice.
- They render live during a turn, the way breadcrumbs do (persisted message plus a chat event),
  and survive reload and restart.
- **Not in the coding agent's context.** The coding agent's context is its own saved history
  (`controller_history`), not the visible transcript, and must stay that way for these
  messages. Requests that start work already reach it through `start_task` (the message is the
  user's words plus the voice reading); corrections through `enqueue_correction`.
- `voice_request_block()` stops appending the "Recent voice conversation" lines to a spoken
  task's goal: they carry small talk into the coding agent's context. The voice agent's own
  reading of the request (which it writes with the whole history in view) stays, and carries
  what "do what we just discussed" refers to.
- Other readers of the visible transcript must ignore voice messages, so saving them changes
  nothing else: the Auto approval judge (`build_judge_context` treats user messages as
  permission; a voice message, possibly misheard, must never authorize a command, as today
  when voice was never saved), the memory closing summary, the latest-user-message id and
  time, and the goal used when a gate card is claimed.
- A spoken narration of a finished task is not saved as a separate message: the written
  answer is already in the chat and in the voice agent's history.
- The voice panel shows only the current exchange and the live line; the conversation's
  history lives in the chat.
- The voice agent's own history reads these persisted messages instead of the in-memory log,
  so its memory of the conversation survives a backend restart. Remove the in-memory log if
  nothing else needs it.
- VS Code shows them too (same transcript); no voice controls there.

### 4. Project words for Whisper

- At voice session start, Whisper's prompt gains the open project's most relevant names: the
  project name, and file and symbol names taken from the index snapshot, ranked so the most
  referenced come first, kept within Whisper's prompt budget (about 224 tokens, shared with
  the existing glossary).
- Best effort: no index, or any failure, means today's glossary alone. The session never
  waits on it beyond a short timeout.

## Non-goals

- Smart Turn scoring and speculative reply generation: only if live tests still show early
  sends or slow replies after this plan.
- Spoken progress milestones ("tests are running").
- Linux and Windows support (a separate project).
- Making diagnostics content opt-in, and the plan-1 deferred minors (tracked in the evidence doc).

## Anti-goals

- A cough, a "hmm", a door or the assistant's own echo must never permanently stop a reply.
- Real words over a reply must never be ignored or sent to review when the detector is running.
- Voice small talk must never enter the coding agent's context or change its behavior.
- No new required install step, setting or environment variable for existing users.

## Constraints

- Test-first for every behavior, in the style of the existing voice tests
  (`voiceSession.test.ts`, `speechSegments.test.ts`, `voice-host.test.ts`,
  `tests/test_voice_agent.py`, `tests/test_voice_routes.py`).
- Every new state transition is traced in the diagnostics file, as plan 1's events are.
- Project UI rules: `docs/design/desktop-2026-10-21/react-ui-guidelines.md`; check Dark/Light
  and wide/narrow for any new or changed UI (the voice setting, voice messages in the chat).
- Backend changes stay behind the existing voice routes and controller seams; no change to the
  coding agent's history format.

## Calls made by the designer (not asked of the user)

Settled from the user's stated rules or by keeping today's behavior; check these when reading:

1. Spoken task goals lose the "Recent voice conversation" lines (the user's rule: only work
   requests reach the coding agent).
2. Voice messages never count as user permission for the Auto approval judge (today's behavior).
3. Spoken playback commands stop the reply for good; no resume command.
4. The ChatGPT voice default (gpt-6-luna) applies to voice only, not the approval judge.
5. The voice model setting is per provider; the env var still wins.
6. Narration is not saved as a message; the voice panel stops keeping its own history.

## Left to the builder

- How playback pauses and resumes (for example a separate output audio context that can be
  suspended without stopping microphone capture, since capture and playback share one today).
- The resume window length (start near 1 s after the sound ends, tune from diagnostics).
- How voice messages are marked and styled in the transcript, within the UI guide.
- How the setting reaches the backend (a config route or the existing provider settings path).
- The ranking and exact budget for project words, and how they reach the native helper (a
  start-up argument or a per-session prompt request).

## Verification

- Unit tests for each goal, including: cough over a reply resumes from the same audio
  position; echo of the reply resumes; real words stop it and are decided; a status answer
  appears in the chat live and is absent from the next coding turn's context; a voice model
  setting change is used by the next decision; vocabulary falls back to the glossary on any
  failure.
- A live session with the user covering each goal, recorded in an evidence doc like
  `2026-11-10-voice-front-man-live.md`.
