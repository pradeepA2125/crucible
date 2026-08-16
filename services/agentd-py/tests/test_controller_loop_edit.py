from pathlib import Path

import pytest

from agentd.chat.controller_loop import ControllerLoop
from agentd.chat.controller_phase import ControllerPhaseSM
from agentd.chat.edit_session import TurnEditSession
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource
from agentd.workspace.shadow import ShadowWorkspaceManager


@pytest.mark.asyncio
async def test_edit_phase_promotes_then_submits(tmp_path: Path):
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()  # ACTIVE is the default phase (Task 1)
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    steps = [
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "x = 1", "replace": "x = 2", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "bumped x"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, EventBroadcaster(), channel_id="c", phase_sm=sm,
        edit_session_factory=lambda: sess)
    out = await loop.run(
        {"goal": "bump x", "workspace_path": str(real)}, max_iters=6,
        auto_accept_edits=True)
    assert out.kind == "submit_changes"
    assert (real / "f.py").read_text() == "x = 2\n"  # instant-promoted


def _drain(queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_failed_edit_surfaces_live_only_and_no_card(tmp_path: Path):
    """A failed edit attempt is invisible on its own (no card — edit_record_cb only fires
    on success), so it still needs a LIVE chat_agent_thinking event: the UI shows
    "✗ edit failed: <reason>" instead of a silent wait.

    But it must NOT reach the durable thinking_log. An engine/preflight error is not model
    reasoning, and thinking_log is replayed as the turn's permanent reasoning trace — the
    same rule _on_retry follows by broadcasting retry_status on its own channel. Baking
    machine errors in also feeds a weak model its own failures on reload, the repetition
    attractor this branch already avoids by not echoing patch_ops into history."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()  # ACTIVE is the default phase (Task 1)
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    steps = [
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "NOPE_NOT_PRESENT", "replace": "y", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "n/a"},
    ]
    cards: list = []

    async def _record(diff, decision, reason):
        cards.append(diff)

    bc = EventBroadcaster()
    q = bc.subscribe("c")
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, bc, channel_id="c", phase_sm=sm, edit_session_factory=lambda: sess)
    out = await loop.run(
        {"goal": "x", "workspace_path": str(real)}, max_iters=6,
        auto_accept_edits=True, edit_record_cb=_record)

    # No diff card for a failed edit.
    assert cards == []
    # Durable: the failure must NOT be baked into the permanent thinking log.
    assert not any("edit failed" in t for t in (out.thinking_log or [])), out.thinking_log
    # Live: a signal is still broadcast, so the UI is not silent while the model
    # retries — on the dedicated edit_failed channel (see the sibling test for why
    # it must not be the thinking channel).
    assert any(e["type"] == "edit_failed" for e in _drain(q))


@pytest.mark.asyncio
async def test_failed_edit_guidance_matches_the_failure_code(tmp_path: Path):
    """The guidance appended after PATCH FAILED used to be one frozen sentence about the
    'file' field — advice for the code-in-'file' malformation, sent for EVERY failure.
    On an anchor miss the 'file' field is correct and 'search' is what broke, so that
    sentence points the model at the wrong field. Guidance now follows the failure code."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    steps = [
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "NOPE_NOT_PRESENT", "replace": "y", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "n/a"},
    ]
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, EventBroadcaster(), channel_id="c", phase_sm=sm,
        edit_session_factory=lambda: sess)
    out = await loop.run(
        {"goal": "x", "workspace_path": str(real)}, max_iters=6,
        auto_accept_edits=True)

    failed = [str(h.get("content", "")) for h in (out.history or [])
              if "PATCH FAILED" in str(h.get("content", ""))]
    assert failed, out.history
    msg = failed[0]
    # The message itself now names the file (engine-side fix).
    assert "f.py" in msg, msg
    # Anchor-specific guidance, not the code-in-'file' boilerplate.
    assert "anchor" in msg.lower(), msg
    assert "code goes in 'content'" not in msg, msg


@pytest.mark.asyncio
async def test_failed_edit_uses_its_own_event_not_the_thinking_channel(tmp_path: Path):
    """A preflight/engine error is not model reasoning. It was broadcast as
    `chat_agent_thinking`, so the webview rendered it as a NUMBERED REASONING STEP
    in the thinking pane — indistinguishable from the model's own thought.

    Removing the durable thinking_log entry (earlier commit) only changed what
    survives a reload; the live line looked identical, so from the UI nothing had
    changed. It needs its own event type, exactly as _on_retry uses retry_status
    for the same reason ("a retry is not model reasoning")."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sm = ControllerPhaseSM()
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    steps = [
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "search_replace", "file": "f.py",
             "search": "NOPE_NOT_PRESENT", "replace": "y", "reason": "r"}]},
        {"type": "submit_changes", "thought": "done", "summary": "n/a"},
    ]
    bc = EventBroadcaster()
    q = bc.subscribe("c")
    loop = ControllerLoop(
        ScriptedReasoningEngine(None, [], controller_step_responses=steps),
        reg, bc, channel_id="c", phase_sm=sm, edit_session_factory=lambda: sess)
    await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=6,
                   auto_accept_edits=True)

    events = _drain(q)
    thinking = [e for e in events if e["type"] == "chat_agent_thinking"
                and "edit failed" in str(e["payload"].get("message", ""))]
    assert thinking == [], f"edit failure still on the thinking channel: {thinking}"

    failed = [e for e in events if e["type"] == "edit_failed"]
    assert failed, [e["type"] for e in events]
    assert "Search text not found" in str(failed[0]["payload"].get("reason", "")), failed[0]


@pytest.mark.asyncio
async def test_loop_broadcasts_live_token_progress(tmp_path: Path):
    """Reasoning streams visibly, but content deltas are accumulated silently and
    only surface when the call returns — a long generation is indistinguishable
    from a hang (observed live: 15 minutes behind a motionless UI). The loop must
    forward the transport's running counts as their own event, NOT as thinking."""
    real = tmp_path / "ws"
    real.mkdir()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])

    class _ProgressEngine:
        """Stands in for a transport that reports progress mid-call."""

        async def create_controller_step(self, plan_context, history, tool_definitions,
                                         *, phase, on_thinking=None, on_retry=None,
                                         on_progress=None, on_salvage=None,
                                         on_usage=None, unconstrained=False):
            if on_progress is not None:
                on_progress(2, 5)
                on_progress(2, 17)
            return {"type": "answer", "thought": "t", "answer": "done"}

    bc = EventBroadcaster()
    q = bc.subscribe("c")
    loop = ControllerLoop(_ProgressEngine(), reg, bc, channel_id="c",
                          phase_sm=ControllerPhaseSM())
    await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=3,
                   auto_accept_edits=True)

    events = _drain(q)
    prog = [e for e in events if e["type"] == "token_progress"]
    assert prog, [e["type"] for e in events]
    assert prog[-1]["payload"]["output"] == 17, prog
    assert prog[-1]["payload"]["thinking"] == 2, prog
    # Must not masquerade as model reasoning (same rule as edit_failed / retry_status).
    assert not any(e["type"] == "chat_agent_thinking"
                   and "17" in str(e["payload"]) for e in events)


@pytest.mark.asyncio
async def test_parse_failure_detail_reaches_the_model(tmp_path: Path):
    """The loop's except path already appends an informative correction carrying the
    exception text (it does NOT use the dead PARSEFAIL_CORRECTION constant). What was
    missing is that the transport used to burn 4 blind retries first and threw the
    offending text away. With fail-fast, the JSONDecodeError detail now reaches the
    model on the very next iteration — so the correction can actually be acted on."""
    real = tmp_path / "ws"
    real.mkdir()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])

    calls = {"n": 0}

    class _ParseFailThenAnswer:
        async def create_controller_step(self, plan_context, history, tool_definitions,
                                         *, phase, on_thinking=None, on_retry=None,
                                         on_progress=None, on_salvage=None,
                                         on_usage=None, unconstrained=False):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError(
                    "OpenAI-compatible output is not valid JSON for controller_step_response "
                    "(Expecting ',' delimiter: line 1 column 4523): {\"type\": \"answer\", "
                    "\"answer\": \"he said \"hello\" and left\"}")
            return {"type": "answer", "thought": "t", "answer": "recovered"}

    loop = ControllerLoop(_ParseFailThenAnswer(), reg, EventBroadcaster(),
                          channel_id="c", phase_sm=ControllerPhaseSM())
    out = await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=4,
                         auto_accept_edits=True)

    corrections = [str(h.get("content", "")) for h in (out.history or [])
                   if "previous response failed" in str(h.get("content", ""))]
    assert corrections, out.history
    # The specific parse error must survive into the model's context — a generic
    # "that was malformed" gives it nothing to fix.
    assert "Expecting ',' delimiter" in corrections[0], corrections[0]
    assert out.kind == "answer"


# ---------------------------------------------- parse-failure specific guidance
# The except branch appended ONE canned sentence ("emit exactly one JSON object,
# no prose, no fences") for every parse failure. Live on NIM that text happened to
# describe `Extra data` exactly — and the model recovered on the next call. It says
# nothing about `Invalid control character` (a raw newline inside a string value),
# which duly recurred. Same shape as the edit-guidance boilerplate: one answer to
# several different questions.

def test_control_character_error_explains_escaping():
    """The failure that kept recurring: literal newlines inside a string value.
    'Emit exactly one JSON object' is useless here — it already did."""
    from agentd.chat.controller_loop import _parse_failure_guidance

    g = _parse_failure_guidance(
        "output is not valid JSON (Invalid control character at: line 1 column 823)")

    assert "\\n" in g, g
    assert "newline" in g.lower(), g


def test_extra_data_error_keeps_the_single_object_guidance():
    """This one the generic text already got right — trailing prose after the
    object. Verified live: the model recovered on the next call."""
    from agentd.chat.controller_loop import _parse_failure_guidance

    g = _parse_failure_guidance("output is not valid JSON (Extra data: line 2 column 1)")

    assert "one" in g.lower() and "json object" in g.lower(), g


def test_unterminated_string_error_points_at_the_quote():
    """The escape-corruption signature. Distinct advice from a control character:
    a missing/unescaped closing quote, not a raw newline."""
    from agentd.chat.controller_loop import _parse_failure_guidance

    g = _parse_failure_guidance(
        "output is not valid JSON (Unterminated string starting at: line 1 column 5)")

    assert '\\"' in g, g


def test_unrecognized_parse_error_falls_back_to_the_generic_text():
    """An unmatched error must still say something actionable, including the
    truncation hint the original text carried."""
    from agentd.chat.controller_loop import _parse_failure_guidance

    g = _parse_failure_guidance("some totally novel decoder complaint")

    assert "json object" in g.lower(), g
    assert "shorter" in g.lower(), g


def test_extra_data_guidance_targets_failure_to_stop_not_prose():
    """Live evidence (4 consecutive failures, 19K-30K char payloads): after closing
    its object the model kept generating the CONVERSATION — '"}, {"role": "tool"…',
    '], "thought": …'. Not prose, not markdown fences, not over-closed brackets: it
    simply did not stop. Telling it 'no prose, no fences' describes none of that."""
    from agentd.chat.controller_loop import _parse_failure_guidance

    g = _parse_failure_guidance(
        "output is not valid JSON (Extra data: line 1 column 20878 (char 20877))")

    assert "stop" in g.lower(), g
    assert "closing brace" in g.lower() or "closing }" in g, g


def test_invalid_escape_error_explains_backslash_doubling():
    """Live: 'Invalid \\escape' while writing a Python file — a lone backslash inside
    the string value. Distinct from a raw control character: the model DID escape its
    newlines, but the FILE CONTENT itself contains backslashes (a \\n inside a Python
    string literal, a regex, a Windows path) which must be doubled for the JSON
    envelope. Fell through to the generic fallback before this."""
    from agentd.chat.controller_loop import _parse_failure_guidance

    g = _parse_failure_guidance(
        "output is not valid JSON (Invalid \\escape: line 1 column 5506 (char 5505))")

    assert "\\\\" in g, g
    assert "backslash" in g.lower(), g


@pytest.mark.asyncio
async def test_loop_tells_the_model_when_a_trailing_action_was_discarded(tmp_path: Path):
    """Salvaging the first object keeps the turn alive, but the model must LEARN that
    its second action never ran — otherwise its history shows only the first result and
    it can believe both happened. Live shape: write_todos + edit in one reply; the todos
    executed, the edit silently vanished."""
    real = tmp_path / "ws"
    real.mkdir()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    calls = {"n": 0}

    class _SalvagingEngine:
        async def create_controller_step(self, plan_context, history, tool_definitions,
                                         *, phase, on_thinking=None, on_retry=None,
                                         on_progress=None, on_salvage=None,
                                         on_usage=None, unconstrained=False):
            calls["n"] += 1
            if calls["n"] == 1 and on_salvage is not None:
                on_salvage(52, '{"type":"edit","thought":"Creating game_loop.py"}')
                return {"type": "answer", "thought": "t", "answer": "first"}
            return {"type": "answer", "thought": "t", "answer": "done"}

    loop = ControllerLoop(_SalvagingEngine(), reg, EventBroadcaster(),
                          channel_id="c", phase_sm=ControllerPhaseSM())
    out = await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=4,
                         auto_accept_edits=True)

    notes = [str(h.get("content", "")) for h in (out.history or [])
             if "discard" in str(h.get("content", "")).lower()]
    assert notes, out.history
    assert "discarded" in notes[0].lower(), notes[0]
    assert "one action per response" in notes[0].lower(), notes[0]
    # Cause-neutral: trailing content may be a second action OR surplus brackets.
    # Asserting one cause would be wrong about half the time.
    assert "more than one action" not in notes[0].lower(), notes[0]


@pytest.mark.asyncio
async def test_syntax_preflight_failure_requests_one_unconstrained_retry(tmp_path: Path):
    """NIM's grammar silently corrupts escapes — `print("hello")` becomes
    `print("hello"}` — so the JSON is valid and the CODE is not. There is nothing to
    catch at the transport; preflight is the only detector. On a syntax rejection the
    loop asks for exactly ONE unconstrained call, then goes back to the grammar."""
    real = tmp_path / "ws"
    real.mkdir()
    (real / "f.py").write_text("x = 1\n")
    sess = TurnEditSession(
        turn_id="t1", real_path=real,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "sh"),
        patch_engine=PatchEngine())
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])
    seen: list[bool] = []
    steps = iter([
        # 1: an edit whose content does not parse -> preflight syntax rejection
        {"type": "edit", "thought": "t", "patch_ops": [
            {"op": "create_file", "file": "b.py",
             "content": "def f(: pass", "reason": "r"}]},
        {"type": "answer", "thought": "t", "answer": "recovered"},
        {"type": "answer", "thought": "t", "answer": "again"},
    ])

    class _RecordingEngine:
        async def create_controller_step(self, plan_context, history, tool_definitions,
                                         *, phase, on_thinking=None, on_retry=None,
                                         on_progress=None, on_salvage=None,
                                         on_usage=None, unconstrained=False):
            seen.append(unconstrained)
            return next(steps)

    loop = ControllerLoop(_RecordingEngine(), reg, EventBroadcaster(),
                          channel_id="c", phase_sm=ControllerPhaseSM(),
                          edit_session_factory=lambda: sess)
    await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=4,
                   auto_accept_edits=True)

    assert seen[0] is False, "the first call must use the grammar"
    assert seen[1] is True, "after a syntax rejection, retry unconstrained once"
    assert len(seen) < 3 or seen[2] is False, "and revert to the grammar after"


@pytest.mark.asyncio
async def test_token_progress_accumulates_across_the_whole_turn(tmp_path: Path):
    """A turn is many model calls. Reporting each call's own counts made the
    number reset to near-zero on every sub-turn — reading as progress going
    backwards, and sitting beside a turn-scoped elapsed timer that never did.
    The counts are cumulative for the turn, so they only ever climb."""
    real = tmp_path / "ws"
    real.mkdir()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])

    class _TwoCallEngine:
        """First call explores, second answers — two streams, one turn."""

        def __init__(self) -> None:
            self.calls = 0

        async def create_controller_step(self, plan_context, history, tool_definitions,
                                         *, phase, on_thinking=None, on_retry=None,
                                         on_progress=None, on_salvage=None,
                                         on_usage=None, unconstrained=False):
            self.calls += 1
            if self.calls == 1:
                if on_progress is not None:
                    on_progress(10, 0)
                    on_progress(30, 20)      # call 1 ends at 30 reasoning / 20 output
                return {"type": "tool_call", "thought": "t", "tool": "list_directory",
                        "args": {"path": "."}}
            if on_progress is not None:
                on_progress(5, 0)            # call 2 restarts its own counters at 5/0
                on_progress(7, 40)
            return {"type": "answer", "thought": "t", "answer": "done"}

    bc = EventBroadcaster()
    q = bc.subscribe("c")
    loop = ControllerLoop(_TwoCallEngine(), reg, bc, channel_id="c",
                          phase_sm=ControllerPhaseSM())
    await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=4,
                   auto_accept_edits=True)

    prog = [e["payload"] for e in _drain(q) if e["type"] == "token_progress"]
    assert len(prog) >= 4, prog

    thinking = [p["thinking"] for p in prog]
    output = [p["output"] for p in prog]
    assert thinking == sorted(thinking), f"reasoning went backwards: {thinking}"
    assert output == sorted(output), f"output went backwards: {output}"

    # Turn totals: reasoning 30 + 7, output 20 + 40.
    # Subset, not dict equality: the payload also carries `input`/`exact`, and this
    # test is about cross-call ACCUMULATION, not the payload's full shape.
    assert prog[-1]["thinking"] == 37, prog
    assert prog[-1]["output"] == 60, prog


@pytest.mark.asyncio
async def test_token_progress_carries_input_size_and_exactness(tmp_path: Path):
    """Two gaps the counter had, both of which made a live turn unreadable.

    (1) INPUT SIZE. During prefill no deltas exist, so the counter sat at zero for
    the entire wait — a 372k-token prompt against a 262k-window model looked
    identical to a hang. The prompt size is known before the first delta, so it is
    reported immediately and the silence becomes legible.

    (2) EXACTNESS. Live ticks are chars/4 estimates; only the final emit carries the
    provider's own usage. They rendered identically, so a user could not tell a
    moving guess from the settled number.
    """
    real = tmp_path / "ws"
    real.mkdir()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])

    class _ProgressEngine:
        async def create_controller_step(self, plan_context, history, tool_definitions,
                                         *, phase, on_thinking=None, on_retry=None,
                                         on_progress=None, on_salvage=None,
                                         on_usage=None, unconstrained=False):
            if on_progress is not None:
                on_progress(0, 0, input_n=372_000)              # prefill, nothing generated
                on_progress(2, 5, input_n=372_000)              # live estimate
                on_progress(2, 17, input_n=372_000, exact=True)  # provider usage
            return {"type": "answer", "thought": "t", "answer": "done"}

    bc = EventBroadcaster()
    q = bc.subscribe("c")
    loop = ControllerLoop(_ProgressEngine(), reg, bc, channel_id="c",
                          phase_sm=ControllerPhaseSM())
    await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=3,
                   auto_accept_edits=True)

    prog = [e for e in _drain(q) if e["type"] == "token_progress"]
    assert prog, "no token_progress events"

    # Input size is present from the very first tick — before any output exists.
    assert prog[0]["payload"]["input"] == 372_000, prog[0]
    assert prog[0]["payload"]["output"] == 0, prog[0]

    # Estimated vs exact is distinguishable.
    assert prog[1]["payload"]["exact"] is False, prog[1]
    assert prog[-1]["payload"]["exact"] is True, prog[-1]
    assert prog[-1]["payload"]["output"] == 17, prog[-1]


@pytest.mark.asyncio
async def test_token_progress_defaults_stay_backward_compatible(tmp_path: Path):
    """A transport that only passes the two positional counts must still work —
    `exact` defaults to False (an estimate) and `input` to None (unknown)."""
    real = tmp_path / "ws"
    real.mkdir()
    reg = AggregatingToolRegistry(
        [BuiltinToolSource(shadow_root=real, real_workspace_path=real)])

    class _OldEngine:
        async def create_controller_step(self, plan_context, history, tool_definitions,
                                         *, phase, on_thinking=None, on_retry=None,
                                         on_progress=None, on_salvage=None,
                                         on_usage=None, unconstrained=False):
            if on_progress is not None:
                on_progress(1, 2)
            return {"type": "answer", "thought": "t", "answer": "done"}

    bc = EventBroadcaster()
    q = bc.subscribe("c")
    loop = ControllerLoop(_OldEngine(), reg, bc, channel_id="c",
                          phase_sm=ControllerPhaseSM())
    await loop.run({"goal": "x", "workspace_path": str(real)}, max_iters=3,
                   auto_accept_edits=True)

    prog = [e for e in _drain(q) if e["type"] == "token_progress"]
    assert prog[-1]["payload"]["exact"] is False
    assert prog[-1]["payload"]["input"] is None
