# Voice plan 2: live evidence (2026-11-11)

Branch `feat/voice-plan-2`. Desktop app built from the worktree (Electron, macOS Apple Silicon),
workspace `pradeep-site`, ChatGPT plan (agent model gpt-6.1-sol; voice on the automatic choice,
gpt-6-luna). One speaker. Evidence comes from the app's diagnostics file
(`~/Library/Application Support/Crucible/voice/diagnostics.jsonl`) and the backend log.
The session ran 07:09–07:28 UTC; fixes found during it went in at 07:21.

The user ended the session ("done with feature") before every planned scenario was run; the
gaps are listed below rather than filled in.

## What the session measured

| Measure | Before 07:21 fixes | After |
|---|---|---|
| Voice-agent decisions (stale: superseded by more speech) | 17 (4 stale) | 13 (4 stale) |
| Failed decisions | 4 (3 timed out at 8 s) | 0 |
| Decision latency, gpt-6-luna | median 6.5 s · p90 7.3 s | median 5.4 s · p90 6.3 s |
| Speech onsets / over a playing reply | 16 / 3 | 8 / 2 |
| Replies paused for the user, then stopped by words | 2 | 2 |
| Pauses that resumed (no words) | 0 | 0 |

Words that stopped a reply: "No, the", "DuskSceneHandle, the one which", "Thank you.", "Stop."
(a playback word). No pause was caused by the reply's own voice, and no reply stopped without
the user's words.

Replay at 12:49 local (three decisions each, same utterance): gpt-6-luna 3.5–5.0 s with or
without this thread's saved history (1,160 characters, so history is not the cost);
gpt-6.1-sol 4.3–5.6 s; gpt-6.1-terra refused both calls. Luna remains the right default, but
the ChatGPT plan ran slower than the morning benchmark (2.8 s median).

## Confirmed live

- Project words: the backend served `pradeep-site`'s own names (`DuskSceneHandle`, `useReveal`,
  `HeroScene`, …) and Whisper wrote "DuskSceneHandle" as one identifier in a live utterance.
- Voice exchanges saved in the chat ("Spoken" caption, "Spoken reply" lines) and still there
  after two app restarts.
- Real words over a reply stop it for good and are decided; "Stop." stops it.
- The voice model runs on gpt-6-luna automatically on the ChatGPT plan.

## Found live, and fixed

| Seen live | Cause | Fix |
|---|---|---|
| Three turns failed: "the voice model did not answer in 8s" | gpt-6-luna took 5.1–7.7 s live | Decide limit 12 s (host gives up at 15 s) (`3f305007`) |
| That error stayed on the panel after later turns succeeded | Nothing cleared it on the spoken path | A committed spoken turn clears it (`ccd92f4f`) |
| The voice panel repeated what the chat now shows | Both rendered the saved exchanges | User decision: the chat is the record; the panel shows only unsettled turns (in flight, or a correction queued or expired) (`5dfaade5`) |
| Dev launch: "runtime update required (indexer)" | `~/.crucible/runtime`'s indexer (Oct 19) lacks `owner=1` | Launched with `CRUCIBLE_DEV_RUNTIME` = the desktop app's own runtime; `~/.crucible/runtime` still needs its indexer updated (separate from this plan) |

## Not exercised live

- A cough, "hmm" or door over a reply (pause then resume): unit-tested only
  (`a cough over a reply pauses it and resumes from the same point`,
  `a cough Whisper hears as "you" over a reply resumes it, never stops it`).
- A long reply on built-in speakers with no interruption (zero self-pauses target), and the
  echo gate's new reference/coupling numbers: the `levels` trace now carries `outputPeak` and
  `coupling` for the next session to tune from.
- Headphones.
- A status question while a task streams (live push without sealing the answer).
- "Do it" after a spoken discussion, and a bare "go ahead".
- The Voice model setting changed in the panel.
- Dark/Light and wide/narrow review of the new UI (voice-model select, "Spoken" caption,
  SpokenLine).
