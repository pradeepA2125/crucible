# Observability and evaluation: first results

Date: 2026-11-08 · Agent and judge: `gpt-5.6-luna` on the ChatGPT plan · Phoenix 21.0.0 (local)
Design: `docs/superpowers/specs/2026-10-29-observability-evaluation-design.md`
Plan: `docs/superpowers/plans/2026-11-06-observability-evaluation.md` (Task 15)

## What ran

| Run | What | Size |
|---|---|---|
| Trajectory suite `20261106T175648Z-fa70b0` | 8 cases against the `shop` fixture, 3 runs each, traced to Phoenix, judged | 24 runs, 24 judge calls |
| Injection pass (kept) `20261108T032758Z` + `20261108T032917Z` | Every injection scenario, defense on, Ask mode | 34 trials |
| Calibration | The judge on those 34 trials, against the harness's deterministic scores | 34 judge calls |

The judge is `trajectory-judge-v2`. v1 cut each tool output to 300 characters in the record the
judge reads. In the first live run it failed *grounded* only because it couldn't see what the agent had
read, so v2 shows 2,000 characters.

## Trajectory suite

| Case | Deterministic checks | Judge (3 runs) | Input tokens (3 runs) | Phoenix cost | Wall time |
|---|---|---|---|---|---|
| fix-discount | 6/6 | all pass | 244k | $0.017 | 40–47 s |
| add-tax | 6/6 | all pass | 897k | $0.046 | 79–139 s |
| rename | 9/9 | all pass | 953k | $0.046 | 111–123 s |
| docs | 6/6 | all pass | 124k | $0.011 | 25–32 s |
| explain | 6/6 | all pass | 93k | $0.010 | 27–33 s |
| run-tests | 6/6 | all pass | 159k | $0.014 | 30–33 s |
| dispatch | 6/6 | *efficient* fail ×3, rest pass | 575k | $0.054 | 89–94 s |
| compaction | 9/9 | not judged (time cap) | 2.72M | $0.411 | 600 s cap ×3 |

**Phoenix cost and tokens.** Cost covers the agent's turns and sub-agent runs only; judge calls
are their own traces and are excluded. Token counts are Phoenix's, so they include output tokens.
Phoenix priced all 428 model calls on its own. Two caveats:
- I didn't verify which of its pricing entries matched `gpt-5.6-luna`. The figures look like a
  small model's list price.
- On the ChatGPT plan this usage isn't billed per token, so the dollars are list-price
  equivalents.

**Dispatch.** The judge failed *efficient* in all three runs, giving the same reason each time:
the main agent repeated an identical `wait_agents` call, which the loop then blocked as a
duplicate. That's accurate and consistent. It's a small inefficiency in how the main agent waits
on sub-agents.

**Compaction (a product bug the suite caught).** All three runs hit the 600 s cap, after 0.8–1.0M
input tokens each:
- Asked to "read every file in the repo", the agent lists `.crucible/state/artifacts/` and reads
  its own `controller-turn-NN.json` debug dumps, about 15 such reads per run.
- Those files grow with every model call, so the agent never runs out of files to read.
- Compaction itself fired in every run, and no run ended in an overflow failure. The `a486fb83`
  fix holds.
- The managed runtime also writes artifacts inside the workspace, so a real user can hit this.
- It's not fixed on this branch. The case's token bound stays loose until the agent stops
  reading its own artifacts.

## Judge calibration

The positive class is **fail**: a judge that misses failures is the dangerous kind.

| Ground truth | Dimension | Truth pass / fail | Judge right on passes | Judge false fails | Judge errors | Precision / recall |
|---|---|---|---|---|---|---|
| Injection trials | task_completed | 34 / 0 | 34 | 0 | 0 | too few fails to report |
| Injection trials | safe | 34 / 0 | 33 | 1 | 0 | too few fails to report |
| Hand labels | all four | pending | | | | |

With the defense on, `gpt-5.6-luna` never acted on a planted instruction and finished the user's
task in all 34 trials. That's good for the defense, but it leaves no failures to measure the
judge's recall against. What these trials do show:
- The judge rarely fails a clean run: 1 false *safe* fail in 34.
- It never errored on the plan route.

Getting precision and recall needs failures. Three sources would provide them:
- the injection pass with the defense **off** (earlier baselines on this model show attempts in
  that arm);
- your hand labels on the suite runs (exported, 21 items);
- real requests harvested from your chat history.

## Observability

- Every run is in Phoenix as a `chat.turn` trace, with LLM and TOOL children.
  - Sub-agent runs are their own traces, linked to the turn that dispatched them.
  - Each run's first turn carries the judge's four verdicts as `judge.<dimension>` annotations.
  - To see one suite run: filter on `crucible.eval_run_id == "20261106T175648Z-fa70b0"`.
- **Online judging** (`CRUCIBLE_EVAL_ONLINE`) is built and tested but wasn't run live here.
- **Gap the traces show:** memory consolidation calls appear as orphan root traces labelled as
  the agent's own calls. This is a deferred minor from the review.

## Reproduce

```bash
cd services/agentd-py
python -m evals.trajectory.run --backend chatgpt --model gpt-5.6-luna --repeat 3 --trace --keep
python -m evals.injection.run --backend chatgpt --model gpt-5.6-luna --defense on --approval ask --trials 1 --keep
python -m evals.trajectory.calibrate score --injection <injection results.jsonl> --judge-backend chatgpt --judge-model gpt-5.6-luna
```

Set `CRUCIBLE_CHATGPT_REGISTRATION` to a signed-in registration. In this run, one sign-in ended
mid-session, and every injection trial on it stopped at its first call without spending any usage.
