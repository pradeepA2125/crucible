# Three-turn timing after the latency fixes — 11 October 2026

Same runner (`scripts/profile-chat-scenario.py`), same three prompts, same configuration as
`controller-three-turn-profile-2026-10-11.md`: ChatGPT plan, GPT-5.6-Luna, medium effort,
semantic retrieval, tool batching and adaptive reads on. Synchronous cProfile on, as before.

Code under test: commits `2e86d52`…`d73ac4d` plus the uncommitted batching work in the tree.
It ran on a second backend (port 8137) with the managed backend's environment and its own
chat/task storage, started about 30 s before turn 1, so its startup warm-ups had run but
its first semantic index build overlapped turn 1.

| Q | Final answer | | Provider requests | | Retrieval seed | | Calls issued / executed | |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| | before | after | before | after | before | after | before | after |
| 1 | 78.68 s | **30.46 s** | 2 (20.9 s) | 2 (15.5 s) | 57.13 s | **13.94 s** | 2/2 | 2/2 |
| 2 | 16.10 s | 81.76 s | 2 (15.8 s) | 9 (80.9 s) | 0.00 s | 0.00 s | 1/1 | 7/7 |
| 3 | 118.29 s | 150.10 s | 15 (117.0 s) | 18 (149.0 s) | 0.00 s | 0.00 s | 22/20 | 26/25 |

Cache hit rate 95–97% in both runs from the second request on.

## Reading the numbers

- **Q1 is the change the fixes target.** The seed fell from 57.1 s to 13.9 s. Both figures are
  cProfile-inflated; on Python 3.12 cProfile profiles every thread, so this one also includes
  the concurrent background index build. Unprofiled, the same seed measures 1.7 s with no build
  running and 3.9 s with one (GIL contention).
- **Q2 and Q3 are model-variance dominated.** Request cost was unchanged (about 8 s each:
  ≈ 2.35 s + 20 ms per output token, r = 0.98). The difference is the number of rounds the
  model chose: Q2 explored with 7 single searches/reads where the earlier run answered after
  one search. A single run per question cannot show the effect of the round-saving fixes.
- **Q3 behaviour, item by item:** the model escaped the regex itself (no retry round); it
  batched more (three 3-read rounds); one repeat search was blocked with the new refusal text;
  it still read `registry.py` as 1–330 then 331–532, so the line counts in search headers did
  not change that choice here.

## Next

- Measure round-saving changes over several runs per question, not one.
- Run the reasoning-effort A/B (low vs medium): output tokens drive request time.
- Builds in a subprocess would remove the GIL contention seen in Q1.
- `CodeChunker.source_paths` normalizes paths per node (80k `_make_relative` calls in the
  build); memoize per path as `_build_context` now does.

## Rerun after reverting the read footer (`b0b83ff`)

The footer added in `d73ac4d` appeared on 11 of 15 targeted reads in the run above, and the
model followed it: step 13 ("Confirm the remaining skill tool source…") and step 16 ("Verify
the remaining catalog routing section…") read the rest of files it did not need. It was
reverted; the footer again appears only when the harness cuts a read short.
Capture: `controller-three-turn-after-revert-2026-10-11.json`.

| Q | Original | With footer | After revert | Requests (orig / footer / revert) |
|---|---:|---:|---:|---|
| 1 | 78.68 s | 30.46 s | **28.39 s** | 2 / 2 / 2 |
| 2 | 16.10 s | 81.76 s | 57.79 s | 2 / 9 / 6 |
| 3 | 118.29 s | 150.10 s | **37.44 s** | 15 / 18 / 4 |

- **Q3:** 4 requests. Step 2 read four files in one batch, each to its exact end —
  `tools/registry.py` 1–532, `chat/todo_source.py` 1–84, `skills/tool_source.py` 1–84 —
  using the line counts that search results now give. No split read, no duplicate.
- **Q2:** still slower than the original; every step's thought is "verify the complete action
  set before answering" (targeted reads of `controller_prompts.py`). The original answered
  after one search that listed every `"description"` line. This is the model's exhaustiveness
  on an "all X" question, not a harness effect.
- One run per configuration: treat per-question differences as indicative, not measured.
