# Codex cold-resume behavior versus Crucible

Research date: October 16, 2026. Read-only investigation of official OpenAI
documentation and existing local telemetry; no live inference experiments.

## Finding

Local Codex telemetry supports the user's observation that a large, genuinely
mostly uncached resumed request need not exhaust a five-hour allowance. The
native harness then returns to high cache reuse and successfully compacts its
active context. Crucible's captured continuation repeatedly invalidated its
prefix and failed compaction instead.

This establishes a major efficiency difference. It does not establish the
precise subscription-percentage accounting of native Codex versus the direct
Sign in with ChatGPT route, or independently attribute the entire observed
Crucible account exhaustion to those nine responses.

## Measured native Codex example

Source: local rollout
`/Users/pradeepkumar/.codex/sessions/2026/10/14/rollout-2026-10-14T14-48-38-01a139b4-e284-7370-85c1-c5b1a2f2b459.jsonl`.
Only token counts, event types, model metadata, and rate-limit snapshots were
extracted. Conversation content was not copied.

The thread resumed on October 14 at 19:20 IST after approximately four hours
without a new recorded model response. Its model was `gpt-6.1-sol`.

| Response | Provider input | Provider cached input | Uncached input |
| --- | ---: | ---: | ---: |
| First resumed response | 328,373 | 22,400 | 305,973 |
| Next response | 332,247 | 328,192 | 4,055 |
| Following response | 342,976 | 332,032 | 10,944 |

The pre-idle five-hour snapshot was 19% used (line 349). The first resumed
response's snapshot was 20% (line 372), and the following response's was 21%
(line 380), with the same reset timestamp. These are rounded, account-wide
snapshots; they are not a precise per-response invoice and may lag or include
other sessions. In particular, do not describe the first request as definitively
costing exactly one percentage point.

Over the resumed segment from 19:20:49 through 19:34:52 IST:

- 27 recorded provider responses, including compaction.
- 9,399,121 total input tokens; 8,984,448 cached; 414,673 uncached.
- 21,300 output tokens, including 5,740 reasoning tokens.
- Five-hour snapshot at segment end: 38% used, versus the pre-idle 19%.

At 19:34:27 IST, line 540 records successful native compaction. Its replacement
history contains a `compaction` item with `encrypted_content`. Subsequent request
input falls from approximately 416k to 37.7k. This is persisted active model
context, distinct from the full visible conversation transcript.

For comparison, Crucible's nine completed responses had 2,569,380 uncached input
tokens and 25,427 output tokens. That is about **6.2 times the uncached input** of
the measured 27-response Codex segment. It is not a 6.2x subscription-usage
multiplier: cached input, output, plan terms, and account-wide activity also matter.

## Documented behavior

1. **Preserve the prefix.** OpenAI describes Codex as keeping instructions, tools,
   sandbox settings, and environment context stable and consistently ordered.
   Runtime changes are appended as new messages instead of rewriting old ones.
   This allows the next loop iteration to reuse the previous prompt prefix.
   [OpenAI Prompt Caching 201](https://developers.openai.com/cookbook/examples/prompt_caching_201).

2. **Idle does not prove cold.** Current GPT-5.6-and-later API documentation gives
   a minimum cache lifetime of 30 minutes after the latest write/reuse, and says
   entries may remain longer. Cache lifetime is model-dependent. The example
   above independently verifies a mostly cold prompt using reported cached
   tokens, rather than inferring coldness from idle time.
   [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

3. **Persist thread state independently of cache.** Saved thread history and
   `thread/resume` work with `store:false`; thread persistence does not mean the
   provider's KV cache is still warm. Auto-compaction thresholds are configurable,
   with model defaults when unspecified.
   [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations),
   [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

4. **Guard background memory work.** The current Codex configuration reference
   lists `memories.min_rate_limit_remaining_percent`, default 25: memory generation
   requires a minimum remaining percentage in its rate-limit windows. This is a
   documented guard, not proof of which memory features were enabled locally.
   [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

5. **Do not translate API prices directly into included quota.** OpenAI's Codex
   pricing page explicitly says credit prices alone do not determine included
   subscription usage. It also distinguishes Codex credit billing (no separate
   cache-write charge) from API-key billing. That distinction does not prove that
   direct OAuth plan requests use API-key billing: Crucible uses the user's plan.
   No verified source found in this investigation promises identical five-hour
   percentage accounting between native Codex and direct OAuth-plan requests.
   [Codex pricing](https://learn.chatgpt.com/docs/pricing).

## Implication for Crucible

The prior cache-session and failed-compaction fixes address concrete defects,
but a session header alone cannot preserve cache when old prompt items change.
The captured image-budget tombstone rewrite and failed-compaction prefix changes
must remain part of the diagnosis. Future comparisons should record before/after
provider allowance snapshots, safe account identity, model/settings, cached input,
output, and compaction outcome. Do not spend another fresh allowance merely to
reproduce the problem before those observations and local request tests exist.
