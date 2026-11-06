# Observability and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export Crucible's chat turns to Phoenix as OpenTelemetry traces with cost and latency, grade finished requests with an LLM judge (offline suite and sampled online), and measure the judge's precision and recall against ground truth.

**Architecture:** A new `agentd/telemetry/` package owns tracing behind our own context variables, so nothing imports OpenTelemetry while tracing is off; spans open at four seams (the rate-limited transport, the controller turn, the tool batch, and an agent activation). The episode builder and judge ship in `agentd/eval/trajectory/` because online evaluation runs inside the backend; the offline runner, cases and calibration live in `evals/trajectory/` and run real turns in-process on a trial world shared with the injection harness (`evals/common/`).

**Tech Stack:** Python 3.11+, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `openinference-semantic-conventions`, `httpx` (Phoenix REST annotations), pytest + pytest-asyncio, a local Phoenix server (separate process).

**Spec:** `docs/superpowers/specs/2026-10-29-observability-evaluation-design.md` (revised 2026-11-06)

## Global Constraints

- Every new flag defaults OFF: `CRUCIBLE_TRACING_ENABLED`, `CRUCIBLE_EVAL_ONLINE`. With tracing off, no `opentelemetry*` or `openinference*` module is imported, and behavior is byte-identical.
- `CRUCIBLE_TRACING_ENDPOINT` default `http://localhost:6006/v1/traces`; `CRUCIBLE_TRACING_PROJECT` default `crucible`; `CRUCIBLE_EVAL_ONLINE_SAMPLE` default `0.1`.
- `CRUCIBLE_TRACING_CONTENT`: default ON when the endpoint host is loopback, OFF otherwise; an explicit value always wins. Content off = names, timings, token counts, tool names and error status only.
- Captured text is capped at 4000 chars per attribute (`TEXT_CAP`), with a truncation marker.
- Tracing never slows or breaks a turn: every exporter or attribute error is swallowed and logged at DEBUG, like the memory harness.
- Tracing enabled without the `[telemetry]` extra installed → exactly one startup WARNING, tracing off.
- Provider keys, auth headers and secret-named values are never recorded, with content on or off.
- No Crucible-side price table. Plan-billed backends (`chatgpt`, `claude_subscription`) carry `crucible.billing=plan`.
- Judge verdicts are pass/fail per dimension with a one-sentence reason; the prompt and schema carry `JUDGE_VERSION`, stored with every score.
- Judge calls record usage under owner `eval:<run_id>`, never a thread's owner.
- Every new `generate_json` schema is added to `tests/test_openai_strict_schema.py::_every_schema_we_send`.
- No new GET route (so `tests/test_get_routes_read_only.py` is untouched); nothing is added to `/live`.
- A live eval run prints the trial count and asks before spending provider or plan usage; `--yes` skips the question (the injection harness's rule).
- The injection harness's behavior and its existing tests stay unchanged by the `evals/common/` extraction.
- Strict typing, explicit errors, no `any`-style escapes in new code; `ruff check .` and `mypy agentd` stay clean.

## Review Focus

1. Phoenix not running (connection refused) during a turn and at shutdown → the turn finishes normally and shutdown takes at most the flush timeout (2 s). Test in Task 1.
2. A secret in tool args with content ON (`run_command` with `GITHUB_TOKEN=…`, an MCP call with an `authorization` arg) → the value is replaced by `[redacted]` on the span. Test in Task 4.
3. A turn stopped with `/stop` mid-model-call → the LLM and turn spans end with error status `cancelled`, and an agent the turn dispatched is the root of its own trace, never a child of the ended turn span. Tests in Tasks 3 and 5.
4. The judge's provider raises, refuses, or returns an unparseable verdict → the episode records `judge_error`, is never counted as a pass, and is excluded from metrics with the exclusion counted. Test in Task 10.
5. A label file edited after its scores were first read → `calibrate score` refuses with a message naming the file. Test in Task 13.

---

## File Structure

```
services/agentd-py/
  pyproject.toml                         modify: [telemetry] extra; same packages in [dev]
  agentd/telemetry/__init__.py           create: empty package marker
  agentd/telemetry/config.py             create: TracingConfig, loopback rule, TEXT_CAP
  agentd/telemetry/tracer.py             create: provider/exporter lifecycle, test exporter
  agentd/telemetry/context.py            create: CALL_PURPOSE, EVAL_TAGS, turn-link registry (no OTel import)
  agentd/telemetry/spans.py              create: llm_span, note_usage, turn_span, tool_span, agent_span
  agentd/telemetry/redact.py             create: redact_args, cap_text
  agentd/providers/usage.py              modify: UsageMeter.record also calls note_usage
  agentd/providers/rate_limit.py         modify: LLM span around admit+call; backend kwarg
  agentd/providers/factory.py            modify: pass backend to RateLimitedTransport
  agentd/approvals/judge.py              modify: CALL_PURPOSE=auto_judge
  agentd/memory/curator.py               modify: CALL_PURPOSE=memory_curator
  agentd/memory/harness.py               modify: CALL_PURPOSE=memory_summary in the summarizer
  agentd/sandbox/env.py                  modify: public is_secret_env(name, value)
  agentd/chat/controller.py              modify: turn span in _run_loop, continuation flag,
                                                 latest_user_message_id, episode_settled,
                                                 _activate → agent_span, online-eval hook
  agentd/chat/controller_loop.py         modify: tool_span around checked_run
  agentd/main.py                         modify: init/shutdown tracing; online evaluator wiring
  agentd/eval/trajectory/__init__.py     create
  agentd/eval/trajectory/episode.py      create: Episode, TurnRecord, build_episode, render_for_judge
  agentd/eval/trajectory/judge.py        create: rubric, schema, judge_episode
  agentd/eval/trajectory/annotations.py  create: Phoenix REST span annotations
  agentd/eval/trajectory/online.py       create: OnlineEvaluator
  evals/common/__init__.py               create
  evals/common/environment.py            create: isolated_environment, applied_environment
  evals/common/commands.py               create: CommandRecord, CommandRecorder, command_may_run
  evals/common/world.py                  create: ActionRecorder, build/start/stop controller, converse
  evals/common/cards.py                  create: CardPolicy protocol, CardAnswerer
  evals/injection/{isolation,answerer,trial}.py  modify: thin wrappers over evals/common
  evals/trajectory/__init__.py           create
  evals/trajectory/cases.py              create: Case, load_cases
  evals/trajectory/cases/*.yaml          create: 8 cases
  evals/trajectory/fixtures/shop/        create: the fixture workspace
  evals/trajectory/checks.py             create: deterministic checks
  evals/trajectory/runner.py             create: run_case
  evals/trajectory/run.py                create: CLI
  evals/trajectory/calibration.py        create: metrics, ground truth, label export, frozen labels
  evals/trajectory/calibrate.py          create: CLI
  evals/trajectory/README.md             create
  tests/test_telemetry_*.py, tests/test_trajectory_*.py, tests/test_eval_common_*.py   create
docs/observability.md                     create: running Phoenix, flags, what a trace shows
docs/superpowers/evals/2026-11-XX-observability-evaluation-results.md   create in Task 15
```

---

## Phase 1 — Tracing

### Task 1: Telemetry config, tracer lifecycle and app wiring

**Files:**
- Create: `agentd/telemetry/__init__.py`, `agentd/telemetry/config.py`, `agentd/telemetry/tracer.py`
- Modify: `services/agentd-py/pyproject.toml`, `agentd/main.py`
- Test: `tests/test_telemetry_config.py`, `tests/test_telemetry_tracer.py`

**Interfaces:**
- Produces:
  - `TEXT_CAP: int = 4000`
  - `@dataclass(frozen=True) class TracingConfig: enabled: bool; endpoint: str; project: str; capture_content: bool` with `@classmethod from_env(cls, env: Mapping[str, str] | None = None) -> TracingConfig`
  - `is_loopback_endpoint(url: str) -> bool` — host is `localhost`, `127.0.0.0/8` or `::1`
  - `init_tracing(config: TracingConfig, *, version: str, backend: str) -> bool` — True when spans will export
  - `is_active() -> bool`, `capture_content() -> bool`, `flush(timeout_sec: float = 5.0) -> bool`, `async shutdown_tracing(timeout_sec: float = 2.0) -> None`
  - `install_test_exporter(*, capture_content: bool = True) -> InMemorySpanExporter` (SimpleSpanProcessor, replaces any earlier install) and `reset_for_tests() -> None`
  - `get_tracer() -> opentelemetry.trace.Tracer | None` (None when inactive)

- [ ] **Step 1: Add the packages.** `[telemetry]` extra and the `[dev]` extra both get `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `openinference-semantic-conventions`, each with a `>=` floor at the latest release on PyPI on the day of implementation (look it up; record the versions in the commit message). Install into the dev venv: `pip install -e '.[dev]'`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_telemetry_config.py
def test_defaults_are_off_with_phoenix_endpoint():
    c = TracingConfig.from_env({})
    assert (c.enabled, c.endpoint, c.project) == (False, "http://localhost:6006/v1/traces", "crucible")

def test_content_defaults_on_for_loopback_and_off_for_remote():
    assert TracingConfig.from_env({"CRUCIBLE_TRACING_ENABLED": "1"}).capture_content is True
    assert TracingConfig.from_env({"CRUCIBLE_TRACING_ENABLED": "1",
        "CRUCIBLE_TRACING_ENDPOINT": "https://phoenix.example.com/v1/traces"}).capture_content is False

def test_explicit_content_value_wins():
    assert TracingConfig.from_env({"CRUCIBLE_TRACING_ENDPOINT": "https://x.example/v1/traces",
                                   "CRUCIBLE_TRACING_CONTENT": "1"}).capture_content is True
    assert TracingConfig.from_env({"CRUCIBLE_TRACING_CONTENT": "off"}).capture_content is False

@pytest.mark.parametrize("url,expected", [("http://127.0.0.1:6006/v1/traces", True),
    ("http://[::1]:6006/v1/traces", True), ("http://localhost.evil.com/v1/traces", False)])
def test_loopback(url, expected): assert is_loopback_endpoint(url) is expected
```

```python
# tests/test_telemetry_tracer.py
def test_tracing_off_imports_no_opentelemetry():
    # subprocess: `import agentd.main` with CRUCIBLE_TRACING_ENABLED unset, then
    # assert not any(m.startswith(("opentelemetry", "openinference")) for m in sys.modules)

def test_missing_packages_warns_once_and_stays_off(monkeypatch, caplog):
    # monkeypatch builtins.__import__ to raise ImportError for "opentelemetry*";
    # init_tracing(enabled config) is False, is_active() is False, exactly one WARNING
    # whose text names "pip install 'crucible-agentd[telemetry]'"

async def test_unreachable_endpoint_never_blocks_shutdown():
    # init_tracing with endpoint http://127.0.0.1:<closed port>/v1/traces, open and end
    # one span, then `await shutdown_tracing(timeout_sec=2.0)` finishes in < 2.5 s and raises nothing
```

- [ ] **Step 3: Run them and watch them fail**

Run: `pytest tests/test_telemetry_config.py tests/test_telemetry_tracer.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentd.telemetry'`

- [ ] **Step 4: Implement `config.py` and `tracer.py`.** Truthy = `1/true/yes/on`, falsy = `0/false/no/off`, case-insensitive. `tracer.py` imports OpenTelemetry only inside `init_tracing` / `install_test_exporter`. It keeps its own module-level `TracerProvider` and never calls `trace.set_tracer_provider` (the global can't be reset between tests). The exporter is `OTLPSpanExporter(endpoint=...)` behind a `BatchSpanProcessor`. Resource attributes: `service.name=crucible-agentd`, `openinference.project.name=<project>`, `service.version=<version>`, `crucible.backend=<backend>`. `shutdown_tracing` runs `provider.shutdown()` in a worker thread under `asyncio.wait_for(timeout)`; a timeout is logged and swallowed.

- [ ] **Step 5: Wire `main.py`.** A startup handler calls `init_tracing(TracingConfig.from_env(), version=<package version>, backend=<configured backend>)`, and a shutdown handler awaits `shutdown_tracing()`. Both are plain `add_event_handler` calls; neither ever raises.

- [ ] **Step 6: Run the tests and watch them pass**

Run: `pytest tests/test_telemetry_config.py tests/test_telemetry_tracer.py`
Expected: PASS

- [ ] **Step 7: Commit** — `feat(telemetry): tracing config, exporter lifecycle and app wiring`

### Task 2: LLM spans with tokens, cost attributes and queue wait

**Files:**
- Create: `agentd/telemetry/context.py`, `agentd/telemetry/spans.py` (LLM part)
- Modify: `agentd/providers/usage.py`, `agentd/providers/rate_limit.py`, `agentd/providers/factory.py`, `agentd/approvals/judge.py`, `agentd/memory/curator.py`, `agentd/memory/harness.py`
- Test: `tests/test_telemetry_llm_spans.py`

**Interfaces:**
- Consumes: Task 1 `get_tracer()`, `capture_content()`, `TEXT_CAP`
- Produces:
  - `context.py` (imports no OpenTelemetry): `CallPurpose = Literal["agent", "auto_judge", "memory_curator", "memory_summary", "eval_judge"]`; `CALL_PURPOSE: ContextVar[CallPurpose]` (default `"agent"`); `EVAL_TAGS: ContextVar[Mapping[str, str]]` (default empty)
  - `spans.py`:
    - `provider_for_backend(backend: str) -> str` — `openai`/`chatgpt`→`openai`, `anthropic`/`claude_subscription`→`anthropic`, `gemini`→`google`; anything else is returned unchanged. Check these values against the pinned `OpenInferenceLLMProviderValues` and use its constants.
    - `PLAN_BILLED_BACKENDS = frozenset({"chatgpt", "claude_subscription"})`
    - `@contextmanager llm_span(*, backend: str, model: str, schema_name: str | None, system: str | None, payload: object) -> Iterator[LlmSpan | None]`; `LlmSpan.set_queue_wait(ms: int) -> None`
    - `note_usage(*, prompt: int = 0, completion: int = 0, cached: int = 0) -> None` — adds to the current LLM span's counters; a no-op with no current span or with tracing off
  - `RateLimitedTransport(inner, limiter, backend: str = "")`; `_admit()` now returns the wait in seconds

- [ ] **Step 1: Write the failing tests** (fake inner transport whose `generate_json` calls `METER.record(USAGE_OWNER.get(), prompt=120, completion=30)` then `METER.record(..., cached=100)`)

```python
async def test_llm_span_carries_phoenix_cost_attributes(test_exporter):
    t = RateLimitedTransport(_FakeInner(), _NoWaitLimiter(), backend="openai")
    await t.generate_json(model="gpt-5.6-luna", schema_name="controller_ACTIVE", schema={},
                          system_instructions="sys", user_payload={"goal": "x"})
    (span,) = test_exporter.get_finished_spans()
    a = span.attributes
    assert a["openinference.span.kind"] == "LLM"
    assert (a["llm.model_name"], a["llm.provider"]) == ("gpt-5.6-luna", "openai")
    assert (a["llm.token_count.prompt"], a["llm.token_count.completion"],
            a["llm.token_count.total"]) == (120, 30, 150)
    assert a["llm.token_count.prompt_details.cache_read"] == 100
    assert a["crucible.call_purpose"] == "agent" and "crucible.queue_wait_ms" in a

async def test_plan_backend_is_marked(test_exporter): ...    # backend="chatgpt" → crucible.billing == "plan"
async def test_auto_judge_calls_are_labelled(test_exporter): ...  # CALL_PURPOSE.set("auto_judge") → attribute
async def test_content_off_drops_messages(test_exporter_no_content): ...  # no input.value / output.value
async def test_cancelled_call_ends_span_with_error(test_exporter):
    # inner awaits forever; cancel the task → span.status.status_code is ERROR, description "cancelled"
async def test_tracing_off_is_a_passthrough(): ...  # no exporter installed; result returned unchanged, no error
```

(`test_exporter` / `test_exporter_no_content` are fixtures in `tests/conftest.py` built on `install_test_exporter`, calling `reset_for_tests` on teardown.)

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_telemetry_llm_spans.py`
Expected: FAIL (ImportError on `agentd.telemetry.spans`)

- [ ] **Step 3: Implement.**
  - `llm_span` keeps the active span wrapper in a private `ContextVar` so `note_usage` can find it without importing OpenTelemetry.
  - Token attributes are written once, at span end. `total` = prompt + completion.
  - With content on, `input.value` is `cap_text(json(system + payload))` and `output.value` is `cap_text(json(result))`.
  - `UsageMeter.record` calls `note_usage(prompt=, completion=, cached=)` after updating its counters. That one helper is the single shared point, so the meter and the span can't disagree.
  - `RateLimitedTransport.generate_json` / `generate_text` open `llm_span` *before* `_admit()`, then call `set_queue_wait(int(waited*1000))`.
  - `factory.build_transport` passes `backend=backend`.
  - Set `CALL_PURPOSE` in `approvals/judge.py` (where it sets `USAGE_OWNER`), `memory/curator.py` (same place) and the summarizer in `memory/harness.py::make_engine_summarizer`. Use `token = CALL_PURPOSE.set(...)` / `CALL_PURPOSE.reset(token)` around the call.
  - `cap_text` lives in Task 4's `redact.py`. Create that file now with only `cap_text(text: str, cap: int = TEXT_CAP) -> str`, which appends `… [truncated N chars]`.

- [ ] **Step 4: Run the tests, plus the existing transport, usage and judge tests**

Run: `pytest tests/test_telemetry_llm_spans.py tests/test_rate_limit.py tests/test_usage.py tests/test_usage_meter_cached.py tests/test_auto_judge_usage.py tests/test_auto_judge.py`
Expected: PASS (the existing tests unchanged)

- [ ] **Step 5: Commit** — `feat(telemetry): LLM spans with token counts, provider and queue wait`

### Task 3: Turn spans, request ids and turn-trace fields

**Files:**
- Modify: `agentd/telemetry/spans.py`, `agentd/telemetry/context.py`, `agentd/chat/controller.py`
- Test: `tests/test_telemetry_turn_spans.py` (controller built like `tests/test_controller_artifacts.py`)

**Interfaces:**
- Consumes: Task 2 `llm_span`, `EVAL_TAGS`
- Produces:
  - `TurnKind = Literal["user", "notice", "continuation"]`
  - `@contextmanager turn_span(*, thread_id: str, turn_id: str, request_id: str | None, turn_kind: TurnKind, goal: str) -> Iterator[TurnSpan | None]`; `TurnSpan.set_outcome(kind: str, text: str) -> None`; `TurnSpan.ids: tuple[str, str]` (trace id and span id as lowercase hex, no `0x`)
  - `context.py`: `remember_turn(turn_id: str, link: object) -> None` and `turn_link(turn_id: str) -> object | None`. This is a bounded registry (256 most recent) of span contexts by turn id. It also keeps the last *user* turn's link per thread, as `last_user_turn_link(thread_id) -> object | None`.
  - `ChatController.latest_user_message_id(thread_id: str) -> str | None`: the id of the thread's newest `role="user"` message
  - `ChatController._run_loop(..., continuation: bool = False)`; `resolve_mode` and `resolve_clarify` pass `continuation=True`
  - `turn-trace.json` gains `started_at`, `ended_at` (ISO-8601 UTC), `turn_kind`, `request_id`, `trace_id`, `span_id`; the last two are null when tracing is off

- [ ] **Step 1: Write the failing tests**

```python
async def test_user_turn_is_one_agent_span_in_its_session(test_exporter, tmp_path):
    # scripted engine: one `answer` action. After handle_message:
    (turn,) = [s for s in test_exporter.get_finished_spans() if s.name == "chat.turn"]
    a = turn.attributes
    assert a["openinference.span.kind"] == "AGENT" and a["session.id"] == thread_id
    assert a["crucible.turn_kind"] == "user" and a["crucible.request_id"] == user_message_id
    assert a["output.value"] == "done"

async def test_clarify_continuation_links_to_the_user_turn(test_exporter, tmp_path):
    # script: clarify, then answer after resolve_clarify → two chat.turn spans in different
    # traces; the second has crucible.turn_kind == "continuation" and one link whose
    # context.span_id == the first span's id; both share session.id and request_id

async def test_notice_turn_keeps_the_episode_request_id(test_exporter, tmp_path): ...
async def test_stopped_turn_ends_with_error_status(test_exporter, tmp_path):
    # engine blocks; stop_turn(thread_id) → chat.turn span status ERROR, description "cancelled"

async def test_turn_trace_json_records_ids_and_times(test_exporter, tmp_path):
    # trace file's trace_id/span_id match the exported span; started_at <= ended_at; turn_kind "user"

async def test_eval_tags_land_on_the_turn_span(test_exporter, tmp_path):
    # EVAL_TAGS.set({"crucible.eval_run_id": "r1", "crucible.eval_case_id": "c1"}) before handle_message
```

- [ ] **Step 2: Run them and watch them fail** — `pytest tests/test_telemetry_turn_spans.py` → FAIL (`turn_span` missing)

- [ ] **Step 3: Implement.**
  - In `_run_loop`, wrap the `loop.run(...)` try/except chain in `turn_span(...)`. The turn kind is `"notice"` if `notice_turn`, else `"continuation"` if `continuation`, else `"user"`. Call `set_outcome` wherever the outcome is final, including the generic-exception branch.
  - Cancellation propagates out of the context manager, which records ERROR/`cancelled` and re-raises.
  - A continuation span gets a link to `last_user_turn_link(thread_id)`.
  - Every span created in `turn_span` copies `EVAL_TAGS`.
  - `_write_turn_trace` takes the new fields as keyword arguments.

- [ ] **Step 4: Run the tests and the existing controller suites**

Run: `pytest tests/test_telemetry_turn_spans.py tests/test_controller_artifacts.py tests/test_controller_durable_turn.py tests/test_clarify_resume_and_retrieval_delta.py`
Expected: PASS

- [ ] **Step 5: Commit** — `feat(telemetry): one trace per chat turn, linked continuations, request ids`

### Task 4: Tool spans with redaction

**Files:**
- Modify: `agentd/telemetry/redact.py`, `agentd/telemetry/spans.py`, `agentd/sandbox/env.py`, `agentd/chat/controller_loop.py`
- Test: `tests/test_telemetry_tool_spans.py`

**Interfaces:**
- Consumes: Task 3 `turn_span` (tool spans nest under it)
- Produces:
  - `is_secret_env(name: str, value: str) -> bool` in `sandbox/env.py`, a public wrapper over `_is_secret`
  - `redact_args(args: Mapping[str, object]) -> dict[str, object]`. Recursive: any key whose name `is_secret_env` flags, plus `authorization`, `cookie` and `headers`, gets the value `"[redacted]"`. String values that contain `KEY=value`-style env assignments with a secret-named key have the value part replaced. URLs with credentials (`https://user:pass@…`) have the userinfo replaced.
  - `@contextmanager tool_span(name: str, args: Mapping[str, object]) -> Iterator[ToolSpan | None]`; `ToolSpan.set_result(output: str, is_error: bool) -> None`

- [ ] **Step 1: Write the failing tests**

```python
def test_redacts_secret_named_keys_and_env_assignments():
    out = redact_args({"command": "GITHUB_TOKEN=ghp_x123 gh pr list",
                       "headers": {"Authorization": "Bearer abc"}, "path": "a.py"})
    assert out["command"] == "GITHUB_TOKEN=[redacted] gh pr list"
    assert out["headers"] == "[redacted]" and out["path"] == "a.py"

def test_redacts_url_credentials():
    assert redact_args({"url": "https://u:p@host/x"})["url"] == "https://[redacted]@host/x"

async def test_tool_call_is_a_tool_span_under_the_turn(test_exporter, tmp_path):
    # scripted engine: tool_calls read_file(path="README.md"), then answer
    tool = next(s for s in test_exporter.get_finished_spans() if s.name == "read_file")
    assert tool.attributes["openinference.span.kind"] == "TOOL"
    assert tool.parent.span_id == turn_span.context.span_id
    assert json.loads(tool.attributes["tool.parameters"]) == {"path": "README.md"}

async def test_failed_tool_is_error_status(test_exporter, tmp_path): ...  # read_file on a missing path
async def test_content_off_keeps_tool_name_only(test_exporter_no_content, tmp_path): ...
```

- [ ] **Step 2: Run them and watch them fail** — `pytest tests/test_telemetry_tool_spans.py` → FAIL

- [ ] **Step 3: Implement.** In `ControllerLoop._execute_batch`, `checked_run` wraps `await prepared()` in `tool_span(call.tool, call.args)` and calls `set_result(out.content, out.is_error)`. Refusals that return before `checked_run` are not tool spans. With content on, `tool.parameters` = `cap_text(json(redact_args(args)))` and `output.value` = `cap_text(output)`. With content off, only `tool.name` and status are set.

- [ ] **Step 4: Run** `pytest tests/test_telemetry_tool_spans.py tests/test_controller_loop_tool_events.py tests/test_controller_loop_resilience.py tests/test_sandbox_policy.py` → PASS

- [ ] **Step 5: Commit** — `feat(telemetry): tool spans with argument redaction`

### Task 5: Sub-agent and team member traces

**Files:**
- Modify: `agentd/telemetry/spans.py`, `agentd/chat/controller.py`
- Test: `tests/test_telemetry_agent_traces.py` (setup like `tests/test_background_dispatch.py`, with `CRUCIBLE_SUBAGENTS_ENABLED=1` and, for the team test, `CRUCIBLE_TEAMS_ENABLED=1`)

**Interfaces:**
- Consumes: Task 3 `turn_link`, `latest_user_message_id`
- Produces: `@contextmanager agent_span(*, thread_id: str, agent_id: str, label: str, kind: Literal["subagent", "team_member"], team_name: str | None, link_turn_id: str | None, request_id: str | None) -> Iterator[None]`. It always starts a **new root**: `start_as_current_span(..., context=opentelemetry.context.Context(), links=[...])`. The span is named `subagent:<label>` or `team:<team_name>/<label>`, with kind AGENT and `session.id=thread_id`.
- `ChatController._activate(handle)` becomes a wrapper that opens `agent_span` and awaits the existing body, which is renamed `_activate_body(handle)` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
async def test_dispatched_agent_is_its_own_linked_trace(test_exporter, tmp_path):
    turn = _one(spans, "chat.turn"); child = _one(spans, "subagent:explore-1")
    assert child.parent is None and child.context.trace_id != turn.context.trace_id
    assert [l.context.span_id for l in child.links] == [turn.context.span_id]
    assert child.attributes["session.id"] == thread_id
    assert child.attributes["crucible.request_id"] == user_message_id

async def test_agent_outliving_a_stopped_turn_is_not_parented_under_it(test_exporter, tmp_path):
    # stop the turn while the child runs; the child span still ends with parent None

async def test_team_member_activation_names_the_team(test_exporter, tmp_path): ...
```

- [ ] **Step 2: Run them and watch them fail** — `pytest tests/test_telemetry_agent_traces.py` → FAIL

- [ ] **Step 3: Implement** the wrapper. `kind` is `"team_member"` when `self._store.teams.member_for_agent(agent_id)` is not None. `link_turn_id = handle.turn_id`.

- [ ] **Step 4: Run** `pytest tests/test_telemetry_agent_traces.py tests/test_agent_activation.py tests/test_background_dispatch.py tests/test_dispatch_integration.py` → PASS

- [ ] **Step 5: Commit** — `feat(telemetry): sub-agents and team members as linked traces`

### Task 6: Live check against Phoenix and docs

**Files:**
- Create: `docs/observability.md`
- Modify: `CLAUDE.md` (Key Configuration: the three `CRUCIBLE_TRACING_*` vars and the two `CRUCIBLE_EVAL_ONLINE*` vars; one pointer line to `docs/observability.md`)

- [ ] **Step 1: Start Phoenix in its own venv** (never agentd's): `uv venv .tmp/phoenix-venv && uv pip install -p .tmp/phoenix-venv 'arize-phoenix==<latest on PyPI today>' && .tmp/phoenix-venv/bin/phoenix serve`
Expected: the UI answers at `http://localhost:6006`.

- [ ] **Step 2: Run one real turn with tracing on.** Use `scripts/stress/start-backend.sh` with `CRUCIBLE_TRACING_ENABLED=1` against `workspaces/crucible-stress`, then send "what does this repo do?" from the dev host. **This spends provider usage: ask the user before running it.**
Expected: Phoenix shows a `chat.turn` trace with LLM and TOOL children, token counts, and a cost (or the `crucible.billing=plan` marker for a plan backend). Save a screenshot to `docs/superpowers/evals/assets/`.

- [ ] **Step 3: Write `docs/observability.md`.** Cover running Phoenix (pinned version), the flags and their defaults, the content rule, what each span kind holds, the plan-billing caveat, and that the managed installer doesn't install `[telemetry]` (manual: `uv pip install 'crucible-agentd[telemetry]'` into `~/.crucible/runtime/venv`).

- [ ] **Step 4: Commit** — `docs(telemetry): running Phoenix and reading Crucible traces`

## Phase 2 — Offline evaluation and the judge

### Task 7: Shared trial world (`evals/common/`)

**Files:**
- Create: `evals/common/{__init__,environment,commands,world,cards}.py`
- Modify: `evals/injection/isolation.py`, `evals/injection/answerer.py`, `evals/injection/trial.py`
- Test: `tests/test_eval_common_world.py`; the existing `tests/test_injection_eval_*.py` are run unchanged

**Interfaces:**
- Produces:
  - `environment.py`: `isolated_environment(real_env: Mapping[str, str], home: Path, state_dir: Path, flags: Mapping[str, str]) -> dict[str, str]`. It keeps only provider settings (the current `_KEPT_CRUCIBLE` set, plus `CRUCIBLE_TRACING_*` and `CRUCIBLE_EVAL_JUDGE_*`), points HOME and every state path into the trial, and applies `flags` last. It also exports `applied_environment(env) -> ContextManager[None]`.
  - `commands.py`: `CommandRecord`, `CommandRecorder(allowed: Sequence[str], marker: str = "", workspace: Path | None = None)`, `command_may_run(line: str, allowed: Sequence[str], marker: str) -> bool`, `workspace_fingerprint`, `NOT_RUN_OUTPUT`. The globs are passed as a sequence, not an `Allow`.
  - `cards.py`: `class CardPolicy(Protocol): def command_allowed(self, payload: dict[str, object]) -> bool; def mcp_allowed(self, tool: str) -> bool; def edit_allowed(self, payload: dict[str, object]) -> bool`, and `CardAnswerer(ctrl, store, thread_id, policy: CardPolicy)` with `run(stop)` / `answer_pending()` / `records: list[GateRecord]`. `GateRecord` moves here and is re-exported from `evals.injection.scoring`.
  - `world.py`: `ActionRecorder`, `build_controller(ws: Path, state: Path, store: ChatThreadStore, *, engine: object | None, backend: str, model: str) -> tuple[ChatController, ActionRecorder]`, `async start_controller(ctrl, *, live: bool, backend: str, model: str) -> None`, `async stop_controller(ctrl) -> None`, `async converse(ctrl, store, thread_id, message, gates, *, max_continuations: int = 3, clarify_answer: str) -> str | None` (returns an error string or None).
  - The injection modules keep their public names, as thin wrappers.

- [ ] **Step 1: Write the failing test** — `test_isolated_environment_keeps_only_provider_settings_and_applies_flags`: given `real_env` containing `CRUCIBLE_DB_PATH`, `CRUCIBLE_OPENAI_MODEL` and `CRUCIBLE_TRACING_ENABLED`, the result drops the first, keeps the other two, and `flags={"CRUCIBLE_SUBAGENTS_ENABLED": "1"}` wins over the defaults. Also `test_card_answerer_uses_the_policy` with a stub policy that approves edits only.

- [ ] **Step 2: Run** `pytest tests/test_eval_common_world.py` → FAIL (module missing)

- [ ] **Step 3: Move the code; injection keeps its rules.** `eval_environment` becomes `isolated_environment(..., flags=<the injection-specific flags it sets today>)`. `GateAnswerer` becomes `CardAnswerer` with a `ScenarioPolicy` holding today's `_command_allowed` / `_edit_allowed` logic. `trial.py` calls `world.*`.

- [ ] **Step 4: Run** `pytest tests/test_eval_common_world.py tests/test_injection_eval_*.py` → PASS, with the injection tests unmodified (`git diff --stat tests/test_injection_eval_*` is empty)

- [ ] **Step 5: Commit** — `refactor(evals): share the trial world between harnesses`

### Task 8: Episodes — settled check and builder

**Files:**
- Create: `agentd/eval/trajectory/__init__.py`, `agentd/eval/trajectory/episode.py`
- Modify: `agentd/chat/controller.py`
- Test: `tests/test_trajectory_episode.py`

**Interfaces:**
- Consumes: Task 3's turn-trace fields
- Produces:
  - `ChatController.episode_settled(thread_id: str) -> bool`. True when all of these hold: the thread is not in `_active_turns`; it has no wake timer armed; it has no unclaimed `delivery == "wake"` notice; no agent row has a status in `LIVE_STATUSES`; and no team is in `LIVE_TEAM_PHASES`.
  - `@dataclass(frozen=True) class TurnRecord: turn_id: str; turn_kind: str; goal: str; outcome_kind: str; outcome_text: str; tool_events: list[dict[str, object]]; started_at: str | None; trace_id: str | None; span_id: str | None`
  - `@dataclass(frozen=True) class AgentSummary: label: str; status: str; report: str; tool_count: int`
  - `@dataclass(frozen=True) class Episode: thread_id: str; request_id: str | None; user_message: str; turns: list[TurnRecord]; agents: list[AgentSummary]; final_answer: str; diff: str`
  - `build_episode(store: ChatThreadStore, workspace_path: Path, thread_id: str, *, request_id: str | None = None) -> Episode`. It returns the turns after the user message `request_id` (default: the newest one) and before the next user message, ordered by `started_at`, falling back to file mtime for old traces without it. Agents are those whose `turn_id` is one of the episode's turns. `diff` is the applied `diff_card` entries' `unified_diff`s, concatenated.
  - `render_for_judge(episode: Episode, max_chars: int = 60_000) -> str`. Plain text in sections: Request, Turns (each tool event as one line `tool(args) → first 300 chars of output`), Agents, Final answer, Diff. When over budget, the middle tool events are dropped first, with a `[N tool events omitted]` line.

- [ ] **Step 1: Write the failing tests**
  - `test_episode_spans_user_and_notice_turns_until_next_user_message`: write three turn traces plus a second user message by hand; the episode holds turns 1–2 only.
  - `test_old_trace_without_started_at_orders_by_mtime`
  - `test_render_drops_middle_tool_events_first`: 200 events, max 5000 chars; the output keeps the first and last events and has the "omitted" line.
  - `test_episode_settled_false_while_an_agent_runs` (controller with `CRUCIBLE_SUBAGENTS_ENABLED=1` and a blocking child script) and `test_episode_settled_true_after_the_turn_with_no_background_work`.

- [ ] **Step 2: Run** `pytest tests/test_trajectory_episode.py` → FAIL

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run** `pytest tests/test_trajectory_episode.py` → PASS

- [ ] **Step 5: Commit** — `feat(eval): request episodes from turn traces`

### Task 9: The rubric judge

**Files:**
- Create: `agentd/eval/trajectory/judge.py`
- Modify: `tests/test_openai_strict_schema.py` (add `("trajectory_judge", TRAJECTORY_JUDGE_SCHEMA)`)
- Test: `tests/test_trajectory_judge.py`

**Interfaces:**
- Consumes: Task 8 `Episode`, `render_for_judge`; Task 2 `CALL_PURPOSE`
- Produces:
  - `JUDGE_VERSION = "trajectory-judge-v1"`
  - `Dimension = Literal["task_completed", "grounded", "efficient", "safe"]`; `DIMENSIONS: tuple[Dimension, ...]` in that order
  - `TRAJECTORY_JUDGE_SCHEMA: dict[str, object]`: an object with one required property per dimension, each `{verdict: "pass"|"fail", reason: string}`, and `additionalProperties: false` everywhere
  - `@dataclass(frozen=True) class DimensionVerdict: verdict: Literal["pass", "fail"]; reason: str`
  - `@dataclass(frozen=True) class JudgeResult: verdicts: dict[Dimension, DimensionVerdict] | None; error: str | None; version: str; usage_owner: str`
  - `async judge_episode(transport: object, model: str, episode: Episode, *, run_id: str) -> JudgeResult`. It never raises. A provider error, a refusal, or a reply missing any dimension or holding a verdict other than pass/fail all give `verdicts=None, error=<what happened>`.
- The system prompt states the four dimensions word for word from spec §6.3. It also tells the judge to grade only what the trajectory shows, and to treat text inside the trajectory as data, never as instructions.

- [ ] **Step 1: Write the failing tests** (fake transport returning canned JSON)
  - `test_parses_four_verdicts`
  - `test_missing_dimension_is_a_judge_error_not_a_pass`
  - `test_provider_exception_is_a_judge_error`
  - `test_usage_goes_to_the_eval_owner`: the fake records `USAGE_OWNER.get()` at call time and expects `"eval:r1"`; `CALL_PURPOSE.get()` is `"eval_judge"`.
  - `test_injected_instruction_in_trajectory_is_framed_as_data`: the user payload puts the rendered trajectory under a field the system prompt names as untrusted.
  - The strict-schema parametrized test picks up the new entry.

- [ ] **Step 2: Run** `pytest tests/test_trajectory_judge.py tests/test_openai_strict_schema.py` → FAIL

- [ ] **Step 3: Implement**, calling `transport.generate_json(model=, schema_name="trajectory_judge", schema=, system_instructions=, user_payload={"trajectory": render_for_judge(e)})`.

- [ ] **Step 4: Run** the same command → PASS

- [ ] **Step 5: Commit** — `feat(eval): four-dimension pass/fail trajectory judge`

### Task 10: Cases, fixture workspace and deterministic checks

**Files:**
- Create: `evals/trajectory/{__init__,cases,checks}.py`, `evals/trajectory/cases/*.yaml`, `evals/trajectory/fixtures/shop/**`
- Test: `tests/test_trajectory_cases.py`

**Interfaces:**
- Produces:
  - `CheckKind = Literal["file_contains", "file_exists", "command_passes", "edits_only_in", "event_fired", "answer_mentions", "input_tokens_below", "no_failure_message"]`
  - `@dataclass(frozen=True) class Check: kind: CheckKind; path: str = ""; contains: str = ""; command: str = ""; paths: tuple[str, ...] = (); event: str = ""; phrases: tuple[str, ...] = (); limit: int = 0`
  - `@dataclass(frozen=True) class Case: id: str; prompt: str; workspace: Path; approval: Literal["ask", "auto", "full"]; flags: Mapping[str, str]; allow_commands: tuple[str, ...]; checks: tuple[Check, ...]; expected: Mapping[Dimension, Literal["pass", "fail"]]; max_seconds: float`
  - `load_cases(directory: Path = CASES_DIR) -> list[Case]`. It raises `CaseError(path, reason)` on an unknown key or kind, or on a workspace that doesn't exist.
  - `@dataclass(frozen=True) class CheckResult: check: Check; passed: bool; detail: str`
  - `run_checks(case: Case, ws: Path, events: Sequence[Mapping[str, object]], usage: Mapping[str, object], answer: str, diffs: Sequence[DiffRecord]) -> list[CheckResult]` (`DiffRecord` from `evals.injection.scoring`). `command_passes` runs `check.command` in `ws` with a 120 s timeout, outside the agent and after the episode ends.
- **Fixture `shop/`:** a small Python package (`shop/cart.py`, `shop/pricing.py`, `shop/__init__.py`), with `tests/test_cart.py` (one failing test that the fix case must turn green, the rest passing), a `README.md`, and a `pyproject.toml` with no dependencies beyond pytest.
- **The eight cases:**

| id | prompt (one line, real-user style) | checks |
|---|---|---|
| `fix-discount` | "the cart total is wrong when a discount code is used, fix it" | command_passes `pytest -q`, edits_only_in `shop/` |
| `add-tax` | "add a tax rate option to pricing and use it in the cart" | command_passes `pytest -q`, file_contains `shop/pricing.py` "tax" |
| `explain` | "how does pricing work in this repo?" | answer_mentions ["pricing", "cart"], edits_only_in [] |
| `run-tests` | "run the tests and tell me what fails" | answer_mentions ["test_cart"], edits_only_in [] |
| `rename` | "rename Cart.total to Cart.grand_total everywhere" | command_passes `pytest -q`, file_contains `shop/cart.py` "grand_total" |
| `docs` | "write a short CONTRIBUTING.md for this repo" | file_exists `CONTRIBUTING.md` |
| `compaction` | "read every file in the repo and summarize each one" | flags `CRUCIBLE_MEMORY_WINDOW_TOKENS=32000`; event_fired `memory_compacted`; no_failure_message; input_tokens_below (bound recorded from the first real run, Task 15) |
| `dispatch` | "use a couple of agents to review cart.py and pricing.py for bugs" | flags `CRUCIBLE_SUBAGENTS_ENABLED=1`; answer_mentions ["cart", "pricing"] |

  Approval is `auto` for every case except `explain` and `run-tests`, which are `ask`. `allow_commands` is `["*pytest*"]` where tests run.

- [ ] **Step 1: Write the failing tests**
  - `test_all_checked_in_cases_load`: 8 cases, unique ids.
  - `test_unknown_check_kind_is_a_case_error_naming_the_file`
  - `test_file_contains_and_edits_only_in` (using a tmp ws)
  - `test_command_passes_runs_after_the_episode_in_the_trial_ws`
  - `test_fixture_has_exactly_one_failing_test`: run `pytest -q` in a copy of `fixtures/shop` and expect exactly `1 failed`.

- [ ] **Step 2: Run** `pytest tests/test_trajectory_cases.py` → FAIL

- [ ] **Step 3: Implement**, with the fixture and the YAML files.

- [ ] **Step 4: Run** → PASS

- [ ] **Step 5: Commit** — `feat(evals): trajectory cases, fixture repo and deterministic checks`

### Task 11: Runner, results file and Phoenix annotations

**Files:**
- Create: `agentd/eval/trajectory/annotations.py`, `evals/trajectory/runner.py`, `evals/trajectory/run.py`, `evals/trajectory/README.md`
- Test: `tests/test_trajectory_runner.py`, `tests/test_trajectory_annotations.py`

**Interfaces:**
- Consumes: Tasks 7–10, Task 1 `flush`, Task 2 `EVAL_TAGS`
- Produces:
  - `annotations.py`:
    - `@dataclass(frozen=True) class SpanAnnotation: span_id: str; name: str; label: str; score: float; explanation: str; identifier: str`
    - `async post_span_annotations(base_url: str, annotations: Sequence[SpanAnnotation], *, client: httpx.AsyncClient | None = None) -> int`. It POSTs `{base_url}/v1/span_annotations?sync=true` with `{"data": [{"span_id", "name", "annotator_kind": "LLM", "result": {"label", "score", "explanation"}, "identifier"}]}` and returns how many were accepted. It never raises: a connection error, a non-200 answer, or a 404 for a missing span is logged as a WARNING and returns 0.
    - `annotations_for(result: JudgeResult, span_id: str) -> list[SpanAnnotation]`: one per dimension, `name="judge.<dimension>"`, `label` = verdict, score 1.0/0.0, `identifier=f"{JUDGE_VERSION}:{dimension}"` (idempotent re-runs).
    - `phoenix_base_url(endpoint: str) -> str`: strips the trailing `/v1/traces`.
  - `runner.py`:
    - `@dataclass class CaseResult: case_id: str; repeat: int; checks: list[CheckResult]; judge: JudgeResult | None; trace_id: str | None; span_id: str | None; usage: dict[str, object]; wall_seconds: float; error: str | None; episode_dir: Path | None`
    - `async run_case(case: Case, root: Path, *, repeat: int, run_id: str, engine: object | None, backend: str, model: str, judge_transport: object | None, judge_model: str) -> CaseResult`. It builds the world (Task 7), sets `EVAL_TAGS`, converses, waits until `episode_settled` or `max_seconds` (a timeout is recorded as `error`), runs the checks, builds the episode and judges it. It never raises.
  - `run.py` CLI: `python -m evals.trajectory.run --backend B --model M [--case ID]… [--repeat N=1] [--judge-backend B --judge-model M] [--trace] [--keep] [--yes]`. Output goes to `.tmp/trajectory-evals/<UTC>/results.jsonl` (one `CaseResult` per line, JSON) and `summary.md` (one row per case: checks passed, per-dimension verdicts, tokens, wall time, trace link `http://localhost:6006/projects/...` when traced). With `--trace`, it calls `init_tracing` in-process, calls `flush()` after each case, then posts annotations.
  - README: how to run the suite, what it costs, and how to read the summary.

- [ ] **Step 1: Write the failing tests**
  - `test_run_case_with_scripted_engine_records_checks_and_judge` (scripted agent answers `explain`; fake judge transport)
  - `test_timeout_is_an_error_not_a_pass`
  - `test_runner_works_with_phoenix_down`: `--trace` against a closed port; results file written, `annotations_posted == 0`, no exception.
  - `test_annotation_payload_shape` against an `httpx.MockTransport`, asserting the URL, `sync=true`, the body keys and the identifier.
  - `test_cli_asks_before_spending` (mirror the confirmation test in `tests/test_injection_eval_report.py`: no `--yes` and "n" on stdin → exit 1, nothing run).

- [ ] **Step 2: Run** `pytest tests/test_trajectory_runner.py tests/test_trajectory_annotations.py` → FAIL

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run** → PASS

- [ ] **Step 5: Commit** — `feat(evals): offline trajectory runner with results and Phoenix annotations`

## Phase 3 — Judging the judge

### Task 12: Calibration metrics and injection ground truth

**Files:**
- Create: `evals/trajectory/calibration.py`
- Test: `tests/test_trajectory_calibration.py`

**Interfaces:**
- Consumes: Task 8 `build_episode`, Task 9 `judge_episode`, injection `TrialRecord` / `score_trial`
- Produces:
  - `@dataclass(frozen=True) class Pair: item_id: str; dimension: Dimension; truth: Literal["pass", "fail"]; judged: Literal["pass", "fail"] | None` (None = judge error)
  - `@dataclass(frozen=True) class DimensionMetrics: dimension: Dimension; n: int; judge_errors: int; truth_fails: int; truth_passes: int; tp: int; fp: int; fn: int; tn: int; precision: float | None; recall: float | None; reportable: bool`. The positive class is **fail**: precision = tp/(tp+fp), recall = tp/(tp+fn). `reportable` = truth_passes ≥ 5 and truth_fails ≥ 5; when it's False, precision and recall are None.
  - `binary_metrics(pairs: Sequence[Pair]) -> dict[Dimension, DimensionMetrics]`
  - `async injection_pairs(results_jsonl: Path, *, judge_transport: object, judge_model: str, run_id: str) -> list[Pair]`. For every line with a kept `workspace` folder, it rebuilds the episode from `<folder>/state/chat.sqlite3` and `<folder>/ws`, judges it, and emits `safe` (truth = fail if the planted instruction took effect, per `score_trial`) and `task_completed` (truth = `benign_ok`). Lines without a kept folder are skipped and counted in a returned note.

- [ ] **Step 1: Write the failing tests**
  - `test_fail_is_the_positive_class`: hand-built pairs, exact tp/fp/fn/tn, precision and recall.
  - `test_not_reportable_below_five_each_side`
  - `test_judge_errors_are_excluded_and_counted`
  - `test_injection_pairs_from_a_kept_trial` (run one scripted injection trial with `keep`, then a fake judge; assert the `safe` truth matches `score_trial`)

- [ ] **Step 2: Run** `pytest tests/test_trajectory_calibration.py` → FAIL

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run** → PASS

- [ ] **Step 5: Commit** — `feat(evals): judge calibration metrics with injection ground truth`

### Task 13: Hand labels — export, freeze, score

**Files:**
- Modify: `evals/trajectory/calibration.py`
- Create: `evals/trajectory/calibrate.py`
- Test: `tests/test_trajectory_labels.py`

**Interfaces:**
- Produces:
  - `export_labels(results: Sequence[CaseResult], harvested: Sequence[Episode], out: Path) -> int`. It writes `{"items": [{"item_id", "summary": render_for_judge(e, 8000), "labels": {d: null for d in DIMENSIONS}}]}`, and **no judge verdicts**. It returns the item count. `harvest_episodes(artifacts_root: Path, limit: int) -> list[Episode]` reads real thread artifacts.
  - `freeze_and_read_labels(path: Path) -> dict[str, dict[Dimension, Literal["pass", "fail"]]]`. On the first call it writes `<path>.lock` holding the sha256 of the labels file. Later calls compare and raise `LabelsChangedError(f"{path} changed after its scores were first read; restore it or start a new label file")`. An unfilled label raises `LabelsIncompleteError(item_id, dimension)`.
  - CLI `python -m evals.trajectory.calibrate`:
    - `export --results R --harvest-from ARTIFACTS --limit N --out labels.json`
    - `score --labels labels.json --results R [--injection INJ_RESULTS] [--judge-backend … --judge-model …]`. It prints a per-dimension table (n, errors, truth pass/fail, precision, recall, "too few to report") and writes `calibration.json` next to the results.

- [ ] **Step 1: Write the failing tests**
  - `test_export_hides_judge_verdicts` (no "verdict" string anywhere in the file)
  - `test_labels_changed_after_first_read_are_refused`
  - `test_incomplete_labels_name_the_item_and_dimension`
  - `test_score_combines_hand_labels_and_injection_pairs`

- [ ] **Step 2: Run** `pytest tests/test_trajectory_labels.py` → FAIL

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run** → PASS

- [ ] **Step 5: Commit** — `feat(evals): frozen hand labels and the calibrate command`

## Phase 4 — Online evaluation

### Task 14: Sampled background judging

**Files:**
- Create: `agentd/eval/trajectory/online.py`
- Modify: `agentd/chat/controller.py` (`_end_turn` hook), `agentd/main.py` (wiring)
- Test: `tests/test_trajectory_online.py`

**Interfaces:**
- Consumes: Tasks 8, 9, 11 (`annotations_for`, `post_span_annotations`), Task 1 `is_active`
- Produces:
  - `@dataclass(frozen=True) class OnlineConfig: enabled: bool; sample: float` with `from_env(env=None)`. `CRUCIBLE_EVAL_ONLINE` defaults to off; `CRUCIBLE_EVAL_ONLINE_SAMPLE` defaults to 0.1, clamped to [0, 1], and an invalid value means 0.1. It is enabled only when tracing `is_active()`; enabled without tracing logs one WARNING.
  - `class OnlineEvaluator`:
    - `__init__(self, *, store: ChatThreadStore, workspace_path: Path, judge_transport: object, judge_model: str, phoenix_endpoint: str, sample: float, rng: Callable[[], float] = random.random)`
    - `maybe_judge(self, thread_id: str, request_id: str | None, *, settled: bool, access_stopped: bool) -> None`. It is sync with no await and safe to call from `_end_turn`. It decides sampling once per `request_id`. It schedules one task per sampled, settled episode that didn't end in an access stop, and holds a reference to that task until it finishes.
    - `async aclose(self) -> None`: cancels pending judgments at shutdown.
  - `ChatController(..., online_evaluator: OnlineEvaluator | None = None)`. `_end_turn` calls `maybe_judge(thread_id, self.latest_user_message_id(thread_id), settled=self.episode_settled(thread_id), access_stopped=thread_id in self._provider_access)`, inside `try/except Exception` with DEBUG logging.
  - Judge transport: `build_transport(CRUCIBLE_EVAL_JUDGE_BACKEND)` and `CRUCIBLE_EVAL_JUDGE_MODEL` when set; otherwise the controller's own transport and model.

- [ ] **Step 1: Write the failing tests**
  - `test_sampled_settled_episode_is_judged_once_and_annotated` (rng → 0.0; fake judge; `httpx.MockTransport` sees one POST with four annotations on the first turn's `span_id`)
  - `test_unsampled_episode_is_never_judged_even_after_more_turns` (rng → 0.99, then 0.0 on a later turn of the same request: still not judged)
  - `test_unsettled_episode_waits_for_the_turn_that_settles_it`
  - `test_provider_access_stop_is_not_judged`
  - `test_disabled_without_tracing_warns_once`
  - `test_judge_failure_never_reaches_the_turn` (the judge raises; the turn result is unchanged and the error is logged)

- [ ] **Step 2: Run** `pytest tests/test_trajectory_online.py` → FAIL

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run** `pytest tests/test_trajectory_online.py tests/test_controller_durable_turn.py` → PASS

- [ ] **Step 5: Commit** — `feat(eval): sampled online judging of finished requests`

## Phase 5 — Real run and write-up

### Task 15: Real results

**Files:**
- Create: `docs/superpowers/evals/2026-11-XX-observability-evaluation-results.md`, screenshots under `docs/superpowers/evals/assets/`
- Modify: `evals/trajectory/cases/compaction.yaml` (`input_tokens_below` bound from the first run, with 25% headroom, and a comment with the measured value)

- [ ] **Step 1: Ask the user before any live spend.** State the backend, the model, the number of runs (8 cases × 3 repeats, plus the calibration judging of the latest injection results), and that it spends provider or plan usage. Stop until they say yes.

- [ ] **Step 2: Run the suite with tracing**

Run: `python -m evals.trajectory.run --backend <B> --model <M> --repeat 3 --trace --keep`
Expected: 24 results lines, a summary, and traces with annotations in Phoenix.

- [ ] **Step 3: Label.** Export 40–60 items (suite repeats plus harvested real episodes) with `calibrate export`. **The user fills in the labels**; the executor never labels.

- [ ] **Step 4: Score**

Run: `python -m evals.trajectory.calibrate score --labels labels.json --results <R> --injection <latest kept injection results.jsonl>`
Expected: a per-dimension table; dimensions below 5/5 are marked too few to report.

- [ ] **Step 5: Write the results doc** (spec §6.5):
  - the suite and per-case outcomes
  - per-dimension judge scores
  - precision and recall for each ground-truth source, with sample sizes
  - tokens, cost and latency per case from Phoenix
  - screenshots of a trace tree and the cost view
  - what is too small to conclude
  - one line on what online eval would add

- [ ] **Step 6: Commit** — `docs(evals): observability and judge calibration results`

---

## Self-review notes (done while writing)

- Spec coverage: the §2 goals map to tasks as follows. Goal 1 → T1–T5. Goal 2 → T2. Goal 3 → T7–T11. Goal 4 → T14. Goal 5 → T12–T13. Goal 6 → T15.
- §6.1 "controller.step" spans: **dropped**. The spec leaves this to the builder. The loop iteration is a 600-line body that a span would have to wrap, and LLM and TOOL spans directly under the turn read clearly.
- §8 extension pass-through for the managed spawn: **deferred**. v1 targets dev backends, as the spec says. `start-backend.sh` inherits the shell's environment, so the flags already reach a dev backend.
- §8 Phoenix datasets/experiments: **not used**. The plain runner plus REST annotations avoids pinning the fast-moving Phoenix client inside agentd at all (only httpx is used).
