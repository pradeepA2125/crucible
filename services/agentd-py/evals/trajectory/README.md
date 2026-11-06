# Trajectory eval suite

A small fixed set of real-user requests runs through Crucible's real chat controller against a
fixture repo, `fixtures/shop`. Each finished request is checked deterministically and graded by
an LLM judge on four pass/fail dimensions: `task_completed`, `grounded`, `efficient` and
`safe`. The design is in `docs/superpowers/specs/2026-10-29-observability-evaluation-design.md`,
§6.3.

## Running it

From `services/agentd-py` with the dev venv:

```bash
python -m evals.trajectory.run --backend chatgpt --model gpt-5.6-luna --repeat 3 --trace
```

Flags:
- `--case ID` (repeatable) runs a subset.
- `--judge-backend` / `--judge-model` pick the judge. The default is the agent's own provider.
- `--trace` exports every run to a local Phoenix (see `docs/observability.md`) and posts the
  judge's verdicts onto each run's first turn span as `judge.<dimension>` annotations.
  - If Phoenix is down, the run still completes and writes its results. The annotations are
    skipped with a warning.
- `--keep` keeps each run's temp folder: the workspace, the chat database and the turn traces.

**Live runs spend provider or ChatGPT plan usage, for both the agent and the judge.** The
command prints the run count and asks before starting; `--yes` skips the question.

Provider settings come from the same environment variables the backend uses. Every other
`CRUCIBLE_*` variable is ignored, as in the injection harness, so a run never touches your real
databases or settings.

Results land in `.tmp/trajectory-evals/<UTC timestamp>/`:
- `results.jsonl`: one line per run, with checks, verdicts and reasons, the judge version,
  the trace id, usage and wall time.
- `summary.md`.

## What happens in a run

1. The fixture is copied to a temp folder, and `HOME` points at a temp folder too.
2. The real chat controller is built there, and the case's prompt is sent.
3. Cards are answered by a careful user:
   - commands that match the case's `allow_commands` are approved;
   - edits are approved (the `edits_only_in` check catches stray ones);
   - MCP calls and sandbox escalations are rejected;
   - a clarify question gets "Just do what I originally asked."
4. The run waits for the whole episode, not just the first turn: notice turns, sub-agents and
   teams all have to finish. It then runs the case's checks and judges the episode.

Allowed commands really run, even after the agent edited the fixture. Grading the agent's fix
needs its test run, and that run executes code the agent wrote. It runs inside the command
sandbox when the sandbox is available.

## Cases

`cases/*.yaml`, one file per case:

| Field | What it holds |
|---|---|
| `id` | The case's name. |
| `prompt` | The user's one-line request. |
| `workspace` | A folder under `fixtures/`. |
| `approval` | `ask` or `full` (Full access). |
| `flags` | Environment variables the case needs, for example `CRUCIBLE_SUBAGENTS_ENABLED`. |
| `allow_commands` | fnmatch globs for the commands the careful user approves. |
| `checks` | The deterministic checks (kinds below). |
| `expected` | Rubric verdicts, only where they're known in advance. |
| `max_seconds` | The time cap for the run. |

Check kinds: `file_contains`, `file_exists`, `command_passes`, `edits_only_in`, `event_fired`,
`answer_mentions`, `input_tokens_below`, `no_failure_message`.
