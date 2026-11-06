# Tracing Crucible in Phoenix

Crucible can export every chat turn to [Phoenix](https://github.com/Arize-ai/phoenix) as
OpenTelemetry traces in the OpenInference conventions: the turn, each model call with its
tokens and latency, each tool call, and every sub-agent and team member. Phoenix computes cost
from the token counts. Design: `docs/superpowers/specs/2026-10-29-observability-evaluation-design.md`.

Tracing is **off by default**. With it off, Crucible loads none of the tracing packages and
behaves exactly as before.

## Run Phoenix

Phoenix runs as its own process, never inside agentd. Verified with Phoenix 21.0.0:

```bash
uv venv -p 3.12 .tmp/phoenix-venv
uv pip install -p .tmp/phoenix-venv/bin/python 'arize-phoenix==21.0.0'
PHOENIX_WORKING_DIR="$PWD/.tmp/phoenix-data" .tmp/phoenix-venv/bin/phoenix serve
```

The UI is at http://localhost:6006.

## Turn tracing on

Install the tracing packages into the backend's environment:

```bash
cd services/agentd-py && pip install -e '.[telemetry]'
```

Then start the backend with `CRUCIBLE_TRACING_ENABLED=1`, for example through
`scripts/stress/start-backend.sh`, which passes your shell's environment through. The managed
runtime the extension installs doesn't include the tracing packages. To trace it, install them
by hand into its venv (`uv pip install -p ~/.crucible/runtime/venv/bin/python
'crucible-agentd[telemetry]'`) and set the variables where the backend is started.

| Variable | Default | What it does |
|---|---|---|
| `CRUCIBLE_TRACING_ENABLED` | off | Export traces. If the packages are missing, the backend logs one warning and keeps tracing off. |
| `CRUCIBLE_TRACING_ENDPOINT` | `http://localhost:6006/v1/traces` | The OTLP/HTTP endpoint. |
| `CRUCIBLE_TRACING_PROJECT` | `crucible` | The Phoenix project the traces land in. |
| `CRUCIBLE_TRACING_CONTENT` | on for a loopback endpoint, off otherwise | Include messages, tool arguments and outputs. An explicit value always wins. |

**Content.** With content off, spans keep names, timings, token counts, tool names and error
status, and nothing else. With content on, each captured text is capped at 4,000 characters.
In tool arguments, secret-named keys, `NAME=value` assignments with secret names, and
credentials in URLs are replaced with `[redacted]`.

## What a trace shows

- **`chat.turn`** (AGENT): one per turn, grouped into a Phoenix session per thread.
  - Attributes:
    - `crucible.turn_kind`: `user`, `notice` or `continuation`.
    - `crucible.request_id`: the user message that started the request, shared by its notice
      turns and continuations, so a filter on it shows one request's whole story.
  - A continuation (an answered clarify or mode card) is its own trace, linked to its user turn.
  - A stopped turn ends with error status `cancelled`.
- **`llm <schema>`** (LLM): every provider call.
  - `llm.model_name`, `llm.provider` and the token counts (including cache reads) that Phoenix
    prices.
  - `crucible.queue_wait_ms` is the time spent waiting on the rate limiter, which the span's
    duration also includes.
  - `crucible.call_purpose` is `agent` for the agent's own reasoning. Otherwise it names a
    call Crucible makes for itself: `auto_judge`, `memory_curator`, `memory_summary` or
    `eval_judge`.
- **`<tool name>`** (TOOL): every tool call that ran, with its arguments and output. A failed
  tool ends with error status.
- **`subagent:<label>`** and **`team:<team>/<member>`** (AGENT): each agent activation is the
  root of its own trace, linked to the turn that started it. Agents outlive turns, so they are
  never children of a turn span.

**Cost on plan backends.** For the ChatGPT plan (`chatgpt`) and the Claude subscription
(`claude_subscription`), spans carry `crucible.billing=plan`. Phoenix's figure is then a
list-price equivalent, not a bill.

A scripted turn checked on 2026-11-06: Phoenix built the turn → model call → tool → model call
tree, stored the span kinds, and priced 4,040 `gpt-4o` tokens at $0.0104.
