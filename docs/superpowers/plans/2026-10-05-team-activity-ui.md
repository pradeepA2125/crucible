# Team Activity UI ("now" half) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record each team member's lifecycle (woke, took up, picked up, wrapped up) and render the team as a journey — a board with a spine, chapters, post cards, stance replies and lifecycle beats; member tabs with activation chapters; a transcript card with live member states.

**Architecture:** A per-team `team_activity` table (own sequence `aseq`, never rendered into member input) written through `TeamService.record` at the controller's wake/activate/drain/report/disband/reap sites and streamed on the team channel as `team_activity`. The routes and `/live` expose it; the host's `TeamViewManager` follows it with a second cursor. In the webview, two pure builders (`teams/journey.ts`, `teams/chapters.ts`) turn posts + activity (+ transcript) into render items; components only draw items.

**Tech Stack:** Python 3.13 / FastAPI / SQLite; TypeScript (editor-client + Zod, VS Code extension host, React webview with vitest + Testing Library, react-markdown).

**Spec:** `docs/superpowers/specs/2026-10-05-team-activity-ui-design.md` — the rows marked "now" in §3 (§4–§8, §10–§11). §9 is Phase 5 and out of scope. Visual reference: `.superpowers/brainstorm/teams-activity-ui/content/team-activity-ui.html` (gitignored; published at https://claude.ai/artifact/EZksWCmnHUo2YQicSTR2VK).

## Part index

| Part | Tasks | Area |
|---|---|---|
| A | 1–3 | Backend: activity store + service, controller writers, routes + `/live` |
| B | 4–5 | editor-client schemas/events; host `TeamViewManager` activity cursor |
| C | 6–10 | Webview: state + identity, journey builder, board, member tab, transcript card |
| D | 11 | Docs, full suites, live smoke |

## Global Constraints

- Activity is **never** rendered into a member's input: `render_delta`, `team_read` and `status_text` read only `team_posts`.
- `aseq` is per team, starts at 1, and is independent of post `seq` (post seq stays the delivery cursor and proposal id).
- Activity writes are best-effort: a failure logs a warning and never fails an activation, a post or a report.
- Event kinds now: `phase`, `woke`, `notified`, `took_up`, `picked_up`, `wrapped_up`, `capped`. `woke.cause` ∈ `kickoff · mention · team_mention · message · main_post · leftover`.
- `wrapped_up.report` is capped at 20 000 characters with a `\n… (truncated)` marker.
- The `"<label> finished (…)"` system posts and the wake-cap system post are removed; `"The team was disbanded."` stays a system post.
- `/live` teams carry only fields that change on events (never per model call); `teams` stays in `controller.ts` `lastLiveSignature`.
- Member palette (hues assigned by roster order): sky `#7dd3fc`, fuchsia `#f0abfc`, teal `#5eead4`, orange `#fdba74`, lime `#bef264`, rose `#fda4af`; `main` violet `#a78bfa` with `✦`; `system` grey `#62626e`. Never the semantic green/red/amber.
- Gap dividers between journey items more than **60 s** apart. Post bodies fold beyond ~6 lines. Messages filter **off** by default.
- Webview UI uses the existing design tokens (`--color-*`, `--accent-*`, `.surface-card`, `.accent-wash`); no `--color-bg-*` (they don't exist).
- Pytest: never `-q`, never piped; `--timeout=120`; redirect and check `$?`. Vitest runs under `perl -e 'alarm N; exec @ARGV'` (macOS has no `timeout`). Commits: `type(scope): short description` + the two attribution lines. Never push.

## Review Focus

1. **A wake caused by a direct message while the Messages filter is off** — the user still sees why the member woke (a standalone beat), never a wake with no visible cause. Pinned in Task 7 (`hidden DM wake becomes a standalone beat`).
2. **A member stopped by Disband** — its open chapter closes as `stopped`, not left "working" forever. Pinned in Task 2 (`test_disband_closes_open_chapters`).
3. **A backfill racing a live `team_activity` event** — no duplicate beats after a reload. Pinned in Task 5 (`activity backfill then follow skips aseq <= lastAseq`).
4. **A team created before this change** (no activity rows) — the board renders posts only, members get one chapter, the card falls back to row statuses; nothing throws. Pinned in Task 7 (`posts without activity`) and Task 9 (`transcript without dividers is one chapter`).
5. **A member changes its stance** (objects, later agrees) — the tally shows the latest stance with `(was ✗)`, and the later reply says `replaces #n`. Pinned in Task 7 (`changed stance`).

---

# Part A — Backend (`services/agentd-py`)

### Task 1: Activity storage and `TeamService.record`

**Files:**
- Modify: `agentd/teams/models.py` (`TeamActivity`, `ACTIVITY_KINDS`), `agentd/teams/store.py` (table + `append_activity`, `activity`, `latest_activity_for`), `agentd/teams/service.py` (`record`, `on_activity`, `render_delta_posts`)
- Test: `tests/test_team_activity_store.py`

**Interfaces:**
- Produces:
  - `teams/models.py`: `ACTIVITY_KINDS: frozenset[str]`, `WAKE_CAUSES: frozenset[str]`, `class TeamActivity(BaseModel)`: `team_id, aseq, at: datetime, label, kind, activation: int | None, cause_seq: int | None, payload: dict[str, Any]`
  - `TeamStore.append_activity(team_id, *, label, kind, activation=None, cause_seq=None, payload=None) -> TeamActivity`
  - `TeamStore.activity(team_id, *, since_aseq=0) -> list[TeamActivity]` (oldest first)
  - `TeamStore.latest_activity_for(team_id, label) -> TeamActivity | None`
  - `TeamService.__init__(..., on_activity: Callable[[TeamRecord, TeamActivity], None] = lambda _t, _a: None)`
  - `TeamService.record(team_id, label, kind, *, activation=None, cause_seq=None, payload=None) -> TeamActivity | None` — best-effort (returns `None` and logs on any exception)
  - `TeamService.render_delta_posts(team_id, label) -> tuple[str, int, list[TeamPost]]` — same text and top as `render_delta`, plus the posts the delta covers that were written by others (the `took_up`/`picked_up` list); `render_delta` becomes `text, top, _ = self.render_delta_posts(...); return text, top`

- [ ] **Step 1: Write the failing tests** (`tests/test_team_activity_store.py`)

```python
"""Team activity record (spec 2026-10-05 §4)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.service import AgentInfo, TeamService


def _setup(tmp_path: Path):
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    now = datetime.now(UTC)
    store.teams.create_team(TeamRecord(
        team_id="team-1", thread_id="t1", name="auth", goal="Add login", max_rounds=3,
        round_started_at=now, approval_gate=False, budget=160, created_turn_id="turn",
        checkpoint_seq=0, created_at=now))
    for label in ("alice", "bob"):
        store.teams.add_member(TeamMember(team_id="team-1", agent_id=f"a-{label}", label=label))
    seen: list[tuple[str, int]] = []
    svc = TeamService(store.teams, tmp_path, lambda aid: AgentInfo("x", "d", "idle"),
                      on_activity=lambda team, ev: seen.append((ev.kind, ev.aseq)))
    return store, svc, seen


def test_aseq_is_per_team_and_independent_of_post_seq(tmp_path: Path) -> None:
    store, svc, seen = _setup(tmp_path)
    store.teams.append_post("team-1", author="main", kind="proposal", text="P", round=0)
    a = svc.record("team-1", "alice", "woke", activation=1, cause_seq=1,
                   payload={"cause": "kickoff", "post_seq": 1})
    b = svc.record("team-1", "bob", "woke", activation=1, cause_seq=1,
                   payload={"cause": "kickoff", "post_seq": 1})
    assert (a.aseq, b.aseq) == (1, 2)
    assert store.teams.append_post("team-1", author="alice", kind="post", text="x").seq == 2
    assert [e.aseq for e in store.teams.activity("team-1")] == [1, 2]
    assert [e.aseq for e in store.teams.activity("team-1", since_aseq=1)] == [2]
    assert seen == [("woke", 1), ("woke", 2)]
    got = store.teams.activity("team-1")[0]
    assert (got.label, got.activation, got.cause_seq, got.payload["cause"]) == (
        "alice", 1, 1, "kickoff")


def test_latest_activity_for_member(tmp_path: Path) -> None:
    store, svc, _ = _setup(tmp_path)
    svc.record("team-1", "alice", "woke", activation=1, payload={"cause": "kickoff"})
    svc.record("team-1", "alice", "took_up", activation=1, payload={"posts": [1], "from": ["main"]})
    svc.record("team-1", "bob", "woke", activation=1, payload={"cause": "kickoff"})
    assert store.teams.latest_activity_for("team-1", "alice").kind == "took_up"
    assert store.teams.latest_activity_for("team-1", "carol") is None


def test_record_is_best_effort(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store, svc, _ = _setup(tmp_path)
    store.teams._conn.execute("DROP TABLE team_activity")
    assert svc.record("team-1", "alice", "woke", payload={"cause": "kickoff"}) is None
    assert "activity" in caplog.text


def test_unknown_kind_is_refused(tmp_path: Path) -> None:
    store, svc, _ = _setup(tmp_path)
    with pytest.raises(ValueError, match="unknown activity kind"):
        store.teams.append_activity("team-1", label="alice", kind="danced")


def test_render_delta_posts_lists_covered_posts_by_others(tmp_path: Path) -> None:
    store, svc, _ = _setup(tmp_path)
    store.teams.append_post("team-1", author="main", kind="proposal", text="P", round=0)
    store.teams.append_post("team-1", author="alice", kind="post", text="mine")
    store.teams.append_post("team-1", author="bob", kind="post", text="@alice hi",
                            mentions=["alice"])
    text, top, posts = svc.render_delta_posts("team-1", "alice")
    assert top == 3
    assert [p.seq for p in posts] == [1, 3]           # her own post is not "handed" to her
    assert "@alice hi" in text
    assert svc.render_delta("team-1", "alice") == (text, top)


def test_activity_never_reaches_member_input(tmp_path: Path) -> None:
    store, svc, _ = _setup(tmp_path)
    store.teams.append_post("team-1", author="main", kind="proposal", text="P", round=0)
    svc.record("team-1", "bob", "wrapped_up", activation=1,
               payload={"status": "completed", "report": "SECRET-REPORT-TEXT"})
    text, _, _ = svc.render_delta_posts("team-1", "alice")
    assert "SECRET-REPORT-TEXT" not in text
    assert "SECRET-REPORT-TEXT" not in svc.status_text("team-1", "alice")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_activity_store.py --color=no --timeout=120 > /tmp/a1.txt 2>&1; echo exit=$?; tail -5 /tmp/a1.txt`
Expected: exit≠0 (`TeamService.__init__() got an unexpected keyword argument 'on_activity'`).

- [ ] **Step 3: Models** (`agentd/teams/models.py`, after `TeamPost`)

```python
# Spec 2026-10-05 §4.2 — the lifecycle facts the UI renders; never member input.
ACTIVITY_KINDS = frozenset({
    "phase", "woke", "notified", "took_up", "picked_up", "wrapped_up", "capped"})
WAKE_CAUSES = frozenset({
    "kickoff", "mention", "team_mention", "message", "main_post", "leftover"})


class TeamActivity(BaseModel):
    team_id: str
    aseq: int
    at: datetime
    label: str          # member label, or "main" / "team" for team-level events
    kind: str
    activation: int | None = None
    cause_seq: int | None = None   # the board post that caused it, when one did
    payload: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Store** (`agentd/teams/store.py`)

Import `ACTIVITY_KINDS, TeamActivity` from `agentd.teams.models`. In `_migrate`, before `self._conn.commit()`:

```python
        # Spec 2026-10-05 §4.1: the lifecycle record. Its own per-team sequence (aseq) so
        # post seq stays the delivery cursor and the proposal id.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS team_activity (
                team_id TEXT NOT NULL, aseq INTEGER NOT NULL, at TEXT NOT NULL,
                label TEXT NOT NULL, kind TEXT NOT NULL, activation INTEGER,
                cause_seq INTEGER, payload_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (team_id, aseq))""")
```

New section after the posts methods:

```python
    # ── activity (spec 2026-10-05 §4) ──────────────────────────────────────

    def append_activity(
        self, team_id: str, *, label: str, kind: str, activation: int | None = None,
        cause_seq: int | None = None, payload: dict[str, Any] | None = None,
    ) -> TeamActivity:
        if kind not in ACTIVITY_KINDS:
            raise ValueError(f"unknown activity kind {kind!r}")
        row = self._conn.execute(
            "SELECT COALESCE(MAX(aseq), 0) + 1 FROM team_activity WHERE team_id = ?",
            (team_id,)).fetchone()
        event = TeamActivity(team_id=team_id, aseq=int(row[0]), at=datetime.now(UTC),
                             label=label, kind=kind, activation=activation,
                             cause_seq=cause_seq, payload=payload or {})
        self._conn.execute(
            "INSERT INTO team_activity (team_id, aseq, at, label, kind, activation, "
            "cause_seq, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (team_id, event.aseq, event.at.isoformat(), label, kind, activation, cause_seq,
             json.dumps(event.payload)))
        self._conn.commit()
        return event

    @staticmethod
    def _activity_from_row(row: sqlite3.Row) -> TeamActivity:
        return TeamActivity(
            team_id=row["team_id"], aseq=row["aseq"], at=datetime.fromisoformat(row["at"]),
            label=row["label"], kind=row["kind"], activation=row["activation"],
            cause_seq=row["cause_seq"], payload=json.loads(row["payload_json"]))

    def activity(self, team_id: str, *, since_aseq: int = 0) -> list[TeamActivity]:
        rows = self._conn.execute(
            "SELECT * FROM team_activity WHERE team_id = ? AND aseq > ? ORDER BY aseq",
            (team_id, since_aseq)).fetchall()
        return [self._activity_from_row(r) for r in rows]

    def latest_activity_for(self, team_id: str, label: str) -> TeamActivity | None:
        row = self._conn.execute(
            "SELECT * FROM team_activity WHERE team_id = ? AND label = ? "
            "ORDER BY aseq DESC LIMIT 1", (team_id, label)).fetchone()
        return self._activity_from_row(row) if row else None
```

(`self._conn.row_factory` is `sqlite3.Row` — `posts()` already indexes rows by name; confirm with `grep -n row_factory agentd/chat/storage.py` and use the same access style as `_post_from_row`.)

- [ ] **Step 5: Service** (`agentd/teams/service.py`)

Imports: `import logging`, `TeamActivity` from `agentd.teams.models`; `logger = logging.getLogger(__name__)` at module level if absent. Constructor:

```python
    def __init__(
        self, store: TeamStore, workspace: Path, agent_info: Callable[[str], AgentInfo],
        on_post: Callable[[TeamRecord, TeamPost], None] = lambda _t, _p: None,
        on_activity: Callable[[TeamRecord, TeamActivity], None] = lambda _t, _a: None,
    ) -> None:
        self._store = store
        self._workspace = workspace
        self._agent_info = agent_info
        self._on_post = on_post
        self._on_activity = on_activity
```

After `system_post`:

```python
    def record(
        self, team_id: str, label: str, kind: str, *, activation: int | None = None,
        cause_seq: int | None = None, payload: dict[str, Any] | None = None,
    ) -> TeamActivity | None:
        """Append one activity event and stream it (spec 2026-10-05 §4.3). Best-effort: the
        record is for the user's eyes; it must never fail the work it describes."""
        try:
            event = self._store.append_activity(
                team_id, label=label, kind=kind, activation=activation,
                cause_seq=cause_seq, payload=payload)
            team = self._store.get_team(team_id)
            if team is not None:
                self._on_activity(team, event)
            return event
        except Exception:  # noqa: BLE001 — best-effort by design (spec §10)
            logger.warning("[teams] could not record %s activity for %s in %s", kind, label,
                           team_id, exc_info=True)
            return None
```

Replace `render_delta` with:

```python
    def render_delta_posts(self, team_id: str, label: str) -> tuple[str, int, list[TeamPost]]:
        """The member's inbox delta, its top seq, and the posts by others it covers — what
        the member is 'handed' (the took_up / picked_up record, spec 2026-10-05 §4.2)."""
        team = self._store.get_team(team_id)
        member = self._store.member(team_id, label)
        assert team is not None and member is not None
        visible = self._store.posts(team_id, since_seq=member.delivered_seq, viewer=label)
        top = max((p.seq for p in visible), default=member.delivered_seq)
        others = [p for p in visible if p.author != label]
        body = "\n\n".join(self._render_post(team_id, p, label) for p in others)
        header = self._header(team, first=member.delivered_seq == 0)
        text = f"{header}\n\n{body}" if body else f"{header}\n\nNo new posts."
        return text, top, others

    def render_delta(self, team_id: str, label: str) -> tuple[str, int]:
        text, top, _ = self.render_delta_posts(team_id, label)
        return text, top
```

(`Any` is already imported in service.py if `summary` uses it; otherwise add `from typing import Any`.)

- [ ] **Step 6: Run and commit**

Run: `.venv/bin/pytest tests/test_team_activity_store.py tests/test_team_service.py tests/test_team_store.py --color=no --timeout=120 > /tmp/a1.txt 2>&1; echo exit=$?; tail -3 /tmp/a1.txt`
Expected: `exit=0`.

```bash
git add agentd/teams/models.py agentd/teams/store.py agentd/teams/service.py tests/test_team_activity_store.py
git commit -m "feat(teams): activity record with its own sequence, TeamService.record"
```

### Task 2: Controller writers — wakes, hand-offs, wrap-ups, phases

**Files:**
- Modify: `agentd/chat/controller.py`
- Modify: `tests/test_team_controller.py` (the `finished` system-post assertions become activity assertions)
- Test: `tests/test_team_activity_controller.py`

**Interfaces:**
- Consumes: Task 1 (`TeamService.record`, `render_delta_posts`, `TeamStore.activity`).
- Produces:
  - `ChatController._team_activity(team: TeamRecord, event: TeamActivity) -> None` — broadcasts `{"type": "team_activity", "aseq": event.aseq, "payload": {"event": event.model_dump(mode="json")}}` on `team_channel(...)`
  - `wake_cause(post: TeamPost, label: str) -> str` (module function): `message` if `post.recipient == label`; `main_post` if `post.author == "main"`; `team_mention` if `"team" in post.mentions`; else `mention`
  - `_activation_stats(record: AgentRecord, posts: list[TeamPost]) -> dict[str, object]` (module function): `{duration_ms, tools, posts, messages, stances}` for the record's current activation
  - divider messages carry `metadata.activation` (for activation > 1; activation 1 needs no divider — its chapter is everything before the first one)
  - Event writes at: `_create_team` (`phase`), `_team_posted`/`_wake_member` (`woke`/`notified`/`capped`, kickoff cause on `wake_all`), `_on_leftover` (`woke` leftover), `_activate` (`took_up`), `_drain_member` (`picked_up`), `_team_member_reported` (`wrapped_up`), `disband_team` (`phase` DISBANDED), `reap_subagents` (`wrapped_up` failed per live member + `phase` FAILED)

- [ ] **Step 1: Write the failing tests** (`tests/test_team_activity_controller.py`)

```python
"""The controller writes the activity record (spec 2026-10-05 §4.3)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.test_team_controller import REPORT, _make, _request, _settle

POST_TO_BOB = {"type": "tool_call", "thought": "tell bob", "tool": "team_post",
               "args": {"text": "@bob the login route needs a test"}}
DM_TO_BOB = {"type": "tool_call", "thought": "ask bob", "tool": "team_message",
             "args": {"member": "bob", "text": "which file?"}}


def _kinds(store, team_id: str, label: str | None = None) -> list[str]:
    return [e.kind for e in store.teams.activity(team_id) if label in (None, e.label)]


@pytest.mark.asyncio
async def test_kickoff_records_phase_wakes_handoffs_and_wrapups(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    events = store.teams.activity(team_id)
    assert events[0].kind == "phase" and events[0].payload["phase"] == "DELIBERATING"
    for label in ("alice", "bob"):
        assert _kinds(store, team_id, label) == ["woke", "took_up", "wrapped_up"]
    woke = next(e for e in events if e.kind == "woke" and e.label == "alice")
    assert (woke.payload["cause"], woke.cause_seq, woke.activation) == ("kickoff", 1, 1)
    took = next(e for e in events if e.kind == "took_up" and e.label == "alice")
    assert took.payload["posts"] == [1] and took.payload["from"] == ["main"]
    wrap = next(e for e in events if e.kind == "wrapped_up" and e.label == "alice")
    assert wrap.payload["status"] == "completed" and wrap.payload["report"] == "done here"
    assert {"duration_ms", "tools", "posts", "messages", "stances"} <= set(wrap.payload)
    # The "finished" system posts are gone: the board holds only the kickoff.
    assert [p.kind for p in store.teams.posts(team_id)] == ["proposal"]


@pytest.mark.asyncio
async def test_mention_records_a_caused_wake(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                                {"alice": [POST_TO_BOB, REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    woke = [e for e in store.teams.activity(team_id) if e.kind == "woke" and e.label == "bob"]
    assert len(woke) == 1
    assert woke[0].payload["cause"] == "mention" and woke[0].payload["by"] == "alice"
    assert woke[0].cause_seq == 2 and woke[0].activation == 1


@pytest.mark.asyncio
async def test_direct_message_cause(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                                {"alice": [DM_TO_BOB, REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    woke = next(e for e in store.teams.activity(team_id) if e.kind == "woke" and e.label == "bob")
    assert woke.payload["cause"] == "message"


@pytest.mark.asyncio
async def test_running_member_is_notified_then_picks_up(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    bob = store.teams.member(team_id, "bob")
    monkeypatch.setattr(ctrl._subagents, "is_active", lambda agent_id: agent_id == bob.agent_id)
    ctrl._teams.post(team_id, "alice", "@bob new finding")
    ctrl._drain_member(bob.agent_id, team_id, "bob")
    kinds = _kinds(store, team_id, "bob")
    assert kinds[-2:] == ["notified", "picked_up"]
    picked = store.teams.activity(team_id)[-1]
    assert picked.payload["posts"] == [store.teams.posts(team_id)[-1].seq]


@pytest.mark.asyncio
async def test_wake_cap_is_an_event_not_a_post(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_MAX_WAKES", "1")
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request(kind="post")))["team_id"])
    await _settle(ctrl)
    ctrl._teams.post(team_id, "bob", "@alice again")
    await _settle(ctrl)
    capped = [e for e in store.teams.activity(team_id) if e.kind == "capped"]
    assert len(capped) == 1 and capped[0].payload == {"wakes": 2, "cap": 1}
    assert not [p for p in store.teams.posts(team_id) if p.kind == "system"]


@pytest.mark.asyncio
async def test_divider_carries_activation(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch,
                                {"alice": [POST_TO_BOB, REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    bob = store.get_agent(store.teams.member(team_id, "bob").agent_id)
    dividers = [m.metadata.get("activation") for m in bob.transcript if m.metadata.get("divider")]
    assert dividers == [2]


@pytest.mark.asyncio
async def test_disband_closes_open_chapters(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    original = engine.create_controller_step

    async def slow(*args, **kwargs):  # type: ignore[no-untyped-def]
        await asyncio.sleep(5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_controller_step", slow)
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await asyncio.sleep(0.05)                      # both activations are now running
    await ctrl.disband_team(tid, team_id)
    await _settle(ctrl)
    wraps = {e.label: e.payload["status"] for e in store.teams.activity(team_id)
             if e.kind == "wrapped_up"}
    assert wraps == {"alice": "stopped", "bob": "stopped"}
    assert any(e.kind == "phase" and e.payload["phase"] == "DISBANDED"
               for e in store.teams.activity(team_id))


@pytest.mark.asyncio
async def test_reap_closes_live_members_and_fails_the_team(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    team_id = str((await ctrl._create_team(tid, "turn1", _request()))["team_id"])
    await _settle(ctrl)
    ctrl.reap_subagents()
    tail = store.teams.activity(team_id)
    assert tail[-1].kind == "phase" and tail[-1].payload["phase"] == "FAILED"


@pytest.mark.asyncio
async def test_activity_is_streamed_on_the_team_channel(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    sent: list[dict] = []
    original = ctrl._broadcaster.broadcast
    monkeypatch.setattr(ctrl._broadcaster, "broadcast",
                        lambda ch, ev: (sent.append(ev), original(ch, ev))[1])
    await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    acts = [e for e in sent if e.get("type") == "team_activity"]
    assert acts and [e["aseq"] for e in acts] == sorted(e["aseq"] for e in acts)
    assert acts[0]["payload"]["event"]["kind"] == "phase"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_activity_controller.py --color=no --timeout=120 > /tmp/a2.txt 2>&1; echo exit=$?; tail -8 /tmp/a2.txt`
Expected: exit≠0 (no `phase` event; the `finished` system posts still exist).

- [ ] **Step 3: Construction and broadcast** (`controller.py`)

`TeamService(...)` in `__init__` gains `on_activity=self._team_activity`. Import `TeamActivity` with the other `agentd.teams.models` names. Add next to `_team_posted`:

```python
    def _team_activity(self, team: TeamRecord, event: TeamActivity) -> None:
        """Every activity event streams on the team channel (spec 2026-10-05 §5.2)."""
        self._broadcaster.broadcast(team_channel(team.thread_id, team.team_id), {
            "type": "team_activity", "aseq": event.aseq,
            "payload": {"event": event.model_dump(mode="json")}})
```

Module functions, after `team_channel`:

```python
def wake_cause(post: TeamPost, label: str) -> str:
    """Why `label` is concerned by `post` (spec 2026-10-05 §4.2)."""
    if post.recipient == label:
        return "message"
    if post.author == "main":
        return "main_post"
    if "team" in post.mentions:
        return "team_mention"
    return "mention"


def _activation_stats(record: AgentRecord, posts: list[TeamPost]) -> dict[str, object]:
    """This activation's counts for its wrap-up row: tool calls since the last divider,
    and the member's posts / messages / stances since the activation started."""
    started = record.activation_started_at
    tools = 0
    for message in reversed(record.transcript):
        if message.metadata.get("divider"):
            break
        events = message.metadata.get("tool_events")
        if isinstance(events, list):
            tools += len(events)
    mine = [p for p in posts if p.author == record.label
            and (started is None or p.created_at >= started)]
    duration_ms = (int((datetime.now(UTC) - started).total_seconds() * 1000)
                   if started is not None else 0)
    return {
        "duration_ms": duration_ms, "tools": tools,
        "posts": sum(1 for p in mine if p.kind in ("post", "proposal") and p.recipient is None),
        "messages": sum(1 for p in mine if p.recipient is not None),
        "stances": sum(1 for p in mine if p.kind in ("agree", "object")),
    }
```

- [ ] **Step 4: Phase events, wakes, notices, cap**

In `_create_team`, right after `self._store.teams.create_team(team)`:

```python
        self._teams.record(team.team_id, "team", "phase",
                           payload={"phase": team.phase, "round": team.round})
```

Replace `_team_posted`'s wake loop and `_wake_member`:

```python
        for member in targets:
            if member.label != post.author:
                cause = "kickoff" if wake_all else wake_cause(post, member.label)
                self._wake_member(team, member, cause=cause, post=post)

    def _wake_member(self, team: TeamRecord, member: TeamMember, *, cause: str,
                     post: TeamPost | None) -> None:
        assert self._subagents is not None and self._teams is not None
        record = self._store.get_agent(member.agent_id)
        next_activation = (record.activation_count + 1) if record is not None else 1
        by = post.author if post is not None else None
        post_seq = post.seq if post is not None else None
        if self._subagents.is_active(member.agent_id):
            # Running: a marker; the next drain renders whatever is new by then (§3.6).
            self._subagents.deliver(member.agent_id, InboxItem(
                kind="team", text="", wakes=True, source_id=f"team:{team.team_id}",
                author="team board"))
            self._teams.record(team.team_id, member.label, "notified",
                               activation=next_activation - 1, cause_seq=post_seq,
                               payload={"cause": cause, "by": by, "post_seq": post_seq})
            return
        cap = team_max_wakes()
        wakes = self._store.teams.bump_wakes(team.team_id, member.label)
        if wakes > cap:
            if wakes == cap + 1:
                self._teams.record(team.team_id, member.label, "capped",
                                   cause_seq=post_seq, payload={"wakes": wakes, "cap": cap})
            return
        self._teams.record(team.team_id, member.label, "woke", activation=next_activation,
                           cause_seq=post_seq,
                           payload={"cause": cause, "by": by, "post_seq": post_seq})
        handle = self._handle_from_record(team.thread_id, member.agent_id)
        handle.activation_input = TEAM_DELTA
        self._subagents.enqueue(handle, self._activate)
```

(`notified` belongs to the running activation, hence `next_activation - 1`.)

`_on_leftover`, inside the `if self._store.teams.member_for_agent(...)` branch:

```python
        membership = self._store.teams.member_for_agent(handle.agent_id)
        if membership is not None:
            text = TEAM_DELTA + ("\n\n" + text if text else "")
            if self._teams is not None:
                record = self._store.get_agent(handle.agent_id)
                self._teams.record(
                    membership.team_id, membership.label, "woke",
                    activation=(record.activation_count + 1) if record is not None else None,
                    payload={"cause": "leftover"})
```

(replace the existing `if self._store.teams.member_for_agent(handle.agent_id) is not None:` line and body with the above).

- [ ] **Step 5: Hand-offs and dividers**

In `_activate`'s member branch, replace the `render_delta` call:

```python
            delta, top, handed = self._teams.render_delta_posts(
                membership.team_id, membership.label)
            self._store.teams.set_delivered_seq(membership.team_id, membership.label, top)
            activation_input = delta + activation_input[len(TEAM_DELTA):]
            self._teams.record(membership.team_id, membership.label, "took_up",
                               activation=activation,
                               payload={"posts": [p.seq for p in handed],
                                        "from": sorted({p.author for p in handed})})
```

`activation` is computed above this point (`activation = record.activation_count + 1`); move the member branch below that line if it currently sits above it. The divider append becomes:

```python
            transcript.append(ChatMessage(
                role="agent", content=_divider_text(activation_input),
                metadata={"divider": True, "activation": activation}))
```

In `_drain_member`, replace the `render_delta` lines:

```python
        member = self._store.teams.member(team_id, label)
        delta, top, handed = self._teams.render_delta_posts(team_id, label)
        if member is None or top <= member.delivered_seq:
            return others
        self._store.teams.set_delivered_seq(team_id, label, top)
        record = self._store.get_agent(agent_id)
        self._teams.record(team_id, label, "picked_up",
                           activation=record.activation_count if record is not None else None,
                           payload={"posts": [p.seq for p in handed],
                                    "from": sorted({p.author for p in handed})})
        return [InboxItem(kind="team", text=delta, wakes=False, author="team board"), *others]
```

- [ ] **Step 6: Wrap-ups, disband, reap**

Replace `_team_member_reported`:

```python
    def _team_member_reported(self, record: AgentRecord, result: ChildResult) -> None:
        """A member's activation ended: its wrap-up (spec 2026-10-05 §4.2). Written even
        after the team ended, so a disband's stopped activations close their chapters."""
        if self._teams is None or record.team_id is None:
            return
        report = result.report
        if len(report) > 20_000:
            report = report[:20_000] + "\n… (truncated)"
        stats = _activation_stats(record, self._store.teams.posts(record.team_id))
        self._teams.record(record.team_id, record.label, "wrapped_up",
                           activation=record.activation_count,
                           payload={"status": result.status, "report": report,
                                    "files_changed": list(result.files_changed), **stats})
```

In `disband_team`, right after the `system_post(team_id, "The team was disbanded.")` line:

```python
            self._teams.record(team_id, "team", "phase",
                               payload={"phase": "DISBANDED", "round": team.round,
                                        "reason": "disbanded"})
```

In `reap_subagents`, after `failed_teams = self._store.teams.fail_live_teams(...)`. `reap_agents` runs first and marks every live row `failed`, so row fields cannot tell which members were mid-activation; a member whose **latest** activity event is not `wrapped_up` was:

```python
        if self._teams is not None:
            for team_id in failed_teams:
                for member in self._store.teams.members(team_id):
                    last = self._store.teams.latest_activity_for(team_id, member.label)
                    if last is not None and last.kind != "wrapped_up":
                        self._teams.record(team_id, member.label, "wrapped_up",
                                           activation=last.activation,
                                           payload={"status": "failed", "report": "",
                                                    "reason": "backend restarted"})
                self._teams.record(team_id, "team", "phase",
                                   payload={"phase": "FAILED", "reason": "backend restarted"})
```

Finally, in `tests/test_team_controller.py`, `test_create_team_rows_kickoff_and_activation` asserted the `finished` system posts; replace that assertion with:

```python
    assert {e.label: e.payload["status"] for e in store.teams.activity(team_id)
            if e.kind == "wrapped_up"} == {"alice": "completed", "bob": "awaiting_peer"}
```

and `test_wake_cap_stops_ping_pong` now checks the `capped` event:

```python
    capped = [e for e in store.teams.activity(team_id) if e.kind == "capped"]
    assert capped and capped[0].label == "alice"
```

- [ ] **Step 7: Run and commit**

Run: `.venv/bin/pytest tests/test_team_activity_controller.py tests/test_team_controller.py tests/test_team_routes.py tests/test_agent_activation.py tests/test_subagent_lifecycle.py --color=no --timeout=120 > /tmp/a2.txt 2>&1; echo exit=$?; tail -5 /tmp/a2.txt`
Expected: `exit=0`.

```bash
git add agentd/chat/controller.py tests/test_team_activity_controller.py tests/test_team_controller.py
git commit -m "feat(teams): record wakes, hand-offs, wrap-ups and phases; drop finished posts"
```

### Task 3: Routes and `/live` — activity backfill, member state, latest, counts

**Files:**
- Modify: `agentd/chat/controller.py` (`team_detail`, `live_teams`), `agentd/teams/service.py` (`summary` members)
- Modify: `tests/test_team_routes.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - `GET …/teams/{team_id}` adds `activity: [TeamActivity json…]`, `last_aseq: int`
  - `TeamService.summary` members add `name` (definition name) and `description` (one line) — the member tab's profile header needs them and the webview has no other source
  - `/live` `teams[]` adds `members[].last: {kind, at, cause_seq, by, status, activation} | None`, `latest: {kind: "post" | "activity", label, text, at} | None`, `counts: {posts: int, proposals: [{id, agree, object, pending}]}`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_team_routes.py`)

```python
@pytest.mark.asyncio
async def test_detail_carries_activity_and_live_carries_state(tmp_path: Path, monkeypatch) -> None:
    store, ctrl, tid, _ = _seed(tmp_path, monkeypatch)
    ctrl._teams.record("team-1", "alice", "woke", activation=1, cause_seq=1,
                       payload={"cause": "kickoff", "by": "main", "post_seq": 1})
    ctrl._teams.record("team-1", "alice", "wrapped_up", activation=1,
                       payload={"status": "completed", "report": "all good"})
    store.teams.append_post("team-1", author="bob", kind="agree", text="ok", ref_id="P1")
    async with _client(tmp_path, ctrl) as client:
        detail = (await client.get(f"/v1/chat/threads/{tid}/teams/team-1")).json()
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
    assert [e["kind"] for e in detail["activity"]] == ["woke", "wrapped_up"]
    assert detail["last_aseq"] == 2
    team = live["teams"][0]
    alice = next(m for m in team["members"] if m["label"] == "alice")
    assert alice["last"]["kind"] == "wrapped_up" and alice["last"]["status"] == "completed"
    bob = next(m for m in team["members"] if m["label"] == "bob")
    assert bob["last"] is None
    assert team["latest"]["kind"] == "post" and team["latest"]["label"] == "bob"
    assert team["counts"]["posts"] == 3
    assert team["counts"]["proposals"] == [{"id": "P1", "agree": 1, "object": 0, "pending": 1}]
    assert {m["label"]: m["name"] for m in detail["members"]} == {
        "alice": "general-purpose", "bob": "general-purpose"}
```

(`_seed` writes two posts: the kickoff proposal by `main` and a DM; the agree above is the third. `pending` counts members with no stance; the proposal's author `main` is not a member.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_routes.py --color=no --timeout=120 > /tmp/a3.txt 2>&1; echo exit=$?; tail -5 /tmp/a3.txt`
Expected: exit≠0 (`KeyError: 'activity'`).

- [ ] **Step 3: Implement**

`agentd/teams/service.py` `summary`, the members list:

```python
            "members": [{"label": m.label, "agent_id": m.agent_id,
                         "name": (info := self._agent_info(m.agent_id)).name,
                         "description": info.description, "status": info.status}
                        for m in self._store.members(team_id)],
```

`controller.py` — `team_detail` gains, before the return:

```python
        activity = self._store.teams.activity(team_id)
```

and the returned dict adds:

```python
                "activity": [e.model_dump(mode="json") for e in activity],
                "last_aseq": max((e.aseq for e in activity), default=0),
```

`live_teams`'s per-team dict becomes:

```python
            members = self._store.teams.members(team.team_id)
            posts = self._store.teams.posts(team.team_id)
            out.append({
                "team_id": team.team_id, "name": team.name, "phase": team.phase,
                "round": team.round, "max_rounds": team.max_rounds,
                "paused_reason": team.paused_reason,
                "members": [{"label": m.label, "agent_id": m.agent_id,
                             "status": self._member_row_status(m.agent_id),
                             "last": _last_view(self._store.teams.latest_activity_for(
                                 team.team_id, m.label))}
                            for m in members],
                "latest": self._team_latest(team.team_id, posts),
                "counts": _team_counts(posts, [m.label for m in members]),
            })
```

Module helpers next to `wake_cause`:

```python
def _last_view(event: TeamActivity | None) -> dict[str, object] | None:
    """A member's latest event, slim — only what the card's state phrase needs."""
    if event is None:
        return None
    return {"kind": event.kind, "at": event.at.isoformat(), "cause_seq": event.cause_seq,
            "by": event.payload.get("by"), "status": event.payload.get("status"),
            "activation": event.activation}


def _team_counts(posts: list[TeamPost], labels: list[str]) -> dict[str, object]:
    proposals = []
    for p in posts:
        if p.kind != "proposal" or p.closed is not None:
            continue
        latest: dict[str, str] = {}
        for s in posts:
            if s.ref_id == p.proposal_id and s.kind in ("agree", "object"):
                latest[s.author] = s.kind
        agree = sum(1 for v in latest.values() if v == "agree")
        objected = sum(1 for v in latest.values() if v == "object")
        pending = sum(1 for label in labels if label not in latest and label != p.author)
        proposals.append({"id": p.proposal_id, "agree": agree, "object": objected,
                          "pending": pending})
    return {"posts": len(posts), "proposals": proposals}
```

Method:

```python
    def _team_latest(self, team_id: str, posts: list[TeamPost]) -> dict[str, object] | None:
        """The card's 'latest' line: the newer of the last board post and the last
        wrapped_up / phase event (spec 2026-10-05 §5.3)."""
        board = [p for p in posts if p.recipient is None]
        candidates: list[dict[str, object]] = []
        if board:
            p = board[-1]
            candidates.append({"kind": "post", "label": p.author, "text": p.text[:140],
                               "at": p.created_at.isoformat()})
        events = [e for e in self._store.teams.activity(team_id)
                  if e.kind in ("wrapped_up", "phase")]
        if events:
            e = events[-1]
            text = (str(e.payload.get("report", ""))[:140] if e.kind == "wrapped_up"
                    else str(e.payload.get("phase", "")))
            candidates.append({"kind": "activity", "label": e.label, "text": text,
                               "at": e.at.isoformat(), "event": e.kind,
                               "status": e.payload.get("status")})
        return max(candidates, key=lambda c: str(c["at"])) if candidates else None
```

Update the existing `/live` expectation in `test_list_detail_live_and_disband` (it compares the whole `teams` entry): add `"last": None` to both members and the `latest`/`counts` keys — or switch that assertion to check the old keys only:

```python
    entry = live["teams"][0]
    assert {k: entry[k] for k in ("team_id", "name", "phase", "round", "max_rounds",
                                  "paused_reason")} == {
        "team_id": "team-1", "name": "auth", "phase": "DELIBERATING", "round": 1,
        "max_rounds": 3, "paused_reason": None}
    assert [(m["label"], m["status"]) for m in entry["members"]] == [
        ("alice", "awaiting_peer"), ("bob", "awaiting_peer")]
```

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/pytest tests/test_team_routes.py tests/test_get_routes_read_only.py tests/test_team_activity_controller.py --color=no --timeout=120 > /tmp/a3.txt 2>&1; echo exit=$?; tail -3 /tmp/a3.txt`
Expected: `exit=0`.

```bash
git add agentd/chat/controller.py agentd/teams/service.py tests/test_team_routes.py
git commit -m "feat(teams): activity backfill on the team route; member state, latest and counts on /live"
```

---

# Part B — editor-client and extension host

### Task 4: editor-client — activity schema, event, detail and `/live` fields

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`
- Test: `apps/editor-client/test/team-activity-contracts.test.ts`

**Interfaces:**
- Consumes: Task 3's route and `/live` shapes; Task 2's `team_activity` event.
- Produces:
  - `TeamActivitySchema` / `TeamActivity`: `{teamId, aseq, at, label, kind, activation: number | null, causeSeq: number | null, payload: Record<string, unknown>}` (payload keys stay snake_case — same passthrough convention as gate payloads)
  - `TeamDetail` gains `activity: TeamActivity[]`, `lastAseq: number`
  - `TeamSummary.members[]` gains `name: string` and `description: string` (default `""`)
  - `TeamLive.members[]` gains `last: {kind, at, causeSeq: number | null, by: string | null, status: string | null, activation: number | null} | null`; `TeamLive` gains `latest: {kind: "post" | "activity", label, text, at, event?: string, status?: string | null} | null` and `counts: {posts: number, proposals: {id, agree, object, pending}[]}`
  - `StreamEvent` gains `{type: "team_activity"; payload: {event: Record<string, unknown>}}`
  - `parseWireTeamActivity(raw: Record<string, unknown>): TeamActivity`

- [ ] **Step 1: Write the failing test** (`test/team-activity-contracts.test.ts`)

```ts
import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient, parseWireTeamActivity } from "../src/client/http-backend-client";

const EVENT = {
  team_id: "team-1", aseq: 2, at: "2026-10-05T11:40:39+00:00", label: "alice", kind: "woke",
  activation: 1, cause_seq: 1, payload: { cause: "kickoff", by: "main", post_seq: 1 },
};
const TEAM = {
  team_id: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 1, max_rounds: 3,
  paused_reason: null, members: [], open_proposals: [], usage: { requests: 0, budget: 160 },
  created_at: "2026-10-05T00:00:00Z",
};

describe("team activity contracts", () => {
  it("parses a wire activity event", () => {
    expect(parseWireTeamActivity(EVENT)).toEqual({
      teamId: "team-1", aseq: 2, at: "2026-10-05T11:40:39+00:00", label: "alice",
      kind: "woke", activation: 1, causeSeq: 1,
      payload: { cause: "kickoff", by: "main", post_seq: 1 },
    });
  });

  it("maps getTeam activity and lastAseq", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ ...TEAM, posts: [], last_seq: 0, activity: [EVENT], last_aseq: 2 }),
    }) });
    const detail = await c.getTeam("t", "team-1");
    expect(detail.lastAseq).toBe(2);
    expect(detail.activity[0]).toMatchObject({ kind: "woke", causeSeq: 1 });
  });

  it("an old backend without activity parses as empty", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ ...TEAM, posts: [], last_seq: 0 }),
    }) });
    const detail = await c.getTeam("t", "team-1");
    expect(detail.activity).toEqual([]);
    expect(detail.lastAseq).toBe(0);
  });

  it("maps /live member last, latest and counts", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: vi.fn().mockResolvedValue({
      ok: true, json: async () => ({
        active_task_id: null, status: null, pending_gates: [], plan: null,
        teams: [{ team_id: "team-1", name: "auth", phase: "DELIBERATING", round: 1,
          max_rounds: 3, paused_reason: null,
          members: [{ label: "alice", agent_id: "a", status: "completed",
            last: { kind: "wrapped_up", at: "2026-10-05T11:45:00+00:00", cause_seq: null,
                    by: null, status: "completed", activation: 1 } },
            { label: "bob", agent_id: "b", status: "running", last: null }],
          latest: { kind: "post", label: "bob", text: "ok", at: "2026-10-05T11:46:00+00:00" },
          counts: { posts: 3, proposals: [{ id: "P1", agree: 1, object: 0, pending: 1 }] } }],
      }),
    }) });
    const team = (await c.getThreadLiveState("t")).teams![0];
    expect(team.members[0].last).toEqual({ kind: "wrapped_up", at: "2026-10-05T11:45:00+00:00",
      causeSeq: null, by: null, status: "completed", activation: 1 });
    expect(team.members[1].last).toBeNull();
    expect(team.latest).toMatchObject({ kind: "post", label: "bob" });
    expect(team.counts.proposals[0]).toEqual({ id: "P1", agree: 1, object: 0, pending: 1 });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (from `apps/editor-client`): `npx vitest run test/team-activity-contracts.test.ts > /tmp/b4.txt 2>&1; echo exit=$?; tail -12 /tmp/b4.txt`
Expected: exit≠0 (`parseWireTeamActivity` is not exported).

- [ ] **Step 3: Schemas** (`task-contracts.ts`, after `TeamPostSchema`)

```ts
// Spec 2026-10-05 §4: lifecycle facts for the journey view. Payload keys stay snake_case.
export const TeamActivitySchema = z.object({
  teamId: z.string(),
  aseq: z.number(),
  at: z.string(),
  label: z.string(),
  kind: z.string(),
  activation: z.number().nullable(),
  causeSeq: z.number().nullable(),
  payload: z.record(z.unknown()).default({}),
});
export type TeamActivity = z.infer<typeof TeamActivitySchema>;

const TeamMemberLastSchema = z.object({
  kind: z.string(),
  at: z.string(),
  causeSeq: z.number().nullable(),
  by: z.string().nullable(),
  status: z.string().nullable(),
  activation: z.number().nullable(),
});
```

`TeamDetailSchema` gains:

```ts
  activity: z.array(TeamActivitySchema).default([]),
  lastAseq: z.number().default(0),
```

`TeamSummarySchema`'s members become:

```ts
  members: z.array(z.object({
    label: z.string(), agentId: z.string(), status: z.string(),
    name: z.string().default(""), description: z.string().default(""),
  })),
```

and `toTeamSummary`'s member mapping adds `name: m["name"] ?? "", description: m["description"] ?? ""`.

`TeamLiveSchema` becomes:

```ts
export const TeamLiveSchema = z.object({
  teamId: z.string(),
  name: z.string(),
  phase: z.string(),
  round: z.number(),
  maxRounds: z.number(),
  pausedReason: z.string().nullable(),
  members: z.array(z.object({
    label: z.string(), agentId: z.string(), status: z.string(),
    last: TeamMemberLastSchema.nullable().default(null),
  })),
  latest: z.object({
    kind: z.enum(["post", "activity"]), label: z.string(), text: z.string(), at: z.string(),
    event: z.string().optional(), status: z.string().nullable().optional(),
  }).nullable().default(null),
  counts: z.object({
    posts: z.number(),
    proposals: z.array(z.object({ id: z.string(), agree: z.number(), object: z.number(),
                                  pending: z.number() })),
  }).default({ posts: 0, proposals: [] }),
});
```

`StreamEvent`, next to `team_post`:

```ts
  | { type: "team_activity"; payload: { event: Record<string, unknown> } }
```

- [ ] **Step 4: Client** (`http-backend-client.ts`)

Import `TeamActivitySchema`, `type TeamActivity`. Next to `toTeamPost`:

```ts
  static toTeamActivity(e: Record<string, unknown>): Record<string, unknown> {
    return {
      teamId: e["team_id"], aseq: e["aseq"], at: e["at"], label: e["label"], kind: e["kind"],
      activation: e["activation"] ?? null, causeSeq: e["cause_seq"] ?? null,
      payload: e["payload"] ?? {},
    };
  }
```

`getTeam` adds to the parsed object:

```ts
      activity: (Array.isArray(raw["activity"]) ? raw["activity"] as Record<string, unknown>[] : [])
        .map((e) => HttpBackendClient.toTeamActivity(e)),
      lastAseq: raw["last_aseq"] ?? 0,
```

`toTeamLive` becomes:

```ts
  private static toTeamLive(t: Record<string, unknown>): Record<string, unknown> {
    const members = Array.isArray(t["members"]) ? t["members"] as Record<string, unknown>[] : [];
    const latest = t["latest"] as Record<string, unknown> | null | undefined;
    const counts = t["counts"] as Record<string, unknown> | undefined;
    return {
      ...HttpBackendClient.toTeamCore(t),
      members: members.map((m) => {
        const last = m["last"] as Record<string, unknown> | null | undefined;
        return {
          label: m["label"], agentId: m["agent_id"], status: m["status"] ?? "",
          last: last ? {
            kind: last["kind"], at: last["at"], causeSeq: last["cause_seq"] ?? null,
            by: last["by"] ?? null, status: last["status"] ?? null,
            activation: last["activation"] ?? null,
          } : null,
        };
      }),
      latest: latest ?? null,
      counts: counts ?? { posts: 0, proposals: [] },
    };
  }
```

At the bottom:

```ts
export function parseWireTeamActivity(raw: Record<string, unknown>): TeamActivity {
  return TeamActivitySchema.parse(HttpBackendClient.toTeamActivity(raw));
}
```

- [ ] **Step 5: Run, build and commit**

Run (from `apps/editor-client`): `npx vitest run > /tmp/b4.txt 2>&1; echo exit=$?; tail -5 /tmp/b4.txt`, then `npm run build > /tmp/b4b.txt 2>&1; echo exit=$?` (the extension types off `dist/`).
Expected: two `exit=0`.

```bash
git add apps/editor-client/src/contracts/task-contracts.ts apps/editor-client/src/client/http-backend-client.ts apps/editor-client/test/team-activity-contracts.test.ts
git commit -m "feat(editor-client): team activity schema, event, and /live member state"
```

### Task 5: Host — `TeamViewManager` follows activity with its own cursor

**Files:**
- Modify: `apps/vscode-extension/src/team-views.ts`, `apps/vscode-extension/test/team-views.test.ts`

**Interfaces:**
- Consumes: Task 4 (`parseWireTeamActivity`, `TeamActivity`, `TeamDetail.lastAseq`).
- Produces: `TeamViewEvent` gains `{type: "team_activity"; activity: TeamActivity}`. The manager keeps `lastSeq` and `lastAseq` per open team and drops a live event at or below its cursor. Host → webview messages are unchanged (`teamEvent` carries the new variant).

- [ ] **Step 1: Write the failing test** (append inside the `describe("TeamViewManager", …)` block)

```ts
  test("activity backfill then follow skips aseq <= lastAseq", async () => {
    const seen: Array<string | number> = [];
    const act = (aseq: number) => ({
      team_id: "team-1", aseq, at: "2026-10-05T00:00:00Z", label: "alice", kind: "woke",
      activation: 1, cause_seq: 1, payload: { cause: "kickoff" },
    });
    const client = {
      getTeam: async () => ({ ...detail("DELIBERATING", 1), activity: [], lastAseq: 3 }),
      streamChannel: channel([
        { type: "team_activity", payload: { event: act(3) }, seq: 3 },
        { type: "team_activity", payload: { event: act(4) }, seq: 4 },
        { type: "team_post", payload: { post: post(2) }, seq: 2 },
      ] as SequencedStreamEvent[], true),
    };
    const m = new TeamViewManager(() => client, {
      detail: () => {},
      event: (_id, e: TeamViewEvent) => seen.push(
        e.type === "team_activity" ? `a${e.activity.aseq}` : e.type === "team_post" ? `p${e.post.seq}` : e.phase),
    }, 0);
    m.setOpen("t", ["team-1"]);
    await flush(); await flush();
    expect(seen).toEqual(["a4", "p2"]);   // activity and posts have independent cursors
    m.closeAll();
  });
```

The existing `detail(...)` helper in this file builds a `TeamDetail` without `activity`/`lastAseq`; extend it with `activity: [], lastAseq: 0` so every test's object matches the schema.

- [ ] **Step 2: Run to verify failure**

Run (from `apps/vscode-extension`): `perl -e 'alarm 120; exec @ARGV' npx vitest run test/team-views.test.ts > /tmp/b5.txt 2>&1; echo exit=$?; tail -12 /tmp/b5.txt`
Expected: exit≠0 (the activity events are not forwarded).

- [ ] **Step 3: Implement** (`src/team-views.ts`)

Imports gain `parseWireTeamActivity` and `type TeamActivity`. The event union:

```ts
export type TeamViewEvent =
  | { type: "team_post"; post: TeamPost }
  | { type: "team_activity"; activity: TeamActivity }
  | { type: "team_phase"; phase: string; round: number; pausedReason: string | null };
```

In `run`, after `let lastSeq = detail.lastSeq;`: `let lastAseq = detail.lastAseq;` and the per-event check becomes:

```ts
          if (event.type === "team_post") {
            if (event.post.seq <= lastSeq) continue;
            lastSeq = event.post.seq;
          } else if (event.type === "team_activity") {
            if (event.activity.aseq <= lastAseq) continue;
            lastAseq = event.activity.aseq;
          }
```

`toViewEvent` gains:

```ts
  if (event.type === "team_activity") {
    return { type: "team_activity", activity: parseWireTeamActivity(event.payload.event) };
  }
```

- [ ] **Step 4: Run and commit**

Run (from `apps/vscode-extension`): `perl -e 'alarm 300; exec @ARGV' npx vitest run > /tmp/b5.txt 2>&1; echo exit=$?; tail -5 /tmp/b5.txt` and `npm run typecheck > /tmp/b5t.txt 2>&1; echo exit=$?`.
Expected: two `exit=0`.

```bash
git add apps/vscode-extension/src/team-views.ts apps/vscode-extension/test/team-views.test.ts
git commit -m "feat(extension): follow team activity with its own cursor"
```

---

# Part C — Webview (`apps/vscode-extension/webview-ui`)

Pure logic lives in three new modules beside the existing `src/teams.ts` (no `src/teams/` directory — it would shadow the `../teams` import): `src/teamIdentity.ts`, `src/teamJourney.ts`, `src/teamChapters.ts`. Components live in `src/components/teams/`.

### Task 6: State, reducer and member identity

**Files:**
- Modify: `src/types.ts`, `src/teams.ts`, `src/hooks/useAppState.ts`, `src/test/teams.test.ts`, `src/test/useAppState.test.ts`
- Create: `src/teamIdentity.ts`, `src/test/teamIdentity.test.tsx`, `src/components/teams/Avatar.tsx`

**Interfaces:**
- Consumes: Task 5's `teamEvent` variant `team_activity`; Task 4's shapes (camelCase).
- Produces:
  - `types.ts`: `TeamActivityView`, `TeamMemberLastView`, `TeamLatestView`, `TeamCountsView`; `TeamMemberView` gains `name?`, `description?`, `last?`; `TeamSummaryView` gains `latest?`, `counts?`; `TeamLiveView` gains `latest`, `counts`; `TeamDetailView` gains `activity`, `lastAseq`; `TeamEventView` gains `{type: "team_activity"; activity}`; `TeamViewState` becomes `{posts, lastSeq, activity, lastAseq}`
  - `teams.ts`: `viewFromTeamDetail` carries activity; `applyTeamEvent` inserts activity by `aseq` and drops `aseq <= lastAseq`
  - `teamIdentity.ts`: `MEMBER_PALETTE`, `MAIN_COLOR`, `SYSTEM_COLOR`, `identityFor(label: string, roster: string[]) -> {initial: string; color: string; kind: "main" | "system" | "member"}`
  - `<Avatar label roster size="sm" | "md" | "lg" ring?="working" | "idle" />`

- [ ] **Step 1: Write the failing tests**

`src/test/teamIdentity.test.tsx` (JSX in one test):

```tsx
import { describe, expect, it } from "vitest";
import { MAIN_COLOR, MEMBER_PALETTE, SYSTEM_COLOR, identityFor } from "../teamIdentity";

describe("member identity", () => {
  const roster = ["review", "impl", "reader"];
  it("assigns palette hues by roster order", () => {
    expect(identityFor("review", roster).color).toBe(MEMBER_PALETTE[0]);
    expect(identityFor("impl", roster).color).toBe(MEMBER_PALETTE[1]);
  });
  it("uses one letter, two on a collision", () => {
    expect(identityFor("impl", roster).initial).toBe("I");
    expect(identityFor("review", roster).initial).toBe("Re");
    expect(identityFor("reader", roster).initial).toBe("Ra");
  });
  it("main and system are fixed", () => {
    expect(identityFor("main", roster)).toEqual({ initial: "✦", color: MAIN_COLOR, kind: "main" });
    expect(identityFor("system", roster)).toMatchObject({ color: SYSTEM_COLOR, kind: "system" });
  });
  it("an unknown label is grey with its initial", () => {
    expect(identityFor("ghost", roster)).toEqual({ initial: "G", color: SYSTEM_COLOR, kind: "member" });
  });
  it("an Avatar puts no text into its surroundings", async () => {
    const { render } = await import("@testing-library/react");
    const { Avatar } = await import("../components/teams/Avatar");
    const { container } = render(<span><Avatar label="review" roster={roster} />review</span>);
    expect(container.textContent).toBe("review");
    expect(container.querySelector("[data-initial]")?.getAttribute("data-initial")).toBe("Re");
  });
  it("wraps the palette past six members", () => {
    const big = ["a1", "b2", "c3", "d4", "e5", "f6", "g7"];
    expect(identityFor("g7", big).color).toBe(MEMBER_PALETTE[0]);
  });
});
```

Append to `src/test/teams.test.ts`:

```ts
describe("team activity state", () => {
  const act = (aseq: number) => ({ teamId: "team-1", aseq, at: `2026-10-05T00:00:0${aseq}Z`,
    label: "alice", kind: "woke", activation: 1, causeSeq: 1, payload: {} });
  it("keeps activity in aseq order and drops repeats", () => {
    let view = viewFromTeamDetail({ ...SUMMARY, posts: [], lastSeq: 0,
      activity: [act(1), act(2)], lastAseq: 2 });
    view = applyTeamEvent(view, { type: "team_activity", activity: act(2) });
    view = applyTeamEvent(view, { type: "team_activity", activity: act(3) });
    expect(view.activity.map((e) => e.aseq)).toEqual([1, 2, 3]);
    expect(view.lastAseq).toBe(3);
  });
});
```

(Existing `viewFromTeamDetail` calls in this file and in `src/test/useAppState.test.ts` pass details without `activity`/`lastAseq`; add `activity: [], lastAseq: 0` to each — TypeScript catches every one under `npm run typecheck`.)

- [ ] **Step 2: Run to verify failure**

Run (from `webview-ui`): `perl -e 'alarm 120; exec @ARGV' npx vitest run src/test/teamIdentity.test.tsx src/test/teams.test.ts > /tmp/c6.txt 2>&1; echo exit=$?; tail -12 /tmp/c6.txt`
Expected: exit≠0 (`../teamIdentity` does not exist).

- [ ] **Step 3: Types** (`src/types.ts`, in the agent-teams section)

```ts
/** A lifecycle fact (spec 2026-10-05 §4); payload keys stay snake_case. */
export interface TeamActivityView {
  teamId: string;
  aseq: number;
  at: string;
  label: string;
  kind: string;
  activation: number | null;
  causeSeq: number | null;
  payload: Record<string, unknown>;
}

export interface TeamMemberLastView {
  kind: string;
  at: string;
  causeSeq: number | null;
  by: string | null;
  status: string | null;
  activation: number | null;
}

export interface TeamLatestView {
  kind: "post" | "activity";
  label: string;
  text: string;
  at: string;
  event?: string;
  status?: string | null;
}

export interface TeamCountsView {
  posts: number;
  proposals: { id: string; agree: number; object: number; pending: number }[];
}
```

`TeamMemberView` becomes:

```ts
export interface TeamMemberView {
  label: string;
  agentId: string;
  status: string;
  name?: string;
  description?: string;
  last?: TeamMemberLastView | null;
}
```

`TeamSummaryView` gains `latest?: TeamLatestView | null; counts?: TeamCountsView;`. `TeamLiveView` gains `latest: TeamLatestView | null; counts: TeamCountsView;`. `TeamDetailView` gains `activity: TeamActivityView[]; lastAseq: number;`. `TeamEventView` gains `| { type: "team_activity"; activity: TeamActivityView }`. `TeamViewState` becomes:

```ts
export interface TeamViewState {
  posts: TeamPostView[];
  lastSeq: number;
  activity: TeamActivityView[];
  lastAseq: number;
}
```

- [ ] **Step 4: Reducer helpers** (`src/teams.ts`)

```ts
export function viewFromTeamDetail(detail: TeamDetailView): TeamViewState {
  return { posts: detail.posts, lastSeq: detail.lastSeq,
           activity: detail.activity ?? [], lastAseq: detail.lastAseq ?? 0 };
}

/** A post or event the backfill already holds (a live broadcast racing it) is dropped. */
export function applyTeamEvent(view: TeamViewState, event: TeamEventView): TeamViewState {
  if (event.type === "team_post") {
    if (event.post.seq <= view.lastSeq) return view;
    return { ...view, posts: [...view.posts, event.post], lastSeq: event.post.seq };
  }
  if (event.type === "team_activity") {
    if (event.activity.aseq <= view.lastAseq) return view;
    const activity = [...view.activity, event.activity].sort((a, b) => a.aseq - b.aseq);
    return { ...view, activity, lastAseq: event.activity.aseq };
  }
  return view;
}
```

`useAppState.ts` `teamDetail` case — the destructure drops the two new view-only fields:

```ts
      const { posts: _posts, lastSeq: _lastSeq, activity: _activity, lastAseq: _lastAseq,
              ...summary } = msg.detail;
```

- [ ] **Step 5: Identity** (`src/teamIdentity.ts`)

```ts
// Member hues, assigned by roster order (spec 2026-10-05 §6). Kept apart from the
// semantic green/red/amber so a member's colour never reads as a status.
export const MEMBER_PALETTE = ["#7dd3fc", "#f0abfc", "#5eead4", "#fdba74", "#bef264", "#fda4af"];
export const MAIN_COLOR = "#a78bfa";
export const SYSTEM_COLOR = "#62626e";

export interface MemberIdentity {
  initial: string;
  color: string;
  kind: "main" | "system" | "member";
}

/** A stable avatar for a label: one letter, or two when members share an initial — the
 * first such member (roster order) takes its second letter, each later one the first letter
 * after its initial that no earlier one used (review / reader → Re / Ra). */
export function identityFor(label: string, roster: string[]): MemberIdentity {
  if (label === "main") return { initial: "✦", color: MAIN_COLOR, kind: "main" };
  if (label === "system" || label === "team") {
    return { initial: "·", color: SYSTEM_COLOR, kind: "system" };
  }
  const index = roster.indexOf(label);
  const first = label.charAt(0).toUpperCase();
  const sameInitial = roster.filter((other) => other.charAt(0).toUpperCase() === first);
  let initial = first;
  if (index >= 0 && sameInitial.length > 1) {
    const taken = new Set<string>();
    for (const other of sameInitial) {
      const pick = [...other.slice(1)].find((c) => !taken.has(c.toLowerCase())) ?? "";
      taken.add(pick.toLowerCase());
      if (other === label) {
        initial = first + pick.toLowerCase();
        break;
      }
    }
  }
  return {
    initial,
    color: index >= 0 ? MEMBER_PALETTE[index % MEMBER_PALETTE.length] : SYSTEM_COLOR,
    kind: "member",
  };
}
```

- [ ] **Step 6: Avatar** (`src/components/teams/Avatar.tsx`)

```tsx
import { identityFor } from "../../teamIdentity";

const SIZES = { sm: 16, md: 22, lg: 34 } as const;

/** A member's avatar (spec 2026-10-05 §6): initial on its hue; a pulsing ring while working,
 * a grey ring and desaturated fill while idle. */
export function Avatar({ label, roster, size = "md", ring }: {
  label: string; roster: string[]; size?: keyof typeof SIZES; ring?: "working" | "idle";
}) {
  const id = identityFor(label, roster);
  const px = SIZES[size];
  return (
    // The initial is drawn by CSS (data-initial), so it never enters the text around it —
    // "woke review · impl" reads (and tests) as words, not "woke Rreview · Iimpl".
    <span aria-hidden="true" data-initial={id.initial}
      className="relative inline-grid flex-shrink-0 place-items-center rounded-full font-semibold before:content-[attr(data-initial)]"
      style={{ width: px, height: px, fontSize: Math.round(px * 0.45), color: "var(--color-panel)",
               background: id.color, filter: ring === "idle" ? "saturate(.25) brightness(.8)" : undefined }}>
      {ring && (
        <span className={ring === "working" ? "team-ring-pulse" : undefined}
          style={{ position: "absolute", inset: -3, borderRadius: "50%",
                   border: `1.5px solid ${ring === "working" ? id.color : "var(--color-text-4)"}` }} />
      )}
    </span>
  );
}
```

Add to `src/index.css`, in the DESIGN LANGUAGE block:

```css
/* Team member avatar ring (spec 2026-10-05 §6). */
@keyframes team-ring { 0% { transform: scale(.9); opacity: 1; } 100% { transform: scale(1.35); opacity: 0; } }
.team-ring-pulse { animation: team-ring 1.4s var(--ease-out) infinite; }
@media (prefers-reduced-motion: reduce) { .team-ring-pulse { animation: none; } }
```

- [ ] **Step 7: Run and commit**

Run (from `webview-ui`): `perl -e 'alarm 300; exec @ARGV' npx vitest run > /tmp/c6.txt 2>&1; echo exit=$?; tail -5 /tmp/c6.txt` and `npm run typecheck > /tmp/c6t.txt 2>&1; echo exit=$?`.
Expected: two `exit=0`.

```bash
git add apps/vscode-extension/webview-ui/src/types.ts apps/vscode-extension/webview-ui/src/teams.ts apps/vscode-extension/webview-ui/src/hooks/useAppState.ts apps/vscode-extension/webview-ui/src/teamIdentity.ts apps/vscode-extension/webview-ui/src/components/teams/Avatar.tsx apps/vscode-extension/webview-ui/src/index.css apps/vscode-extension/webview-ui/src/test/teamIdentity.test.tsx apps/vscode-extension/webview-ui/src/test/teams.test.ts apps/vscode-extension/webview-ui/src/test/useAppState.test.ts
git commit -m "feat(webview): team activity state and stable member identity"
```

### Task 7: The journey builder

**Files:**
- Create: `src/teamJourney.ts`, `src/test/teamJourney.test.ts`

**Interfaces:**
- Consumes: Task 6 types.
- Produces (`src/teamJourney.ts`):
  - `JourneyFilters {posts, activity, messages}`, `DEFAULT_FILTERS` (`messages: false`)
  - `TallyChip {label, stance: "agree" | "object" | "pending", was: "agree" | "object" | null, seq: number | null}`
  - `PostFooter {woke: string[], queued: string[]}`
  - `JourneyItem` union: `chapter {key, title, current, ended}` · `post {key, at, post, footer, tally}` · `stance {key, at, post, replaces}` · `system {key, at, post}` · `beat {key, at, event}` · `wrap {key, at, event}` · `gap {key, minutes}`
  - `chapterTitle(phase: string, round: number): string`
  - `tallyFor(proposal: TeamPostView, posts: TeamPostView[], roster: string[]): TallyChip[]`
  - `buildJourney(posts, activity, roster, filters = DEFAULT_FILTERS): JourneyItem[]`

Rules (spec 2026-10-05 §6), all in `buildJourney`:
1. A `Kickoff` chapter first, then the round-0 posts; everything else in time order (posts by `createdAt`, activity by `at`; on equal times a post precedes the events it caused).
2. A `phase` event becomes a chapter (`chapterTitle`); the last chapter is `current` unless it is an ended one (`Done`, `Disbanded`, `Failed`).
3. `woke`/`notified` with a `causeSeq` whose post is rendered as a post card folds into that card's footer (`woke` / `queued` lists) — not an item. Otherwise it is a `beat`.
4. `took_up`, `picked_up`, `capped` → `beat`; `wrapped_up` → `wrap`.
5. `agree`/`object`/`withdraw` posts → `stance` with `replaces` = the same author's previous stance seq on the same proposal; `system` posts → `system` (always shown).
6. Filters: `posts` off hides post and stance items (system lines stay); `activity` off hides beats and wraps (footers stay); `messages` off hides direct messages (and their caused wakes become beats).
7. A `gap` precedes a timed item more than 60 s after the previous timed item.

- [ ] **Step 1: Write the failing tests** (`src/test/teamJourney.test.ts`)

```ts
import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, buildJourney, chapterTitle, tallyFor } from "../teamJourney";
import type { TeamActivityView, TeamPostView } from "../types";

const T = (s: number) => new Date(Date.UTC(2026, 9, 5, 17, 10, s)).toISOString();
const post = (seq: number, at: number, over: Partial<TeamPostView> = {}): TeamPostView => ({
  teamId: "team-1", seq, author: "review", kind: "post", recipient: null, text: `p${seq}`,
  mentions: [], refId: null, round: 1, payload: {}, closed: null, createdAt: T(at), ...over,
});
const ev = (aseq: number, at: number, label: string, kind: string,
            over: Partial<TeamActivityView> = {}): TeamActivityView => ({
  teamId: "team-1", aseq, at: T(at), label, kind, activation: 1, causeSeq: null, payload: {}, ...over,
});
const ROSTER = ["review", "impl"];
const kinds = (items: ReturnType<typeof buildJourney>) => items.map((i) => i.kind);

const KICKOFF = post(1, 1, { author: "main", kind: "proposal", round: 0 });
const PHASE = ev(1, 0, "team", "phase", { activation: null, payload: { phase: "DELIBERATING", round: 1 } });

describe("buildJourney", () => {
  it("kickoff wakes fold into the proposal's footer", () => {
    const items = buildJourney([KICKOFF], [PHASE,
      ev(2, 1, "review", "woke", { causeSeq: 1, payload: { cause: "kickoff" } }),
      ev(3, 1, "impl", "woke", { causeSeq: 1, payload: { cause: "kickoff" } }),
      ev(4, 2, "review", "took_up", { payload: { posts: [1], from: ["main"] } }),
    ], ROSTER);
    expect(kinds(items)).toEqual(["chapter", "post", "chapter", "beat"]);
    const card = items[1];
    expect(card.kind === "post" && card.footer).toEqual({ woke: ["review", "impl"], queued: [] });
    expect(items[0]).toMatchObject({ title: "Kickoff", current: false });
    expect(items[2]).toMatchObject({ title: "Round 1 · deliberating", current: true });
  });

  it("a mention's wake folds under the mentioning post; a queued one too", () => {
    const items = buildJourney([KICKOFF, post(2, 10, { mentions: ["impl"] })], [PHASE,
      ev(2, 10, "impl", "notified", { causeSeq: 2, payload: { cause: "mention", by: "review" } }),
    ], ROSTER);
    const card = items.find((i) => i.kind === "post" && i.post.seq === 2);
    expect(card?.kind === "post" && card.footer).toEqual({ woke: [], queued: ["impl"] });
    expect(items.some((i) => i.kind === "beat")).toBe(false);
  });

  it("hidden DM wake becomes a standalone beat", () => {
    const dm = post(2, 10, { author: "impl", recipient: "review" });
    const wake = ev(2, 10, "review", "woke", { causeSeq: 2, payload: { cause: "message", by: "impl" } });
    const hidden = buildJourney([KICKOFF, dm], [PHASE, wake], ROSTER);
    expect(hidden.filter((i) => i.kind === "beat")).toHaveLength(1);
    expect(hidden.some((i) => i.kind === "post" && i.post.seq === 2)).toBe(false);
    const shown = buildJourney([KICKOFF, dm], [PHASE, wake], ROSTER,
                               { ...DEFAULT_FILTERS, messages: true });
    expect(shown.some((i) => i.kind === "beat")).toBe(false);
    const card = shown.find((i) => i.kind === "post" && i.post.seq === 2);
    expect(card?.kind === "post" && card.footer.woke).toEqual(["review"]);
  });

  it("stances become replies; a changed stance replaces the earlier one", () => {
    const items = buildJourney([KICKOFF,
      post(2, 10, { kind: "object", refId: "P1", text: "missing case" }),
      post(3, 20, { kind: "agree", refId: "P1", text: "fine now" }),
    ], [PHASE], ROSTER);
    const stances = items.filter((i) => i.kind === "stance");
    expect(stances.map((i) => i.kind === "stance" && i.replaces)).toEqual([null, 2]);
  });

  it("changed stance shows in the tally with what it was", () => {
    const posts = [KICKOFF,
      post(2, 10, { kind: "object", refId: "P1" }),
      post(3, 20, { kind: "agree", refId: "P1" }),
      post(4, 21, { author: "impl", kind: "agree", refId: "P1" })];
    expect(tallyFor(KICKOFF, posts, ROSTER)).toEqual([
      { label: "review", stance: "agree", was: "object", seq: 3 },
      { label: "impl", stance: "agree", was: null, seq: 4 },
    ]);
    expect(tallyFor(KICKOFF, [KICKOFF], ROSTER)[0]).toEqual(
      { label: "review", stance: "pending", was: null, seq: null });
  });

  it("wrap-ups, phases and system lines", () => {
    const items = buildJourney([KICKOFF, post(2, 30, { author: "system", kind: "system", text: "The team was disbanded." })], [PHASE,
      ev(2, 20, "review", "wrapped_up", { payload: { status: "stopped", report: "" } }),
      ev(3, 31, "team", "phase", { activation: null, payload: { phase: "DISBANDED", round: 1 } }),
    ], ROSTER);
    expect(kinds(items)).toEqual(["chapter", "post", "chapter", "wrap", "system", "chapter"]);
    const last = items[items.length - 1];
    expect(last).toMatchObject({ kind: "chapter", title: "Disbanded", ended: true, current: false });
  });

  it("gaps over a minute get a divider", () => {
    const items = buildJourney([KICKOFF, post(2, 10), post(3, 10 + 180)], [], ROSTER);
    const gap = items.find((i) => i.kind === "gap");
    expect(gap).toMatchObject({ minutes: 3 });
  });

  it("filters hide layers but keep footers", () => {
    const events = [PHASE,
      ev(2, 1, "review", "woke", { causeSeq: 1, payload: { cause: "kickoff" } }),
      ev(3, 2, "review", "took_up", { payload: { posts: [1] } }),
      ev(4, 5, "review", "wrapped_up", { payload: { status: "completed", report: "r" } })];
    const noActivity = buildJourney([KICKOFF], events, ROSTER, { ...DEFAULT_FILTERS, activity: false });
    expect(kinds(noActivity)).toEqual(["chapter", "post", "chapter"]);
    const card = noActivity[1];
    expect(card.kind === "post" && card.footer.woke).toEqual(["review"]);
    const noPosts = buildJourney([KICKOFF], events, ROSTER, { ...DEFAULT_FILTERS, posts: false });
    expect(kinds(noPosts)).toEqual(["chapter", "chapter", "beat", "beat", "wrap"]);
  });

  it("posts without activity (a team from before this change)", () => {
    const items = buildJourney([KICKOFF, post(2, 10)], [], ROSTER);
    expect(kinds(items)).toEqual(["chapter", "post", "post"]);
    expect(items[0]).toMatchObject({ title: "Kickoff", current: true });
  });

  it("chapter titles", () => {
    expect(chapterTitle("DELIBERATING", 2)).toBe("Round 2 · deliberating");
    expect(chapterTitle("IMPLEMENTING", 1)).toBe("Implementing");
    expect(chapterTitle("FAILED", 1)).toBe("Failed");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (from `webview-ui`): `perl -e 'alarm 120; exec @ARGV' npx vitest run src/test/teamJourney.test.ts > /tmp/c7.txt 2>&1; echo exit=$?; tail -12 /tmp/c7.txt`
Expected: exit≠0 (`../teamJourney` does not exist).

- [ ] **Step 3: Implement** (`src/teamJourney.ts`)

```ts
import type { TeamActivityView, TeamPostView } from "./types";

// The board as a journey (spec 2026-10-05 §6). Every folding rule lives here, as data;
// components only draw the items.

export interface JourneyFilters { posts: boolean; activity: boolean; messages: boolean }
export const DEFAULT_FILTERS: JourneyFilters = { posts: true, activity: true, messages: false };

export type Stance = "agree" | "object";
export interface TallyChip { label: string; stance: Stance | "pending"; was: Stance | null; seq: number | null }
export interface PostFooter { woke: string[]; queued: string[] }

export type JourneyItem =
  | { kind: "chapter"; key: string; title: string; current: boolean; ended: boolean }
  | { kind: "post"; key: string; at: string; post: TeamPostView; footer: PostFooter; tally: TallyChip[] | null }
  | { kind: "stance"; key: string; at: string; post: TeamPostView; replaces: number | null }
  | { kind: "system"; key: string; at: string; post: TeamPostView }
  | { kind: "beat"; key: string; at: string; event: TeamActivityView }
  | { kind: "wrap"; key: string; at: string; event: TeamActivityView }
  | { kind: "gap"; key: string; minutes: number };

const GAP_MS = 60_000;
const ENDED_PHASES = new Set(["DONE", "DISBANDED", "FAILED"]);
const STANCE_KINDS = new Set(["agree", "object", "withdraw"]);

export function chapterTitle(phase: string, round: number): string {
  switch (phase) {
    case "DELIBERATING": return `Round ${round} · deliberating`;
    case "AWAITING_APPROVAL": return "Awaiting your approval";
    case "IMPLEMENTING": return "Implementing";
    case "REVIEWING": return "Review";
    case "DEADLOCKED": return "Deadlocked";
    case "PAUSED": return "Paused";
    case "DONE": return "Done";
    case "DISBANDED": return "Disbanded";
    case "FAILED": return "Failed";
    default: return phase.charAt(0) + phase.slice(1).toLowerCase();
  }
}

/** One chip per member (the author excluded): its latest stance on the proposal, and what
 * it was before when that differed. */
export function tallyFor(proposal: TeamPostView, posts: TeamPostView[], roster: string[]): TallyChip[] {
  const id = `P${proposal.seq}`;
  return roster.filter((label) => label !== proposal.author).map((label) => {
    const mine = posts.filter((p) => p.author === label && p.refId === id
                                     && (p.kind === "agree" || p.kind === "object"));
    const last = mine[mine.length - 1];
    const before = last ? [...mine].reverse().find((p) => p.kind !== last.kind) : undefined;
    return {
      label,
      stance: last ? (last.kind as Stance) : "pending",
      was: before ? (before.kind as Stance) : null,
      seq: last ? last.seq : null,
    };
  });
}

type Entry = { at: number; order: number; post?: TeamPostView; event?: TeamActivityView };

export function buildJourney(
  posts: TeamPostView[], activity: TeamActivityView[], roster: string[],
  filters: JourneyFilters = DEFAULT_FILTERS,
): JourneyItem[] {
  const isCard = (p: TeamPostView) => p.kind !== "system" && !STANCE_KINDS.has(p.kind);
  const cardShown = (p: TeamPostView) =>
    isCard(p) && filters.posts && (p.recipient === null || filters.messages);

  // Rule 3: fold caused wakes into the post that caused them.
  const bySeq = new Map(posts.map((p) => [p.seq, p]));
  const footers = new Map<number, PostFooter>();
  const folded = new Set<number>();
  for (const e of activity) {
    if ((e.kind !== "woke" && e.kind !== "notified") || e.causeSeq === null) continue;
    const cause = bySeq.get(e.causeSeq);
    if (!cause || !cardShown(cause)) continue;
    const footer = footers.get(cause.seq) ?? { woke: [], queued: [] };
    (e.kind === "woke" ? footer.woke : footer.queued).push(e.label);
    footers.set(cause.seq, footer);
    folded.add(e.aseq);
  }

  const items: JourneyItem[] = [{ kind: "chapter", key: "kickoff", title: "Kickoff", current: false, ended: false }];
  let lastAt: number | null = null;
  const timed = (item: Exclude<JourneyItem, { kind: "chapter" } | { kind: "gap" }>) => {
    const at = Date.parse(item.at);
    if (lastAt !== null && at - lastAt > GAP_MS) {
      items.push({ kind: "gap", key: `gap-${item.key}`, minutes: Math.round((at - lastAt) / 60_000) });
    }
    lastAt = at;
    items.push(item);
  };
  const postItem = (p: TeamPostView): void => {
    if (p.kind === "system") {
      timed({ kind: "system", key: `p${p.seq}`, at: p.createdAt, post: p });
    } else if (STANCE_KINDS.has(p.kind)) {
      if (!filters.posts) return;
      const earlier = posts.filter((q) => q.seq < p.seq && q.author === p.author
                                         && q.refId === p.refId && STANCE_KINDS.has(q.kind));
      timed({ kind: "stance", key: `p${p.seq}`, at: p.createdAt, post: p,
              replaces: earlier.length ? earlier[earlier.length - 1].seq : null });
    } else if (cardShown(p)) {
      timed({ kind: "post", key: `p${p.seq}`, at: p.createdAt, post: p,
              footer: footers.get(p.seq) ?? { woke: [], queued: [] },
              tally: p.kind === "proposal" ? tallyFor(p, posts, roster) : null });
    }
  };

  // Rule 1: the kickoff first, then everything else in time order.
  const kickoff = posts.filter((p) => p.round === 0);
  kickoff.forEach(postItem);
  const entries: Entry[] = [
    ...posts.filter((p) => p.round !== 0).map((p) => ({ at: Date.parse(p.createdAt), order: 0, post: p })),
    ...activity.map((e) => ({ at: Date.parse(e.at), order: 1, event: e })),
  ].sort((a, b) => a.at - b.at || a.order - b.order);

  for (const entry of entries) {
    if (entry.post) {
      postItem(entry.post);
      continue;
    }
    const e = entry.event as TeamActivityView;
    if (e.kind === "phase") {
      const phase = String(e.payload.phase ?? "");
      items.push({ kind: "chapter", key: `a${e.aseq}`,
                   title: chapterTitle(phase, Number(e.payload.round ?? 1)),
                   current: false, ended: ENDED_PHASES.has(phase) });
      continue;
    }
    if (!filters.activity || folded.has(e.aseq)) continue;
    timed(e.kind === "wrapped_up"
      ? { kind: "wrap", key: `a${e.aseq}`, at: e.at, event: e }
      : { kind: "beat", key: `a${e.aseq}`, at: e.at, event: e });
  }

  // Rule 2: the last chapter is the current one, unless the team has ended.
  const chapters = items.filter((i): i is Extract<JourneyItem, { kind: "chapter" }> => i.kind === "chapter");
  const last = chapters[chapters.length - 1];
  if (!last.ended) last.current = true;
  return items;
}
```

The first `phase` event (written when the team is created) sorts after the round-0 posts only because those posts are pulled out first — that is the "Round 1" chapter following "Kickoff".

- [ ] **Step 4: Run and commit**

Run (from `webview-ui`): `perl -e 'alarm 120; exec @ARGV' npx vitest run src/test/teamJourney.test.ts > /tmp/c7.txt 2>&1; echo exit=$?; tail -5 /tmp/c7.txt` and `npm run typecheck > /tmp/c7t.txt 2>&1; echo exit=$?`.
Expected: two `exit=0`.

```bash
git add apps/vscode-extension/webview-ui/src/teamJourney.ts apps/vscode-extension/webview-ui/src/test/teamJourney.test.ts
git commit -m "feat(webview): journey builder — chapters, folded wakes, stance replies, gaps"
```

### Task 8: The board — journey components and the window header

**Files:**
- Create: `src/components/teams/PostBody.tsx`, `src/components/teams/PhaseStepper.tsx`, `src/components/teams/Journey.tsx`
- Modify: `src/components/teams/TeamWindow.tsx`
- Delete: `src/components/teams/TeamBoard.tsx` (replaced by `Journey.tsx`)
- Rewrite: `src/test/teamWindow.test.tsx`

**Interfaces:**
- Consumes: Task 6 (`Avatar`, `identityFor`, state types), Task 7 (`buildJourney`, `JourneyItem`, `DEFAULT_FILTERS`), `TeamsContext`, `AgentsContext`, `agents.ts` (`elapsedMs`, `formatElapsed`), `useNow`.
- Produces:
  - `<PostBody text />` — markdown for team posts and reports (react-markdown + the chat's GFM plugin, without the chat bubble frame)
  - `<PhaseStepper phase round maxRounds mini? />`
  - `<Journey teamId />` — filters, items, and the pinned "now" strip
  - `TeamWindow`'s header: name, goal, Disband, stepper, presence row (avatar + state, click opens that member's tab); tabs carry an avatar and a state dot

- [ ] **Step 1: Write the failing tests** (`src/test/teamWindow.test.tsx`, replacing the file)

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { ThreadView } from "../components/ThreadView";
import { TeamWindow } from "../components/teams/TeamWindow";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AgentSummaryView, AppState, TeamActivityView, TeamPostView, TeamSummaryView } from "../types";

let postMessage: ReturnType<typeof vi.fn>;
beforeEach(async () => {
  postMessage = (await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

const T = (s: number) => new Date(Date.UTC(2026, 9, 5, 17, 10, s)).toISOString();
const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  maxRounds: 3, pausedReason: null,
  members: [{ label: "review", agentId: "agent-r", status: "completed", name: "explore", description: "Reads code" },
            { label: "impl", agentId: "agent-i", status: "running", name: "general-purpose", description: "Edits code" }],
  openProposals: [], usage: { requests: 0, budget: 160 }, createdAt: T(0),
};
const post = (seq: number, at: number, over: Partial<TeamPostView>): TeamPostView => ({
  teamId: "team-1", seq, author: "review", kind: "post", recipient: null, text: "", mentions: [],
  refId: null, round: 1, payload: {}, closed: null, createdAt: T(at), ...over,
});
const ev = (aseq: number, at: number, label: string, kind: string,
            over: Partial<TeamActivityView> = {}): TeamActivityView => ({
  teamId: "team-1", aseq, at: T(at), label, kind, activation: 1, causeSeq: null, payload: {}, ...over,
});
const POSTS: TeamPostView[] = [
  post(1, 1, { author: "main", kind: "proposal", round: 0, text: "**Add discount codes** to the cart",
    payload: { assignments: [{ member: "impl", part: "cart", files: ["shop/cart.py"] }] } }),
  post(2, 20, { kind: "object", refId: "P1", text: "invalid codes keep the old discount",
    payload: { evidence: { files: ["shop/cart.py"], line: 14 } } }),
  post(3, 30, { kind: "agree", refId: "P1", text: "fine with the clearing rule" }),
  post(4, 31, { author: "impl", recipient: "review", text: "which file holds the cap?" }),
];
const ACTIVITY: TeamActivityView[] = [
  ev(1, 0, "team", "phase", { activation: null, payload: { phase: "DELIBERATING", round: 1 } }),
  ev(2, 1, "review", "woke", { causeSeq: 1, payload: { cause: "kickoff", by: "main" } }),
  ev(3, 1, "impl", "woke", { causeSeq: 1, payload: { cause: "kickoff", by: "main" } }),
  ev(4, 2, "review", "took_up", { payload: { posts: [1], from: ["main"] } }),
  ev(5, 29, "review", "wrapped_up", { payload: { status: "completed", report: "All claims **verified**.",
    duration_ms: 27000, tools: 12, posts: 0, messages: 0, stances: 2 } }),
  ev(6, 31, "review", "woke", { activation: 2, causeSeq: 4, payload: { cause: "message", by: "impl", post_seq: 4 } }),
];
const agent = (id: string, label: string, status: string): AgentSummaryView => ({
  agentId: id, parentAgentId: null, depth: 1, name: "general-purpose", label, status,
  now: "read_file shop/cart.py", toolCount: 3, filesChangedCount: 0, startedAt: T(0),
  endedAt: null, reportPreview: "", activationStartedAt: T(25), activationEndedAt: null,
});

function renderWindow(tab = "board", team = TEAM) {
  const onTab = vi.fn();
  const onClose = vi.fn();
  const agentsUi: AgentsUi = {
    agents: { "agent-r": agent("agent-r", "review", "completed"), "agent-i": agent("agent-i", "impl", "running") },
    views: {}, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
  };
  const teamsUi: TeamsUi = { teams: { "team-1": team },
    views: { "team-1": { posts: POSTS, lastSeq: 4, activity: ACTIVITY, lastAseq: 6 } }, openTeam: vi.fn() };
  render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>
    <TeamWindow teamId="team-1" tab={tab} onTab={onTab} onClose={onClose} />
  </TeamsContext.Provider></AgentsContext.Provider>);
  return { onTab, onClose };
}

describe("TeamWindow board", () => {
  it("reads as a journey: chapters, the proposal with markdown, its wake footer and tally", () => {
    renderWindow();
    expect(screen.getByRole("dialog", { name: "Team auth" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Kickoff" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Round 1 · deliberating" })).toBeInTheDocument();
    expect(screen.getByText("Add discount codes").tagName).toBe("STRONG");
    expect(screen.getByTestId("footer-p1")).toHaveTextContent("woke review · impl");
    expect(screen.getByRole("button", { name: "review agrees, was objecting" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "impl no stance yet" })).toBeInTheDocument();
  });

  it("stances are replies; a later one says what it replaces", () => {
    renderWindow();
    expect(screen.getByText("objects to")).toBeInTheDocument();
    expect(screen.getByText("replaces #2")).toBeInTheDocument();
    fireEvent.click(screen.getByText("evidence"));
    expect(screen.getByText("shop/cart.py:14")).toBeInTheDocument();
  });

  it("beats and wrap-ups; a report expands as markdown", () => {
    renderWindow();
    expect(screen.getByTestId("beat-a4")).toHaveTextContent("review took up P1 · 1 new post");
    const wrap = screen.getByTestId("wrap-a5");
    expect(wrap).toHaveTextContent("review wrapped up");
    expect(wrap).toHaveTextContent("27s · 12 tools · 2 stances");
    fireEvent.click(screen.getByRole("button", { name: "Show review's report" }));
    expect(screen.getByText("verified").tagName).toBe("STRONG");
  });

  it("a wake whose message is hidden stands alone; Messages shows the message with the footer", () => {
    renderWindow();
    expect(screen.getByTestId("beat-a6")).toHaveTextContent("review woke — direct message from impl #4");
    fireEvent.click(screen.getByRole("button", { name: "Messages" }));
    expect(screen.queryByTestId("beat-a6")).toBeNull();
    expect(screen.getByText("which file holds the cap?")).toBeInTheDocument();
    expect(screen.getByTestId("footer-p4")).toHaveTextContent("woke review");
  });

  it("the now strip shows who is working and who is idle", () => {
    renderWindow();
    const now = screen.getByTestId("now-strip");
    expect(now).toHaveTextContent("impl is working — read_file shop/cart.py");
    expect(now).toHaveTextContent("review is idle");
  });

  it("header: stepper, presence opens a member tab, disband needs a confirm, Esc closes", () => {
    const { onTab, onClose } = renderWindow();
    expect(screen.getByText("Deliberating · round 1 of 3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open impl's tab" }));
    expect(onTab).toHaveBeenCalledWith("agent-i");
    fireEvent.click(screen.getByRole("button", { name: "Disband" }));
    expect(postMessage).not.toHaveBeenCalledWith({ type: "disbandTeam", teamId: "team-1" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm disband" }));
    expect(postMessage).toHaveBeenCalledWith({ type: "disbandTeam", teamId: "team-1" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("an ended team has no Disband and no now strip", () => {
    renderWindow("board", { ...TEAM, phase: "DISBANDED" });
    expect(screen.queryByRole("button", { name: "Disband" })).toBeNull();
    expect(screen.queryByTestId("now-strip")).toBeNull();
  });
});

describe("ThreadView team wiring", () => {
  it("Open board opens the window, reports the open team, and a member tab joins the open agents", () => {
    const state = {
      view: "thread", threads: [], activeThreadId: "t", streaming: null, thinkingStatus: null,
      inputEnabled: true, liveGates: [], livePlan: null, liveReview: null, liveError: null,
      liveTodos: null, liveSessions: null, sessionTranscripts: {}, workbar: null,
      retryStatus: null, tokenProgress: null, editFailure: null, liveStatus: null,
      turnActive: false, turnKind: null, agentsRunning: 0, planMode: false, stepReview: true,
      agents: {}, agentViews: {}, teams: { "team-1": TEAM }, teamViews: {},
      messages: [{ role: "agent", content: "", type: "team_created", timestamp: "t",
                   metadata: { team_id: "team-1", name: "auth", agent_ids: ["agent-r", "agent-i"] } }],
    } as AppState;
    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenTeams", teamIds: [] });
    act(() => { screen.getByRole("button", { name: "Open board" }).click(); });
    expect(screen.getByRole("dialog", { name: "Team auth" })).toBeInTheDocument();
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenTeams", teamIds: ["team-1"] });
    act(() => { screen.getByRole("tab", { name: /review/ }).click(); });
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenAgents", agentIds: ["agent-r"] });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (from `webview-ui`): `perl -e 'alarm 120; exec @ARGV' npx vitest run src/test/teamWindow.test.tsx > /tmp/c8.txt 2>&1; echo exit=$?; tail -20 /tmp/c8.txt`
Expected: exit≠0 (no "Kickoff" chapter; the old board has no journey).

- [ ] **Step 3: `PostBody.tsx` and `PhaseStepper.tsx`**

`src/components/teams/PostBody.tsx`:

```tsx
import ReactMarkdown from "react-markdown";
import { MARKDOWN_COMPONENTS, MARKDOWN_PLUGINS, MARKDOWN_TABLE_CLASSES } from "../shared/MarkdownContent";

/** Markdown for team posts and reports — the chat's renderer without its bubble frame. */
export function PostBody({ text, className = "" }: { text: string; className?: string }) {
  return (
    <div className={[
      "min-w-0 text-[12px] leading-[1.5] text-text break-words",
      "[&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_ul]:pl-4 [&_ol]:pl-4 [&_ul]:list-disc [&_ol]:list-decimal",
      "[&_h1]:text-[12px] [&_h2]:text-[12px] [&_h3]:text-[11px] [&_h4]:text-[11px] [&_h1]:font-semibold [&_h2]:font-semibold",
      "[&_h3]:uppercase [&_h4]:uppercase [&_h3]:tracking-[.06em] [&_h4]:tracking-[.06em] [&_h3]:text-text-2 [&_h4]:text-text-2",
      "[&_code]:font-mono [&_code]:text-[11px] [&_code]:text-code",
      ...MARKDOWN_TABLE_CLASSES, className,
    ].join(" ")}>
      <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} components={MARKDOWN_COMPONENTS}>{text}</ReactMarkdown>
    </div>
  );
}
```

(If `text-code` is not a Tailwind colour in this theme, use `[&_code]:text-[var(--color-code)]`; check `@theme` in `src/index.css` for `--color-code`.)

`src/components/teams/PhaseStepper.tsx`:

```tsx
import { isTerminalTeam } from "../../teams";

const STEPS = ["Kickoff", "Deliberating", "Implementing", "Review", "Done"];
const INDEX: Record<string, number> = {
  DELIBERATING: 1, AWAITING_APPROVAL: 1, DEADLOCKED: 1, PAUSED: 1,
  IMPLEMENTING: 2, REVIEWING: 3, DONE: 4,
};

/** The road the team travels (spec 2026-10-05 §6/§8). An ended team shows how it ended. */
export function PhaseStepper({ phase, round, maxRounds, mini = false }: {
  phase: string; round: number; maxRounds: number; mini?: boolean;
}) {
  const at = INDEX[phase] ?? 1;
  const ended = isTerminalTeam(phase) && phase !== "DONE";
  const label = (step: string, i: number) =>
    i === 1 && at === 1 && !ended ? `Deliberating · round ${round} of ${maxRounds}` : step;
  return (
    <div className={`flex flex-wrap items-center gap-y-1 ${mini ? "text-[10.5px]" : "text-[11px]"}`} aria-label="Phase">
      {STEPS.map((step, i) => {
        const done = i < at || phase === "DONE";
        const now = i === at && !ended && phase !== "DONE";
        if (ended && i > 1) return null;
        return (
          <span key={step} className="inline-flex items-center whitespace-nowrap">
            {i > 0 && <span className={mini ? "mx-1 h-px w-2.5" : "mx-1.5 h-px w-[18px]"} style={{ background: "var(--color-border-strong)" }} />}
            <span className="mr-1.5 h-2 w-2 rounded-full" style={{
              background: now ? "var(--color-accent)" : done ? "var(--color-green)" : "transparent",
              border: `1.5px solid ${now ? "var(--color-accent)" : done ? "var(--color-green)" : "var(--color-text-4)"}`,
              boxShadow: now ? "0 0 0 3px var(--accent-bg-2)" : undefined }} />
            <span style={{ color: now ? "var(--color-accent-ink)" : done ? "var(--color-text-2)" : "var(--color-text-4)",
                           fontWeight: now ? 600 : undefined }}>{label(step, i)}</span>
          </span>
        );
      })}
      {ended && (
        <span className="inline-flex items-center whitespace-nowrap">
          <span className={mini ? "mx-1 h-px w-2.5" : "mx-1.5 h-px w-[18px]"} style={{ background: "var(--color-border-strong)" }} />
          <span className="mr-1.5 h-2 w-2 rounded-full" style={{ background: phase === "FAILED" ? "var(--color-red)" : "var(--color-text-3)" }} />
          <span style={{ color: phase === "FAILED" ? "var(--color-red)" : "var(--color-text-2)", fontWeight: 600 }}>
            {phase === "FAILED" ? "Failed" : "Disbanded"}
          </span>
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 4: `Journey.tsx`**

```tsx
import { useMemo, useState } from "react";
import { elapsedMs, formatElapsed, isTerminalAgent } from "../../agents";
import { isTerminalTeam } from "../../teams";
import { identityFor } from "../../teamIdentity";
import { DEFAULT_FILTERS, buildJourney, type JourneyFilters, type JourneyItem, type TallyChip } from "../../teamJourney";
import type { TeamActivityView, TeamPostView } from "../../types";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { Avatar } from "./Avatar";
import { PostBody } from "./PostBody";
import { useTeamsUi } from "./TeamsContext";

const clock = (at: string) => new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function Name({ label, roster }: { label: string; roster: string[] }) {
  return <span className="font-semibold" style={{ color: identityFor(label, roster).color }}>{label}</span>;
}

function Seq({ seq }: { seq: number }) {
  return <span className="font-mono text-[10.5px] font-semibold text-text-3">#{seq}</span>;
}

function Time({ at }: { at: string }) {
  return <span className="ml-auto whitespace-nowrap text-[10.5px] tabular-nums text-text-3" title={new Date(at).toLocaleString()}>{clock(at)}</span>;
}

/** The spine's dot for an item, coloured by its author. */
function Dot({ color, shape = "round" }: { color: string; shape?: "round" | "square" }) {
  return <span aria-hidden="true" className="absolute left-[-19px] top-[13px] h-[9px] w-[9px]"
    style={{ background: color, borderRadius: shape === "round" ? "50%" : 2, boxShadow: "0 0 0 3px var(--color-surface-2)" }} />;
}

const STANCE_WORD = { agree: "agrees", object: "objects", pending: "no stance yet" } as const;
const WAS_WORD = { agree: "agreeing", object: "objecting" } as const;

function Tally({ chips, roster }: { chips: TallyChip[]; roster: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] text-text-3">Stances</span>
      {chips.map((c) => {
        const tone = c.stance === "agree" ? "var(--color-green)" : c.stance === "object" ? "var(--color-red)" : "var(--color-text-3)";
        const name = `${c.label} ${STANCE_WORD[c.stance]}${c.was ? `, was ${WAS_WORD[c.was]}` : ""}`;
        return (
          <button key={c.label} type="button" aria-label={name}
            onClick={() => c.seq !== null && document.getElementById(`team-post-${c.seq}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}
            className="inline-flex h-[18px] items-center gap-1 rounded-full border px-1.5 text-[10.5px]"
            style={{ color: tone, borderColor: "var(--color-border-strong)" }}>
            <Avatar label={c.label} roster={roster} size="sm" />
            {c.stance === "agree" ? "✓" : c.stance === "object" ? "✗" : "–"}
            {c.was && <span className="text-text-3">(was {c.was === "agree" ? "✓" : "✗"})</span>}
          </button>
        );
      })}
    </div>
  );
}

function PostCard({ post, footer, tally, roster }: Extract<JourneyItem, { kind: "post" }> & { roster: string[] }) {
  const [open, setOpen] = useState(false);
  const long = post.text.split("\n").length > 6 || post.text.length > 420;
  const assignments = Array.isArray(post.payload.assignments) ? post.payload.assignments as { member?: string; part?: string; files?: string[] }[] : [];
  return (
    <article id={`team-post-${post.seq}`} data-author={post.author}
      className="relative grid gap-1.5 rounded-[10px] border border-border bg-surface px-3 py-2.5">
      <Dot color={identityFor(post.author, roster).color} />
      <div className="flex flex-wrap items-center gap-2">
        <Avatar label={post.author} roster={roster} size="sm" />
        <Name label={post.author} roster={roster} />
        {post.recipient && <><span className="text-text-3">→</span><Avatar label={post.recipient} roster={roster} size="sm" /><Name label={post.recipient} roster={roster} /></>}
        {post.kind === "proposal"
          ? <span className="rounded border px-1.5 font-mono text-[11px] font-semibold" style={{ color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }}>P{post.seq}</span>
          : <Seq seq={post.seq} />}
        <span className="text-[11px] text-text-3">{post.kind === "proposal" ? "proposal" : post.recipient ? "direct message" : post.author === "main" ? "post · from you, via main" : "post"}</span>
        <Time at={post.createdAt} />
      </div>
      <div className={long && !open ? "max-h-[7.6em] overflow-hidden [mask-image:linear-gradient(#000_70%,transparent)]" : undefined}>
        <PostBody text={post.text} />
      </div>
      {long && <button type="button" className="justify-self-start text-[11px] text-accent-ink" onClick={() => setOpen(!open)}>{open ? "Show less" : "Show more"}</button>}
      {assignments.length > 0 && (
        <div className="grid gap-1 rounded-lg border px-2.5 py-2 text-[11.5px]" style={{ background: "var(--color-panel)", borderColor: "var(--hairline)" }}>
          {assignments.map((a, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <span className="w-16 text-[10px] uppercase tracking-[.08em] text-text-3">Assigns</span>
              <Avatar label={String(a.member)} roster={roster} size="sm" /><Name label={String(a.member)} roster={roster} />
              <span className="text-text-2">{a.part}</span>
              {(a.files ?? []).map((f) => <code key={f} className="font-mono text-[11px] text-[var(--color-code)]">{f}</code>)}
            </div>
          ))}
        </div>
      )}
      {tally && <Tally chips={tally} roster={roster} />}
      {(footer.woke.length > 0 || footer.queued.length > 0) && (
        <div data-testid={`footer-p${post.seq}`} className="flex flex-wrap items-center gap-1.5 border-t pt-1.5 text-[11px] text-text-3" style={{ borderColor: "var(--hairline)" }}>
          {footer.woke.length > 0 && <><span style={{ color: "var(--color-amber)" }}>⚡</span>woke {footer.woke.map((l, i) => <span key={l} className="inline-flex items-center gap-1">{i > 0 && " · "}<Avatar label={l} roster={roster} size="sm" />{l}</span>)}</>}
          {footer.queued.length > 0 && <><span style={{ color: "var(--color-code)" }}>↪</span>queued for {footer.queued.map((l, i) => <span key={l} className="inline-flex items-center gap-1">{i > 0 && " · "}<Avatar label={l} roster={roster} size="sm" />{l}</span>)}<span>· it was working — picks this up at its next step</span></>}
        </div>
      )}
    </article>
  );
}

function StanceReply({ post, replaces, roster }: Omit<Extract<JourneyItem, { kind: "stance" }>, "key"> & { roster: string[] }) {
  const tone = post.kind === "agree" ? "var(--color-green)" : post.kind === "object" ? "var(--color-red)" : "var(--color-text-3)";
  const evidence = post.payload.evidence as { files?: string[]; line?: number; command?: string; output?: string; quote_seq?: number } | undefined;
  const verb = post.kind === "agree" ? "agrees with" : post.kind === "object" ? "objects to" : "withdraws";
  return (
    <div id={`team-post-${post.seq}`} data-author={post.author}
      className="ml-3 grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-2 rounded-r-lg py-1.5 pl-2.5 pr-2.5"
      style={{ borderLeft: `2px solid ${tone}`, background: "linear-gradient(90deg, var(--hairline), transparent)" }}>
      <Avatar label={post.author} roster={roster} size="sm" />
      <div className="grid min-w-0 gap-0.5">
        <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
          <Name label={post.author} roster={roster} />
          <span style={{ color: tone }}>{post.kind === "agree" ? "✓ " : post.kind === "object" ? "✗ " : ""}</span>
          <span style={{ color: tone }}>{verb}</span>
          <button type="button" className="font-mono text-[10.5px] font-semibold text-accent-ink"
            onClick={() => document.getElementById(`team-post-${Number(String(post.refId).slice(1))}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>{post.refId}</button>
          <Seq seq={post.seq} />
          {replaces !== null && <span className="text-[11px] text-text-3">replaces #{replaces}</span>}
        </div>
        {post.text && <PostBody text={post.text} className="text-text-2" />}
        {evidence && (
          <details className="text-[11px] text-text-3">
            <summary className="cursor-pointer">evidence</summary>
            {evidence.files && evidence.files.length > 0 && <div className="font-mono">{evidence.line !== undefined ? `${evidence.files[0]}:${evidence.line}` : evidence.files[0]}</div>}
            {evidence.command && <div className="font-mono">$ {evidence.command}</div>}
            {evidence.output && <div className="whitespace-pre-wrap font-mono">{evidence.output}</div>}
            {evidence.quote_seq !== undefined && <div>quotes post #{evidence.quote_seq}</div>}
          </details>
        )}
      </div>
      <Time at={post.createdAt} />
    </div>
  );
}

const CAUSE_TEXT: Record<string, (by: string, seq: string) => string> = {
  kickoff: () => "kickoff",
  mention: (by, seq) => `mentioned by ${by} in ${seq}`,
  team_mention: (by, seq) => `@team from ${by} in ${seq}`,
  message: (by, seq) => `direct message from ${by} ${seq}`,
  main_post: (_by, seq) => `main's post ${seq}`,
  leftover: () => "input that arrived after its last step",
};

/** Plain wording for a lifecycle beat (spec 2026-10-05 §6). */
export function beatText(
  e: TeamActivityView, isProposal: (seq: number) => boolean = () => false,
): { icon: string; tone: string; text: string } {
  const p = e.payload;
  const seqs = (Array.isArray(p.posts) ? p.posts as number[] : []);
  const list = seqs.map((s) => (isProposal(s) ? `P${s}` : `#${s}`)).join(" ");
  const by = String(p.by ?? "");
  const seq = p.post_seq !== undefined && p.post_seq !== null ? `#${String(p.post_seq)}` : "";
  switch (e.kind) {
    case "took_up":
      return { icon: "▶", tone: "var(--color-accent)", text: seqs.length
        ? `${e.label} took up ${list} · ${seqs.length} new post${seqs.length === 1 ? "" : "s"}`
        : `${e.label} started · nothing new on the board` };
    case "picked_up":
      return { icon: "↪", tone: "var(--color-code)", text: `${e.label} picked up ${list} while working` };
    case "woke":
      return { icon: "⚡", tone: "var(--color-amber)", text: `${e.label} woke — ${(CAUSE_TEXT[String(p.cause)] ?? (() => String(p.cause)))(by, seq)}` };
    case "notified":
      return { icon: "↪", tone: "var(--color-code)", text: `${e.label} was working — ${seq} from ${by} queued for its next step` };
    case "capped":
      return { icon: "⏸", tone: "var(--color-text-3)", text: `${e.label} was not woken — ${String(p.wakes)} wakes this phase (limit ${String(p.cap)})` };
    default:
      return { icon: "·", tone: "var(--color-text-3)", text: `${e.label} ${e.kind}` };
  }
}

function Beat({ event, isProposal }: { event: TeamActivityView; isProposal: (seq: number) => boolean }) {
  const b = beatText(event, isProposal);
  return (
    <div data-testid={`beat-a${event.aseq}`} className="relative flex min-h-5 items-center gap-2 text-[11.5px] text-text-2">
      <span aria-hidden="true" className="absolute left-[-17px] top-1/2 -mt-[2.5px] h-[5px] w-[5px] rounded-full" style={{ background: "var(--color-text-4)" }} />
      <span className="w-4 flex-none text-center" style={{ color: b.tone }}>{b.icon}</span>
      <span className="min-w-0 truncate">{b.text}</span>
      <Time at={event.at} />
    </div>
  );
}

const STATUS_CHIP: Record<string, { text: string; color: string; bg: string }> = {
  completed: { text: "✓ completed", color: "var(--color-green)", bg: "var(--green-bg)" },
  awaiting_peer: { text: "⏳ waiting on a teammate", color: "var(--color-text-2)", bg: "transparent" },
  partial: { text: "◐ partial", color: "var(--color-amber)", bg: "var(--amber-bg)" },
  failed: { text: "✗ failed", color: "var(--color-red)", bg: "var(--red-bg)" },
  failed_transient: { text: "⚠ provider unavailable", color: "var(--color-amber)", bg: "var(--amber-bg)" },
  stopped: { text: "■ stopped", color: "var(--color-text-3)", bg: "transparent" },
};

export function wrapStats(p: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof p.duration_ms === "number") parts.push(formatElapsed(p.duration_ms));
  if (typeof p.tools === "number") parts.push(`${p.tools} tool${p.tools === 1 ? "" : "s"}`);
  for (const [key, word] of [["posts", "post"], ["messages", "message"], ["stances", "stance"]] as const) {
    const n = p[key];
    if (typeof n === "number" && n > 0) parts.push(`${n} ${word}${n === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
}

function WrapRow({ event, roster }: { event: TeamActivityView; roster: string[] }) {
  const [open, setOpen] = useState(false);
  const p = event.payload;
  const status = String(p.status ?? "completed");
  const chip = STATUS_CHIP[status] ?? STATUS_CHIP.completed;
  const report = String(p.report ?? "");
  return (
    <div data-testid={`wrap-a${event.aseq}`} className="relative grid gap-1.5 rounded-lg border border-border px-2.5 py-1.5" style={{ background: "var(--color-panel)" }}>
      <Dot color={identityFor(event.label, roster).color} shape="square" />
      <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
        <Avatar label={event.label} roster={roster} size="sm" />
        <Name label={event.label} roster={roster} />{" "}<span>wrapped up</span>
        <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: chip.color, background: chip.bg, borderColor: "var(--color-border-strong)" }}>{chip.text}</span>
        <span className="tabular-nums text-text-3">{wrapStats(p)}</span>
        {typeof p.reason === "string" && <span className="text-text-3">— {p.reason}</span>}
        {report && (
          <button type="button" aria-label={`${open ? "Hide" : "Show"} ${event.label}'s report`} onClick={() => setOpen(!open)}
            className="ml-auto text-[11px] text-accent-ink">report {open ? "▾" : "▸"}</button>
        )}
      </div>
      {open && <div className="border-t pt-1.5" style={{ borderColor: "var(--hairline)" }}><PostBody text={report} /></div>}
    </div>
  );
}

function NowStrip({ teamId, roster }: { teamId: string; roster: string[] }) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const team = teamsUi.teams[teamId];
  const rows = (team?.members ?? []).map((m) => ({ m, row: agentsUi.agents[m.agentId] }));
  const running = rows.some(({ m, row }) => !isTerminalAgent(row?.status ?? m.status));
  const now = useNow(running);
  return (
    <div data-testid="now-strip" aria-live="polite" className="sticky bottom-[-12px] -mx-3.5 -mb-3 mt-3 grid gap-1 px-3.5 pb-3 pt-2.5"
      style={{ background: "linear-gradient(transparent, var(--color-surface-2) 30%)" }}>
      {rows.map(({ m, row }) => {
        const status = row?.status ?? m.status;
        if (!isTerminalAgent(status)) {
          const ms = row ? elapsedMs(row, now) : null;
          return (
            <div key={m.label} className="flex items-center gap-2 text-[11.5px]" style={{ color: "var(--color-accent-ink)" }}>
              <span className="h-[7px] w-[7px] rounded-full" style={{ background: "var(--color-accent)", boxShadow: "0 0 0 3px var(--accent-bg-2)" }} />
              <Avatar label={m.label} roster={roster} size="sm" />
              <span className="min-w-0 truncate">{status === "waiting" ? `${m.label} needs your approval` : `${m.label} is working${row?.now ? ` — ${row.now}` : ""}`}</span>
              {ms !== null && <span className="ml-auto text-[10.5px] tabular-nums text-text-3">{formatElapsed(ms)}</span>}
            </div>
          );
        }
        return (
          <div key={m.label} className="flex items-center gap-2 text-[11.5px] text-text-3">
            <Avatar label={m.label} roster={roster} size="sm" ring="idle" />
            <span>{m.label} is idle</span>
          </div>
        );
      })}
    </div>
  );
}

/** The Board tab (spec 2026-10-05 §6). */
export function Journey({ teamId }: { teamId: string }) {
  const teamsUi = useTeamsUi();
  const [filters, setFilters] = useState<JourneyFilters>(DEFAULT_FILTERS);
  const team = teamsUi.teams[teamId];
  const view = teamsUi.views[teamId];
  const roster = useMemo(() => (team?.members ?? []).map((m) => m.label), [team]);
  const items = useMemo(() => buildJourney(view?.posts ?? [], view?.activity ?? [], roster, filters),
                        [view, roster, filters]);
  const proposals = useMemo(() => new Set((view?.posts ?? []).filter((p) => p.kind === "proposal").map((p) => p.seq)), [view]);
  const isProposal = (seq: number) => proposals.has(seq);
  const toggle = (key: keyof JourneyFilters, name: string) => (
    <button type="button" aria-pressed={filters[key]} onClick={() => setFilters({ ...filters, [key]: !filters[key] })}
      className="h-[22px] rounded-full border px-2.5 text-[11px]"
      style={filters[key]
        ? { color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }
        : { color: "var(--color-text-3)", borderColor: "var(--color-border-strong)" }}>{name}</button>
  );
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-1.5" role="group" aria-label="Show">
        {toggle("posts", "Posts")}{toggle("activity", "Activity")}{toggle("messages", "Messages")}
        <span className="ml-auto text-[10.5px] text-text-3">newest at the bottom</span>
      </div>
      <div className="relative grid gap-2.5 pl-[22px] before:absolute before:bottom-1 before:left-[7px] before:top-1 before:w-[1.5px] before:bg-[var(--color-border-strong)] before:content-['']">
        {items.map((item) => {
          switch (item.kind) {
            case "chapter":
              return (
                <div key={item.key} className="relative -ml-[22px] mb-0.5 mt-1.5 flex items-center gap-2">
                  <span className="grid h-4 w-4 place-items-center rounded-full border-[1.5px]" style={{
                    background: "var(--color-surface-2)",
                    borderColor: item.current ? "var(--color-accent)" : item.ended ? "var(--color-text-3)" : "var(--color-green)" }}>
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: item.current ? "var(--color-accent)" : item.ended ? "var(--color-text-3)" : "var(--color-green)" }} />
                  </span>
                  <h3 className="m-0 text-[11px] font-semibold uppercase tracking-[.08em] text-text-2">{item.title}</h3>
                </div>
              );
            case "post": return <PostCard key={item.key} {...item} roster={roster} />;
            case "stance": return <StanceReply key={item.key} {...item} roster={roster} />;
            case "system":
              return <div key={item.key} data-author="system" className="text-[11px] italic text-text-3">{item.post.text}</div>;
            case "beat": return <Beat key={item.key} event={item.event} isProposal={isProposal} />;
            case "wrap": return <WrapRow key={item.key} event={item.event} roster={roster} />;
            case "gap":
              return (
                <div key={item.key} className="relative text-center text-[10.5px] text-text-4 before:absolute before:inset-x-0 before:top-1/2 before:border-t before:border-dashed before:border-[var(--color-border-strong)] before:content-['']">
                  <span className="relative px-2" style={{ background: "var(--color-surface-2)" }}>· {item.minutes} min later ·</span>
                </div>
              );
          }
        })}
      </div>
      {team && !isTerminalTeam(team.phase) && <NowStrip teamId={teamId} roster={roster} />}
    </div>
  );
}
```

(`toLocaleTimeString` makes clock text locale-dependent; the tests above never assert on it.)

- [ ] **Step 5: `TeamWindow.tsx` header and tabs**

Replace the imports of `TeamBoard` with `Journey`, and add `Avatar` and `PhaseStepper`. The header block (inside `.accent-wash`) becomes:

```tsx
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="grid h-5 w-5 place-items-center rounded-md text-[11px]" style={{ background: "var(--accent-bg)", border: "1px solid var(--accent-brd)", color: "var(--color-accent-ink)" }}>✦</span>
            <span className="truncate text-[14px] font-semibold text-text">{team.name}</span>
            <span className="min-w-0 flex-1 truncate text-[12px] text-text-2">{team.goal}</span>
            <span className="flex items-center gap-1">
              {/* the existing Disband / Confirm disband / Close buttons, unchanged */}
            </span>
          </div>
          <PhaseStepper phase={team.phase} round={team.round} maxRounds={team.maxRounds} />
          <div className="flex flex-wrap items-center gap-3.5">
            {team.members.map((m) => {
              const status = agentsUi.agents[m.agentId]?.status ?? m.status;
              const working = !isTerminalAgent(status);
              return (
                <button key={m.agentId} type="button" aria-label={`Open ${m.label}'s tab`} onClick={() => onTab(m.agentId)}
                  className="flex items-center gap-2 rounded-lg px-1 py-0.5 text-text-2 hover:bg-[var(--hairline)]">
                  <Avatar label={m.label} roster={roster} ring={working ? "working" : "idle"} />
                  <span className="grid text-left leading-tight">
                    <span className="font-semibold" style={{ color: identityFor(m.label, roster).color }}>{m.label}</span>
                    <small className="text-[10.5px] text-text-3">{working ? "working" : status === "awaiting_peer" ? "waiting on a teammate" : "idle"}</small>
                  </span>
                </button>
              );
            })}
            {team.usage.budget > 0 && <span className="ml-auto text-[11px] text-text-3">budget {team.usage.budget} requests</span>}
          </div>
```

with `const roster = team.members.map((m) => m.label);` defined after the `if (!team) return null;` guard, and `isTerminalAgent` imported from `../../agents`, `identityFor` from `../../teamIdentity`. In the tab list, each member tab renders `<Avatar label={m.label} roster={roster} size="sm" />` before the label and keeps its state dot. The board body renders `<Journey teamId={teamId} />` where it rendered `<TeamBoard teamId={teamId} />`. The follow-bottom token becomes `${boardToken}:${teamsUi.views[teamId]?.lastAseq ?? 0}` so new activity also scrolls the board.

Delete `src/components/teams/TeamBoard.tsx` (`git rm`).

- [ ] **Step 6: Run and commit**

Run (from `webview-ui`): `perl -e 'alarm 300; exec @ARGV' npx vitest run > /tmp/c8.txt 2>&1; echo exit=$?; tail -6 /tmp/c8.txt` and `npm run typecheck > /tmp/c8t.txt 2>&1; echo exit=$?`.
Expected: two `exit=0`.

```bash
git add -A apps/vscode-extension/webview-ui/src/components/teams apps/vscode-extension/webview-ui/src/test/teamWindow.test.tsx
git commit -m "feat(webview): the board as a journey — chapters, cards, replies, beats, wrap-ups"
```

### Task 9: The member tab — activation chapters

**Files:**
- Create: `src/teamChapters.ts`, `src/test/teamChapters.test.ts`, `src/components/teams/MemberView.tsx`, `src/test/memberView.test.tsx`
- Modify: `src/components/teams/TeamWindow.tsx` (member tab body), `src/components/teams/Journey.tsx` (export `SaidPost`, `STATUS_CHIP`)

**Interfaces:**
- Consumes: Task 6 (`Avatar`, identity), Task 8 (`PostBody`, `STATUS_CHIP`, `beatText`), `AgentsContext` views (`messages`, `live`, `detail`), `TeamsContext` views (`posts`, `activity`), `MessageRow`, `AgentRow`.
- Produces:
  - `teamChapters.ts`: `ChapterWhy {cause, by, postSeq}`, `MemberChapter {n, why, start, status, durationMs, handed, pickedUp, work, tools, report, said, current}`, `countTools(messages: ChatMsg[]): number`, `buildChapters(label, messages, activity, posts, opts: {working: boolean; fallbackStatus: string}): MemberChapter[]`, `whyText(why: ChapterWhy | null, isProposal: (seq: number) => boolean): string`
  - `<MemberView teamId agentId />` — profile header + chapters
  - `Journey.tsx` exports `SaidPost` (a compact post card) and `STATUS_CHIP`

Chapters (spec 2026-10-05 §7): the transcript is cut at its divider messages (`metadata.divider`, numbered by `metadata.activation`; everything before the first divider is activation 1). Each chapter joins the activity events with the same `activation`: `woke` → why; `took_up` → handed posts and start time; `picked_up` → mid-work pickups; `wrapped_up` → status, duration, report (the transcript's own report message wins when present). The member's posts are assigned to the chapter whose time span holds them. The last chapter is `current` while the member is working.

- [ ] **Step 1: Write the failing tests**

`src/test/teamChapters.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { buildChapters, countTools, whyText } from "../teamChapters";
import type { ChatMsg, TeamActivityView, TeamPostView } from "../types";

const T = (s: number) => new Date(Date.UTC(2026, 9, 5, 17, 10, s)).toISOString();
const msg = (meta: Record<string, unknown>, content = ""): ChatMsg =>
  ({ role: "agent", content, type: "text", timestamp: T(0), metadata: meta });
const pills = (n: number) => msg({ tool_events: Array.from({ length: n }, (_, i) => ({ id: i })) });
const ev = (aseq: number, at: number, kind: string, activation: number,
            payload: Record<string, unknown> = {}, causeSeq: number | null = null): TeamActivityView =>
  ({ teamId: "team-1", aseq, at: T(at), label: "review", kind, activation, causeSeq, payload });
const post = (seq: number, at: number, over: Partial<TeamPostView> = {}): TeamPostView => ({
  teamId: "team-1", seq, author: "review", kind: "post", recipient: null, text: `p${seq}`,
  mentions: [], refId: null, round: 1, payload: {}, closed: null, createdAt: T(at), ...over,
});

const MESSAGES = [
  pills(3), msg({ report: true, status: "completed" }, "first report"),
  msg({ divider: true, activation: 2 }, "↩ woken"),
  pills(2),
];
const ACTIVITY = [
  ev(2, 1, "woke", 1, { cause: "kickoff", by: "main", post_seq: 1 }, 1),
  ev(3, 2, "took_up", 1, { posts: [1], from: ["main"] }),
  ev(5, 20, "wrapped_up", 1, { status: "completed", report: "first report", duration_ms: 18000 }),
  ev(6, 30, "woke", 2, { cause: "mention", by: "impl", post_seq: 5 }, 5),
  ev(7, 31, "took_up", 2, { posts: [5], from: ["impl"] }),
  ev(8, 35, "picked_up", 2, { posts: [7], from: ["impl"] }),
];

describe("buildChapters", () => {
  it("cuts at dividers and joins activity by activation", () => {
    const chapters = buildChapters("review", MESSAGES, ACTIVITY,
      [post(4, 10), post(6, 33)], { working: true, fallbackStatus: "running" });
    expect(chapters.map((c) => c.n)).toEqual([1, 2]);
    const [one, two] = chapters;
    expect(one).toMatchObject({ status: "completed", durationMs: 18000, handed: [1], tools: 3,
                                report: "first report", current: false });
    expect(one.why).toEqual({ cause: "kickoff", by: "main", postSeq: 1 });
    expect(one.said.map((p) => p.seq)).toEqual([4]);
    expect(two).toMatchObject({ status: "working", handed: [5], tools: 2, report: null, current: true });
    expect(two.pickedUp).toEqual([{ posts: [7], at: T(35) }]);
    expect(two.said.map((p) => p.seq)).toEqual([6]);
  });

  it("transcript without dividers is one chapter", () => {
    const chapters = buildChapters("review", [pills(2), msg({ report: true }, "done")], [],
      [post(4, 10), post(5, 11, { author: "impl" })], { working: false, fallbackStatus: "completed" });
    expect(chapters).toHaveLength(1);
    expect(chapters[0]).toMatchObject({ n: 1, why: null, status: "completed", report: "done", tools: 2 });
    expect(chapters[0].said.map((p) => p.seq)).toEqual([4]);
  });

  it("a stopped chapter without a transcript report uses the wrap-up's", () => {
    const chapters = buildChapters("review", [pills(1)],
      [ev(1, 0, "wrapped_up", 1, { status: "stopped", report: "" })], [],
      { working: false, fallbackStatus: "stopped" });
    expect(chapters[0]).toMatchObject({ status: "stopped", report: null });
  });

  it("counts tools and words the reason a chapter started", () => {
    expect(countTools([pills(2), msg({}), pills(1)])).toBe(3);
    const isP = (s: number) => s === 1;
    expect(whyText({ cause: "kickoff", by: "main", postSeq: 1 }, isP)).toBe("kickoff — main's proposal P1");
    expect(whyText({ cause: "mention", by: "impl", postSeq: 5 }, isP)).toBe("woken by impl's post #5");
    expect(whyText({ cause: "message", by: "impl", postSeq: 9 }, isP)).toBe("woken by impl's message #9");
    expect(whyText({ cause: "leftover", by: null, postSeq: null }, isP)).toBe("leftover input");
    expect(whyText(null, isP)).toBe("started");
  });
});
```

`src/test/memberView.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { MemberView } from "../components/teams/MemberView";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AgentDetailView, ChatMsg, TeamActivityView, TeamPostView, TeamSummaryView } from "../types";

const T = (s: number) => new Date(Date.UTC(2026, 9, 5, 17, 10, s)).toISOString();
const msg = (meta: Record<string, unknown>, content = ""): ChatMsg =>
  ({ role: "agent", content, type: "text", timestamp: T(0), metadata: meta });
const ev = (aseq: number, at: number, kind: string, activation: number,
            payload: Record<string, unknown> = {}, causeSeq: number | null = null): TeamActivityView =>
  ({ teamId: "team-1", aseq, at: T(at), label: "review", kind, activation, causeSeq, payload });
const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 1, maxRounds: 3,
  pausedReason: null,
  members: [{ label: "review", agentId: "agent-r", status: "running", name: "explore", description: "Reads code and cites path:line" },
            { label: "impl", agentId: "agent-i", status: "completed", name: "general-purpose", description: "Edits" }],
  openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: T(0),
};
const POSTS: TeamPostView[] = [
  { teamId: "team-1", seq: 1, author: "main", kind: "proposal", recipient: null, text: "Plan **one**",
    mentions: [], refId: null, round: 0, payload: {}, closed: null, createdAt: T(0) },
  { teamId: "team-1", seq: 4, author: "review", kind: "object", recipient: null, text: "gap", mentions: [],
    refId: "P1", round: 1, payload: {}, closed: null, createdAt: T(10) },
  { teamId: "team-1", seq: 5, author: "impl", kind: "post", recipient: null, text: "@review see this", mentions: ["review"],
    refId: null, round: 1, payload: {}, closed: null, createdAt: T(25) },
];
const DETAIL = {
  agentId: "agent-r", parentAgentId: null, depth: 1, name: "explore", label: "review", status: "running",
  now: "read_file shop/cart.py", toolCount: 3, filesChangedCount: 0, startedAt: T(0), endedAt: null,
  reportPreview: "", prompt: "Member review", report: "", filesChanged: [], staleRefusals: 0,
  transcript: [], lastSeq: 0,
} as AgentDetailView;

function renderView() {
  const messages = [msg({ tool_events: [{ id: 1, tool: "read_file", args: {}, source: "execution", done: true }] }),
    msg({ report: true, status: "completed" }, "Objected: **missing case**"),
    msg({ divider: true, activation: 2 }, "↩ woken")];
  const agentsUi: AgentsUi = {
    agents: { "agent-r": { ...DETAIL } }, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
    views: { "agent-r": { detail: DETAIL, messages, live: [], callIds: {}, nextId: 1 } },
  };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM }, openTeam: vi.fn(), views: { "team-1": {
    posts: POSTS, lastSeq: 5, lastAseq: 7, activity: [
      ev(1, 1, "woke", 1, { cause: "kickoff", by: "main", post_seq: 1 }, 1),
      ev(2, 2, "took_up", 1, { posts: [1], from: ["main"] }),
      ev(3, 20, "wrapped_up", 1, { status: "completed", report: "Objected", duration_ms: 18000, tools: 1 }),
      ev(4, 26, "woke", 2, { cause: "mention", by: "impl", post_seq: 5 }, 5),
      ev(5, 27, "took_up", 2, { posts: [5], from: ["impl"] }),
    ] } } };
  render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>
    <MemberView teamId="team-1" agentId="agent-r" />
  </TeamsContext.Provider></AgentsContext.Provider>);
}

describe("MemberView", () => {
  it("profile: name, role, description, state, stats and stances", () => {
    renderView();
    const profile = screen.getByTestId("member-profile");
    expect(profile).toHaveTextContent("review");
    expect(profile).toHaveTextContent("explore");
    expect(profile).toHaveTextContent("Reads code and cites path:line");
    expect(profile).toHaveTextContent("working");
    expect(profile).toHaveTextContent("2 chapters");
    expect(profile).toHaveTextContent("2 wakes");
    expect(screen.getByText("P1 ✗ objected")).toBeInTheDocument();
  });

  it("chapters: why each started, the latest open with what it was handed", () => {
    renderView();
    expect(screen.getByRole("button", { name: /Chapter 1 · kickoff — main's proposal P1/ })).toBeInTheDocument();
    const two = screen.getByRole("button", { name: /Chapter 2 · woken by impl's post #5/ });
    expect(two).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("handed-2")).toHaveTextContent("#5");
    expect(screen.getByTestId("chapter-2")).toHaveTextContent("working — read_file shop/cart.py");
  });

  it("an earlier chapter is collapsed to its report's first line and opens on click", () => {
    renderView();
    const one = screen.getByRole("button", { name: /Chapter 1/ });
    expect(one).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("chapter-1")).toHaveTextContent("Objected: missing case");
    fireEvent.click(one);
    expect(screen.getByText("missing case").tagName).toBe("STRONG");
    expect(screen.getByTestId("chapter-1")).toHaveTextContent("objects to");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (from `webview-ui`): `perl -e 'alarm 120; exec @ARGV' npx vitest run src/test/teamChapters.test.ts src/test/memberView.test.tsx > /tmp/c9.txt 2>&1; echo exit=$?; tail -12 /tmp/c9.txt`
Expected: exit≠0 (`../teamChapters` does not exist).

- [ ] **Step 3: `src/teamChapters.ts`**

```ts
import type { ChatMsg, TeamActivityView, TeamPostView } from "./types";

// A member's own journey (spec 2026-10-05 §7): one chapter per activation.

export interface ChapterWhy { cause: string; by: string | null; postSeq: number | null }

export interface MemberChapter {
  n: number;
  why: ChapterWhy | null;
  start: string | null;
  status: string;
  durationMs: number | null;
  handed: number[];
  pickedUp: { posts: number[]; at: string }[];
  work: ChatMsg[];          // the chapter's transcript messages, report excluded
  tools: number;
  report: string | null;
  said: TeamPostView[];     // the member's posts during the chapter
  current: boolean;
}

export function countTools(messages: ChatMsg[]): number {
  return messages.reduce((n, m) => {
    const events = m.metadata?.tool_events;
    return n + (Array.isArray(events) ? events.length : 0);
  }, 0);
}

const seqs = (v: unknown): number[] => (Array.isArray(v) ? v.filter((x): x is number => typeof x === "number") : []);

export function buildChapters(
  label: string, messages: ChatMsg[], activity: TeamActivityView[], posts: TeamPostView[],
  opts: { working: boolean; fallbackStatus: string },
): MemberChapter[] {
  const segments: { n: number; messages: ChatMsg[] }[] = [{ n: 1, messages: [] }];
  for (const m of messages) {
    if (m.metadata?.divider === true) {
      const n = typeof m.metadata.activation === "number" ? m.metadata.activation : segments.length + 1;
      segments.push({ n, messages: [] });
      continue;
    }
    segments[segments.length - 1].messages.push(m);
  }
  const mine = activity.filter((e) => e.label === label);
  const chapters = segments.map((seg, i): MemberChapter => {
    const events = mine.filter((e) => e.activation === seg.n);
    const woke = events.find((e) => e.kind === "woke");
    const took = events.find((e) => e.kind === "took_up");
    const wrap = events.find((e) => e.kind === "wrapped_up");
    const reportMsg = seg.messages.find((m) => m.metadata?.report === true);
    const wrapReport = typeof wrap?.payload.report === "string" && wrap.payload.report ? wrap.payload.report : null;
    const last = i === segments.length - 1;
    const current = last && opts.working;
    return {
      n: seg.n,
      why: woke ? { cause: String(woke.payload.cause ?? ""), by: (woke.payload.by as string | null) ?? null,
                    postSeq: (woke.payload.post_seq as number | null) ?? woke.causeSeq } : null,
      start: took?.at ?? woke?.at ?? null,
      status: wrap ? String(wrap.payload.status ?? "completed") : current ? "working" : last ? opts.fallbackStatus : "completed",
      durationMs: typeof wrap?.payload.duration_ms === "number" ? wrap.payload.duration_ms : null,
      handed: seqs(took?.payload.posts),
      pickedUp: events.filter((e) => e.kind === "picked_up").map((e) => ({ posts: seqs(e.payload.posts), at: e.at })),
      work: seg.messages.filter((m) => m.metadata?.report !== true),
      tools: countTools(seg.messages),
      report: reportMsg ? reportMsg.content : wrapReport,
      said: [],
      current,
    };
  });
  // Assign the member's posts by time: a chapter holds what was written from its start
  // until the next chapter's start (a chapter with no start opens at the beginning).
  const authored = posts.filter((p) => p.author === label);
  chapters.forEach((c, i) => {
    const from = c.start ? Date.parse(c.start) : Number.NEGATIVE_INFINITY;
    const next = chapters[i + 1]?.start;
    const to = next ? Date.parse(next) : Number.POSITIVE_INFINITY;
    c.said = authored.filter((p) => {
      const at = Date.parse(p.createdAt);
      return at >= from && at < to;
    });
  });
  return chapters;
}

export function whyText(why: ChapterWhy | null, isProposal: (seq: number) => boolean): string {
  if (!why) return "started";
  const ref = why.postSeq === null ? "" : isProposal(why.postSeq) ? `P${why.postSeq}` : `#${why.postSeq}`;
  switch (why.cause) {
    case "kickoff": return `kickoff — main's proposal ${ref}`.trim();
    case "mention": return `woken by ${why.by}'s post ${ref}`;
    case "team_mention": return `woken by ${why.by}'s @team post ${ref}`;
    case "message": return `woken by ${why.by}'s message ${ref}`;
    case "main_post": return `woken by main's post ${ref}`;
    case "leftover": return "leftover input";
    default: return why.cause;
  }
}
```

In `teamChapters.test.ts`'s first test, chapter 1's said list is `[4]` because post 4 (t=10) falls between chapter 1's start (t=2) and chapter 2's start (t=31); post 6 (t=33) falls in chapter 2.

- [ ] **Step 4: `MemberView.tsx`**

First, in `Journey.tsx`, export the status chip map (`export const STATUS_CHIP`) and add an exported compact card after `StanceReply`:

```tsx
/** A post as shown inside a member's chapter: same identity and body, no spine dot. */
export function SaidPost({ post, roster }: { post: TeamPostView; roster: string[] }) {
  if (post.kind === "agree" || post.kind === "object" || post.kind === "withdraw") {
    return <StanceReply kind="stance" at={post.createdAt} post={post} replaces={null} roster={roster} />;
  }
  return (
    <article className="grid gap-1 rounded-[10px] border border-border bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Avatar label={post.author} roster={roster} size="sm" />
        <Name label={post.author} roster={roster} />
        {post.recipient && <><span className="text-text-3">→</span><Name label={post.recipient} roster={roster} /></>}
        <Seq seq={post.seq} />
        <span className="text-[11px] text-text-3">{post.recipient ? "direct message" : post.kind}</span>
        <Time at={post.createdAt} />
      </div>
      <PostBody text={post.text} />
    </article>
  );
}
```

(`StanceReply`'s props already omit `key` — Task 8 — so `SaidPost` passes only the fields.)

`src/components/teams/MemberView.tsx`:

```tsx
import { useMemo, useState } from "react";
import { formatElapsed, isTerminalAgent } from "../../agents";
import { buildChapters, whyText, type MemberChapter } from "../../teamChapters";
import { identityFor } from "../../teamIdentity";
import type { TeamPostView, ToolEventView } from "../../types";
import { MessageRow } from "../MessageRow";
import { AgentRow } from "../messages/AgentRow";
import { useAgentsUi } from "../agents/AgentsContext";
import { Avatar } from "./Avatar";
import { SaidPost, STATUS_CHIP, beatText } from "./Journey";
import { PostBody } from "./PostBody";
import { useTeamsUi } from "./TeamsContext";

const STATE_WORD: Record<string, string> = {
  running: "working", queued: "working", waiting: "needs your approval",
  awaiting_peer: "waiting on a teammate", failed: "failed", stopped: "stopped",
};

const firstLine = (text: string) =>
  text.split("\n").map((l) => l.replace(/[*_`#>]/g, "").trim()).find((l) => l) ?? "";

/** A member's own journey (spec 2026-10-05 §7). */
export function MemberView({ teamId, agentId }: { teamId: string; agentId: string }) {
  const agentsUi = useAgentsUi();
  const teamsUi = useTeamsUi();
  const team = teamsUi.teams[teamId];
  const tview = teamsUi.views[teamId];
  const aview = agentsUi.views[agentId];
  const row = agentsUi.agents[agentId];
  const member = team?.members.find((m) => m.agentId === agentId);
  const roster = useMemo(() => (team?.members ?? []).map((m) => m.label), [team]);
  const posts = tview?.posts ?? [];
  const status = row?.status ?? member?.status ?? "idle";
  const working = !isTerminalAgent(status);
  const chapters = useMemo(() => (member && aview
    ? buildChapters(member.label, aview.messages, tview?.activity ?? [], posts,
                    { working, fallbackStatus: status })
    : []), [member, aview, tview, posts, working, status]);
  const isProposal = (seq: number) => posts.some((p) => p.seq === seq && p.kind === "proposal");
  if (!member) return null;
  if (!aview) return <div className="text-[11px] text-text-3">Loading…</div>;

  const label = member.label;
  const wakes = (tview?.activity ?? []).filter((e) => e.label === label && e.kind === "woke").length;
  const tools = chapters.reduce((n, c) => n + c.tools, 0) + (working ? aview.live.length : 0);
  const active = chapters.reduce((n, c) => n + (c.durationMs ?? 0), 0);
  const mine = posts.filter((p) => p.author === label);
  const openProposals = posts.filter((p) => p.kind === "proposal" && p.closed === null);
  const stances = openProposals.map((p) => {
    const last = [...mine].reverse().find((q) => q.refId === `P${p.seq}` && (q.kind === "agree" || q.kind === "object"));
    return { id: `P${p.seq}`, stance: last?.kind ?? null };
  });
  return (
    <div className="grid gap-3.5">
      <section data-testid="member-profile" className="grid grid-cols-[auto_minmax(0,1fr)] gap-3 rounded-[10px] border border-border bg-surface p-3">
        <Avatar label={label} roster={roster} size="lg" ring={working ? "working" : "idle"} />
        <div className="grid min-w-0 gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-[14px]" style={{ color: identityFor(label, roster).color }}>{label}</strong>
            {member.name && <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }}>{member.name}</span>}
            <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: working ? "var(--color-accent-ink)" : "var(--color-text-3)", borderColor: "var(--color-border-strong)" }}>
              ● {STATE_WORD[status] ?? "idle"}
            </span>
          </div>
          {member.description && <div className="text-[12px] text-text-2">{member.description}</div>}
          <div className="flex flex-wrap gap-3.5 text-[11px] text-text-3">
            <span><b className="text-text">{chapters.length}</b> chapter{chapters.length === 1 ? "" : "s"}</span>
            <span><b className="text-text">{wakes}</b> wake{wakes === 1 ? "" : "s"}</span>
            <span><b className="text-text">{tools}</b> tools</span>
            <span><b className="text-text">{mine.filter((p) => p.recipient === null && p.kind === "post").length}</b> posts</span>
            <span><b className="text-text">{mine.filter((p) => p.recipient !== null).length}</b> messages</span>
            {active > 0 && <span><b className="text-text">{formatElapsed(active)}</b> active</span>}
          </div>
          {stances.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-text-3">Stances
              {stances.map((s) => (
                <span key={s.id} className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]"
                  style={{ color: s.stance === "agree" ? "var(--color-green)" : s.stance === "object" ? "var(--color-red)" : "var(--color-text-3)", borderColor: "var(--color-border-strong)" }}>
                  {s.id} {s.stance === "agree" ? "✓ agreed" : s.stance === "object" ? "✗ objected" : "– no stance"}
                </span>
              ))}
            </div>
          )}
        </div>
      </section>
      <div className="relative grid gap-2.5 pl-[22px] before:absolute before:bottom-2 before:left-[7px] before:top-2 before:w-[1.5px] before:bg-[var(--color-border-strong)] before:content-['']">
        {chapters.map((c, i) => (
          <Chapter key={c.n} chapter={c} defaultOpen={i === chapters.length - 1} label={label} roster={roster}
            posts={posts} isProposal={isProposal} live={c.current ? aview.live : []} now={row?.now ?? ""} />
        ))}
      </div>
    </div>
  );
}

function Chapter({ chapter: c, defaultOpen, label, roster, posts, isProposal, live, now }: {
  chapter: MemberChapter; defaultOpen: boolean; label: string; roster: string[]; posts: TeamPostView[];
  isProposal: (seq: number) => boolean; live: ToolEventView[]; now: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [showWork, setShowWork] = useState(false);
  const chip = STATUS_CHIP[c.status];
  const color = identityFor(label, roster).color;
  const postBySeq = (seq: number) => posts.find((p) => p.seq === seq);
  const ref = (seq: number) => (isProposal(seq) ? `P${seq}` : `#${seq}`);
  return (
    <div data-testid={`chapter-${c.n}`} className="relative rounded-[10px] border border-border bg-surface">
      <span aria-hidden="true" className="absolute left-[-24px] top-[9px] grid h-[18px] w-[18px] place-items-center rounded-full text-[10px] font-semibold"
        style={{ background: color, color: "var(--color-panel)", boxShadow: "0 0 0 3px var(--color-surface-2)" }}>{c.n}</span>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left text-text">
        <span className="text-text-3">{open ? "▾" : "▸"}</span>
        <strong>Chapter {c.n}</strong>{" "}
        <span className="text-[12px] text-text-2">· {whyText(c.why, isProposal)}</span>
        <span className="ml-auto flex items-center gap-2">
          {c.start && <span className="text-[10.5px] tabular-nums text-text-3">{new Date(c.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
          {c.durationMs !== null && <span className="text-[11px] text-text-3">{formatElapsed(c.durationMs)}</span>}
          {c.current
            ? <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }}>● working</span>
            : chip && <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: chip.color, background: chip.bg, borderColor: "var(--color-border-strong)" }}>{chip.text}</span>}
        </span>
      </button>
      {!open && c.report && <div className="px-3 pb-2.5 pl-[30px] text-[11.5px] text-text-3">{firstLine(c.report)}</div>}
      {open && (
        <div className="grid gap-2.5 px-3 pb-3">
          {c.handed.length > 0 && (
            <div className="grid gap-1.5">
              <span className="text-[10px] uppercase tracking-[.08em] text-text-3">Handed · {c.handed.length} post{c.handed.length === 1 ? "" : "s"}</span>
              <div data-testid={`handed-${c.n}`} className="flex flex-wrap gap-1.5">
                {c.handed.map((seq) => {
                  const p = postBySeq(seq);
                  return (
                    <span key={seq} className="inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-[11.5px] text-text-2" style={{ background: "var(--color-panel)", borderColor: "var(--color-border-strong)" }}>
                      <span className="font-mono text-[10.5px] font-semibold text-text-3">{ref(seq)}</span>
                      {p && <><Avatar label={p.author} roster={roster} size="sm" /><span className="min-w-0 truncate">{p.author} · {firstLine(p.text)}</span></>}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
          {c.pickedUp.map((pu) => (
            <div key={pu.at} className="text-[11.5px] text-text-2">
              <span style={{ color: "var(--color-code)" }}>↪ </span>
              {beatText({ teamId: "", aseq: 0, at: pu.at, label, kind: "picked_up", activation: c.n, causeSeq: null, payload: { posts: pu.posts } }, isProposal).text}
            </div>
          ))}
          {(c.work.length > 0 || live.length > 0) && (
            <div className="grid gap-1.5">
              <span className="text-[10px] uppercase tracking-[.08em] text-text-3">Work</span>
              <button type="button" onClick={() => setShowWork(!showWork)}
                className="justify-self-start rounded-md border border-dashed px-2.5 py-1 text-[11.5px] text-text-2" style={{ borderColor: "var(--color-border-strong)" }}>
                Explored · {c.tools + live.length} tool{c.tools + live.length === 1 ? "" : "s"} {showWork ? "▾" : "▸"}
              </button>
              {showWork && <div className="grid gap-1">{c.work.map((m, i) => <MessageRow key={i} msg={m} />)}
                {live.length > 0 && <AgentRow content="" toolEvents={live} />}</div>}
            </div>
          )}
          {c.said.length > 0 && (
            <div className="grid gap-1.5">
              <span className="text-[10px] uppercase tracking-[.08em] text-text-3">Said</span>
              {c.said.map((p) => <SaidPost key={p.seq} post={p} roster={roster} />)}
            </div>
          )}
          {c.report && (
            <div className="grid gap-1.5 rounded-[9px] border px-3 py-2"
              style={{ borderColor: "var(--green-brd)", background: "linear-gradient(180deg, var(--green-bg), transparent 60%), var(--color-panel)" }}>
              <div className="flex items-center gap-2 text-[11.5px]">{chip && <span style={{ color: chip.color }}>{chip.text}</span>}<strong>Report</strong></div>
              <PostBody text={c.report} />
            </div>
          )}
          {c.current && (
            <div className="flex items-center gap-2 text-[11.5px]" style={{ color: "var(--color-accent-ink)" }}>
              <span className="h-[7px] w-[7px] rounded-full" style={{ background: "var(--color-accent)", boxShadow: "0 0 0 3px var(--accent-bg-2)" }} />
              working{now ? ` — ${now}` : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

`ToolEventView` is the existing pill type in `src/types.ts` (the one `AgentViewState.live` holds).

- [ ] **Step 5: Use it in `TeamWindow.tsx`**

The tab body becomes `{tab === "board" ? <Journey teamId={teamId} /> : <MemberView teamId={teamId} agentId={tab} />}`; drop the `AgentTranscript` import but keep `viewToken` (still used for the follow-bottom token).

- [ ] **Step 6: Run and commit**

Run (from `webview-ui`): `perl -e 'alarm 300; exec @ARGV' npx vitest run > /tmp/c9.txt 2>&1; echo exit=$?; tail -6 /tmp/c9.txt` and `npm run typecheck > /tmp/c9t.txt 2>&1; echo exit=$?`.
Expected: two `exit=0`.

```bash
git add apps/vscode-extension/webview-ui/src/teamChapters.ts apps/vscode-extension/webview-ui/src/components/teams apps/vscode-extension/webview-ui/src/test/teamChapters.test.ts apps/vscode-extension/webview-ui/src/test/memberView.test.tsx
git commit -m "feat(webview): member tab with activation chapters"
```

### Task 10: The transcript card

**Files:**
- Modify: `src/components/teams/TeamCard.tsx` (rewrite), `src/teams.ts` (`memberPhrase`, `latestText`, `countsText`)
- Rewrite: `src/test/teamCard.test.tsx`; append to `src/test/teams.test.ts`

**Interfaces:**
- Consumes: Task 6 (`TeamMemberView.last`, `TeamSummaryView.latest/counts`, `Avatar`), Task 8 (`PhaseStepper`), `AgentsContext`, `useNow`, `formatElapsed`, `elapsedMs`.
- Produces (`src/teams.ts`):
  - `memberPhrase(member: TeamMemberView, row: AgentSummaryView | undefined, now: number): {text: string; tone: "work" | "ok" | "wait" | "bad" | "idle"}`
  - `latestText(latest: TeamLatestView): string`
  - `countsText(counts: TeamCountsView | undefined, budget: number): string`
  - `<TeamCard teamId agentIds name? />` per spec §8

- [ ] **Step 1: Write the failing tests**

Append to `src/test/teams.test.ts`:

```ts
import { countsText, latestText, memberPhrase } from "../teams";

describe("card wording", () => {
  const NOW = Date.parse("2026-10-05T17:12:00Z");
  const member = (over: object = {}) => ({ label: "review", agentId: "a", status: "completed", last: null, ...over });
  const row = (over: object = {}) => ({ agentId: "a", parentAgentId: null, depth: 1, name: "explore",
    label: "review", status: "running", now: "read_file shop/cart.py", toolCount: 3, filesChangedCount: 0,
    startedAt: "2026-10-05T17:00:00Z", endedAt: null, reportPreview: "",
    activationStartedAt: "2026-10-05T17:11:28Z", activationEndedAt: null, ...over });
  it("working comes from the agent row, with elapsed time", () => {
    expect(memberPhrase(member(), row(), NOW)).toEqual(
      { text: "● working · read_file shop/cart.py · 32s", tone: "work" });
  });
  it("finished states come from the member's last event", () => {
    const at = "2026-10-05T17:10:00Z";
    const done = member({ last: { kind: "wrapped_up", at, causeSeq: null, by: null, status: "completed", activation: 1 } });
    expect(memberPhrase(done, row({ status: "completed" }), NOW)).toEqual({ text: "✓ reported · 2m00s ago", tone: "ok" });
    const waiting = member({ last: { kind: "wrapped_up", at, causeSeq: null, by: null, status: "awaiting_peer", activation: 1 } });
    expect(memberPhrase(waiting, row({ status: "awaiting_peer" }), NOW).text).toBe("⏳ waiting on a teammate");
    const failed = member({ last: { kind: "wrapped_up", at, causeSeq: null, by: null, status: "failed", activation: 1 } });
    expect(memberPhrase(failed, row({ status: "failed" }), NOW)).toEqual({ text: "✗ failed", tone: "bad" });
  });
  it("needs approval, and no history", () => {
    expect(memberPhrase(member(), row({ status: "waiting" }), NOW)).toEqual({ text: "⏸ needs your approval", tone: "wait" });
    expect(memberPhrase(member({ status: "awaiting_peer" }), undefined, NOW)).toEqual({ text: "💤 idle", tone: "idle" });
  });
  it("latest and counts", () => {
    expect(latestText({ kind: "post", label: "review", text: "@impl findings for P1", at: "x" })).toBe("review posted: @impl findings for P1");
    expect(latestText({ kind: "activity", label: "impl", text: "Agreed with P1", at: "x", event: "wrapped_up", status: "completed" }))
      .toBe("impl wrapped up — Agreed with P1");
    expect(countsText({ posts: 9, proposals: [{ id: "P1", agree: 2, object: 0, pending: 0 }] }, 60))
      .toBe("9 posts · P1 2 ✓ · budget 60 requests");
    expect(countsText({ posts: 3, proposals: [{ id: "P1", agree: 1, object: 1, pending: 1 }] }, 0))
      .toBe("3 posts · P1 1 ✓ 1 ✗ 1 pending");
  });
});
```

(Move the `import` line to the top of the file with the other imports.)

`src/test/teamCard.test.tsx` (replacing the file):

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { MessageRow } from "../components/MessageRow";
import { TeamCard } from "../components/teams/TeamCard";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AgentSummaryView, TeamSummaryView } from "../types";

const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "discount-feature", goal: "g", phase: "DELIBERATING", round: 1, maxRounds: 3,
  pausedReason: null,
  members: [
    { label: "review", agentId: "agent-r", status: "completed", name: "explore",
      last: { kind: "wrapped_up", at: new Date(Date.now() - 120_000).toISOString(), causeSeq: null, by: null, status: "completed", activation: 1 } },
    { label: "impl", agentId: "agent-i", status: "running", name: "general-purpose", last: null },
  ],
  openProposals: [], usage: { requests: 0, budget: 60 }, createdAt: "2026-10-05T00:00:00Z",
  latest: { kind: "post", label: "review", text: "confirmed the 50% cap", at: new Date().toISOString() },
  counts: { posts: 9, proposals: [{ id: "P1", agree: 2, object: 0, pending: 0 }] },
};
const row = (id: string, label: string, status: string, tools: number, acts: number): AgentSummaryView => ({
  agentId: id, parentAgentId: null, depth: 1, name: "x", label, status, now: "read_file shop/discounts.py",
  toolCount: tools, filesChangedCount: 0, startedAt: new Date(Date.now() - 60_000).toISOString(), endedAt: null,
  reportPreview: "", activationCount: acts, activationStartedAt: new Date(Date.now() - 32_000).toISOString(),
  activationEndedAt: null,
});

function wrap(node: ReactNode, teams: Partial<TeamsUi> = {}) {
  const agentsUi: AgentsUi = {
    agents: { "agent-r": row("agent-r", "review", "completed", 14, 2), "agent-i": row("agent-i", "impl", "running", 7, 3) },
    views: {}, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
  };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM }, views: {}, openTeam: vi.fn(), ...teams };
  return { teamsUi, ...render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>{node}</TeamsContext.Provider></AgentsContext.Provider>) };
}

describe("TeamCard", () => {
  it("shows the stepper, a state row per member, the latest line and counts", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r", "agent-i"]} />);
    const card = screen.getByTestId("team-card");
    expect(card).toHaveTextContent("discount-feature");
    expect(card).toHaveTextContent("Deliberating · round 1 of 3");
    expect(screen.getByTestId("member-row-review")).toHaveTextContent(/✓ reported · 2m0\ds ago/);
    expect(screen.getByTestId("member-row-review")).toHaveTextContent("2 ch · 14 tools");
    expect(screen.getByTestId("member-row-impl")).toHaveTextContent("● working · read_file shop/discounts.py");
    expect(card).toHaveTextContent("review posted: confirmed the 50% cap");
    expect(card).toHaveTextContent("9 posts · P1 2 ✓ · budget 60 requests");
  });

  it("Open board opens the team window", () => {
    const { teamsUi } = wrap(<TeamCard teamId="team-1" agentIds={["agent-r", "agent-i"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open board" }));
    expect(teamsUi.openTeam).toHaveBeenCalledWith("team-1");
  });

  it("a paused team shows its reason; an ended team shows how it ended", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-r"]} />, {
      teams: { "team-1": { ...TEAM, phase: "FAILED", pausedReason: "budget reached" } } });
    expect(screen.getByTestId("team-card")).toHaveTextContent("Failed");
    expect(screen.getByTestId("team-card")).toHaveTextContent("budget reached");
  });

  it("MessageRow renders the card for a team_created message, before summaries load", () => {
    wrap(<MessageRow msg={{ role: "agent", content: "", type: "team_created", timestamp: "2026-10-05T00:00:00Z",
      metadata: { team_id: "team-9", name: "cache", agent_ids: ["agent-r"] } }} />, { teams: {} });
    expect(screen.getByText("cache")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (from `webview-ui`): `perl -e 'alarm 120; exec @ARGV' npx vitest run src/test/teamCard.test.tsx src/test/teams.test.ts > /tmp/c10.txt 2>&1; echo exit=$?; tail -12 /tmp/c10.txt`
Expected: exit≠0 (`memberPhrase` is not exported).

- [ ] **Step 3: Wording** (`src/teams.ts`)

```ts
import { elapsedMs, formatElapsed, isTerminalAgent } from "./agents";
import type { TeamCountsView, TeamLatestView, TeamMemberView } from "./types";

export type PhraseTone = "work" | "ok" | "wait" | "bad" | "idle";

/** A member's one-line state on the transcript card (spec 2026-10-05 §8): live work from the
 * agent row, everything after from the member's last activity event. */
export function memberPhrase(
  member: TeamMemberView, row: AgentSummaryView | undefined, now: number,
): { text: string; tone: PhraseTone } {
  const status = row?.status ?? member.status;
  if (status === "waiting") return { text: "⏸ needs your approval", tone: "wait" };
  if (!isTerminalAgent(status)) {
    const ms = row ? elapsedMs(row, now) : null;
    const doing = row?.now ? ` · ${row.now}` : "";
    return { text: `● working${doing}${ms !== null ? ` · ${formatElapsed(ms)}` : ""}`, tone: "work" };
  }
  const last = member.last;
  if (last?.kind === "wrapped_up") {
    const ago = `${formatElapsed(Math.max(0, now - Date.parse(last.at)))} ago`;
    switch (last.status) {
      case "completed": return { text: `✓ reported · ${ago}`, tone: "ok" };
      case "partial": return { text: `◐ reported partial · ${ago}`, tone: "wait" };
      case "awaiting_peer": return { text: "⏳ waiting on a teammate", tone: "idle" };
      case "stopped": return { text: "■ stopped", tone: "idle" };
      case "failed": return { text: "✗ failed", tone: "bad" };
      default: return { text: `${last.status ?? "ended"} · ${ago}`, tone: "idle" };
    }
  }
  if (last?.kind === "capped") return { text: "⏸ wake limit reached this phase", tone: "wait" };
  if (status === "failed") return { text: "✗ failed", tone: "bad" };
  return { text: "💤 idle", tone: "idle" };
}

export function latestText(latest: TeamLatestView): string {
  if (latest.kind === "post") return `${latest.label} posted: ${latest.text}`;
  if (latest.event === "wrapped_up") return `${latest.label} wrapped up — ${latest.text || latest.status || ""}`.trim();
  return `team ${latest.text.toLowerCase()}`;
}

export function countsText(counts: TeamCountsView | undefined, budget: number): string {
  const parts: string[] = [];
  if (counts) {
    parts.push(`${counts.posts} post${counts.posts === 1 ? "" : "s"}`);
    for (const p of counts.proposals) {
      const tally = [p.agree ? `${p.agree} ✓` : "", p.object ? `${p.object} ✗` : "",
                     p.pending ? `${p.pending} pending` : ""].filter(Boolean).join(" ");
      parts.push(`${p.id} ${tally}`.trim());
    }
  }
  if (budget > 0) parts.push(`budget ${budget} requests`);
  return parts.join(" · ");
}
```

(`AgentSummaryView` is already imported in `teams.ts` for `waitingOn`; merge the import lines.)

- [ ] **Step 4: `TeamCard.tsx`** (replacing the file)

```tsx
import { isTerminalAgent } from "../../agents";
import { countsText, isTerminalTeam, latestText, memberPhrase } from "../../teams";
import { identityFor } from "../../teamIdentity";
import type { TeamSummaryView } from "../../types";
import { rosterRow } from "../agents/AgentRosterCard";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { Avatar } from "./Avatar";
import { PhaseStepper } from "./PhaseStepper";
import { useTeamsUi } from "./TeamsContext";

const TONE: Record<string, string> = {
  work: "var(--color-accent-ink)", ok: "var(--color-text-2)", wait: "var(--color-amber)",
  bad: "var(--color-red)", idle: "var(--color-text-3)",
};

function placeholder(teamId: string, name: string, agentIds: string[], labels: string[]): TeamSummaryView {
  return { teamId, name, goal: "", phase: "DELIBERATING", round: 1, maxRounds: 0, pausedReason: null,
    members: agentIds.map((agentId, i) => ({ label: labels[i], agentId, status: "queued" })),
    openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: "" };
}

/** A team in the transcript (spec 2026-10-05 §8), anchored by its team_created message. */
export function TeamCard({ teamId, agentIds, name = "team" }: {
  teamId: string; agentIds: string[]; name?: string;
}) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const team = teamsUi.teams[teamId]
    ?? placeholder(teamId, name, agentIds, agentIds.map((id) => rosterRow(agentsUi.agents, id).label));
  const roster = team.members.map((m) => m.label);
  const ended = isTerminalTeam(team.phase);
  const anyWorking = team.members.some((m) => !isTerminalAgent(agentsUi.agents[m.agentId]?.status ?? m.status));
  const now = useNow(!ended && anyWorking);
  const counts = countsText(team.counts, team.usage.budget);
  return (
    <div className="surface-card overflow-hidden" data-testid="team-card">
      <div className="accent-wash grid gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
        <div className="flex items-center gap-2">
          <Avatar label="main" roster={roster} size="sm" />
          <span className="truncate text-[13px] font-semibold text-text">{team.name}</span>
          <button type="button" onClick={() => teamsUi.openTeam(teamId)}
            className="ml-auto h-6 cursor-pointer whitespace-nowrap rounded-md border px-2.5 text-[11px] transition-transform duration-150 hover:-translate-y-px"
            style={{ borderColor: "var(--accent-brd)", color: "var(--color-accent-ink)", background: "var(--accent-bg)" }}>
            Open board
          </button>
        </div>
        {team.maxRounds > 0 && <PhaseStepper phase={team.phase} round={team.round} maxRounds={team.maxRounds} mini />}
        {team.pausedReason && <div className="text-[11px]" style={{ color: "var(--color-amber)" }}>{team.pausedReason}</div>}
      </div>
      {team.members.map((m) => {
        const row = agentsUi.agents[m.agentId];
        const phrase = memberPhrase(m, row, now);
        const working = phrase.tone === "work";
        return (
          <div key={m.agentId} data-testid={`member-row-${m.label}`}
            className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5 border-b px-3 py-2" style={{ borderColor: "var(--hairline)" }}>
            <Avatar label={m.label} roster={roster} ring={working ? "working" : "idle"} />
            <div className="min-w-0">
              <div className="flex items-baseline gap-1.5">
                <span className="text-[12px] font-semibold" style={{ color: identityFor(m.label, roster).color }}>{m.label}</span>
                {m.name && <span className="inline-flex h-[18px] items-center rounded-full border px-1.5 text-[10.5px]" style={{ color: "var(--color-accent-ink)", background: "var(--accent-bg)", borderColor: "var(--accent-brd)" }}>{m.name}</span>}
              </div>
              <div className="truncate text-[11.5px]" style={{ color: TONE[phrase.tone] }}>{phrase.text}</div>
            </div>
            {row && <span className="whitespace-nowrap text-[10.5px] tabular-nums text-text-3">
              {row.activationCount ?? 0} ch · {row.toolCount} tools</span>}
          </div>
        );
      })}
      {team.latest && (
        <div className="flex min-w-0 items-center gap-2 px-3 pt-2 text-[11.5px] text-text-2">
          <Avatar label={team.latest.label} roster={roster} size="sm" />
          <span className="min-w-0 truncate">{latestText(team.latest)}</span>
        </div>
      )}
      {counts && <div className="px-3 pb-2.5 pt-1.5 text-[10.5px] text-text-3">{counts}</div>}
    </div>
  );
}
```

`rosterRow` is already exported from `AgentRosterCard.tsx`. The placeholder keeps `MessageRow`'s card usable before `listTeams` answers (Phase 4 behaviour); `maxRounds: 0` hides the stepper until the summary loads.

- [ ] **Step 5: Run and commit**

Run (from `webview-ui`): `perl -e 'alarm 300; exec @ARGV' npx vitest run > /tmp/c10.txt 2>&1; echo exit=$?; tail -6 /tmp/c10.txt` and `npm run typecheck > /tmp/c10t.txt 2>&1; echo exit=$?`; then from `apps/vscode-extension`: `npm run build > /tmp/c10b.txt 2>&1; echo exit=$?`.
Expected: three `exit=0`.

```bash
git add apps/vscode-extension/webview-ui/src/teams.ts apps/vscode-extension/webview-ui/src/components/teams/TeamCard.tsx apps/vscode-extension/webview-ui/src/test/teamCard.test.tsx apps/vscode-extension/webview-ui/src/test/teams.test.ts
git commit -m "feat(webview): transcript card with member states, latest line and counts"
```

---

# Part D — Docs, suites, smoke

### Task 11: Docs, full suites, live smoke

- [ ] **Step 1: CLAUDE.md** — extend the **Agent teams — foundations (v2 Phase 4)** bullet with an **activity journey** sentence: `team_activity` (own per-team `aseq`, never member input; kinds `phase/woke/notified/took_up/picked_up/wrapped_up/capped`, written through `TeamService.record` — best-effort — at `_create_team`, `_wake_member`, `_on_leftover`, `_activate`, `_drain_member`, `_team_member_reported`, `disband_team`, `reap_subagents`; streamed as `team_activity` with `aseq`; `getTeam` returns `activity` + `last_aseq`; `/live` teams add `members[].last`, `latest`, `counts`), replacing the `"<label> finished"` system posts. Webview: pure builders `teamJourney.ts` (board items; caused wakes fold into their post's footer, a hidden cause makes a standalone beat) and `teamChapters.ts` (member chapters cut at transcript dividers numbered by `metadata.activation`, joined to activity); `teamIdentity.ts` (palette by roster order; the avatar initial is drawn by CSS so it never enters surrounding text). Spec: `docs/superpowers/specs/2026-10-05-team-activity-ui-design.md`. Commit with `docs(claude): team activity journey`.

- [ ] **Step 2: Full suites**

```bash
cd services/agentd-py && .venv/bin/pytest --color=no --timeout=120 > /tmp/py.txt 2>&1; echo exit=$?; tail -3 /tmp/py.txt
cd ../.. && npm run build > /tmp/build.txt 2>&1; echo exit=$?
perl -e 'alarm 900; exec @ARGV' npm run test > /tmp/ts.txt 2>&1; echo exit=$?; grep -E "Tests " /tmp/ts.txt
npm run typecheck > /tmp/tc.txt 2>&1; echo exit=$?
cd apps/vscode-extension/webview-ui && perl -e 'alarm 600; exec @ARGV' npx vitest run > /tmp/web.txt 2>&1; echo exit=$?; tail -4 /tmp/web.txt
```

Known pre-existing flakes: `test_command_only_step_runs_command_and_verifies`, `test_reap_kills_live_recorded_process` — re-run alone before attributing.

- [ ] **Step 3: Live smoke on the dev host** (second VS Code instance `vscode-p4`, CDP 9335). Relaunch it with the provider and the flag in its environment — the managed spawn merges `process.env`, and this profile stores no `openai_compatible` base URL:

```bash
set -a; source .env; set +a
CRUCIBLE_OPENAI_COMPAT_BASE_URL=https://integrate.api.nvidia.com/v1 \
CRUCIBLE_OPENAI_COMPAT_MODEL=nvidia/nemotron-3-ultra-550b-a55b \
CRUCIBLE_OPENAI_COMPAT_API_KEY="$NVIDIA_API_KEY" CRUCIBLE_TEAMS_ENABLED=1 \
nohup "/Applications/Visual Studio Code.app/Contents/MacOS/Code" --user-data-dir=<job tmp>/vscode-p4 \
  --extensions-dir=<job tmp>/vscode-p4/ext --remote-debugging-port=9335 \
  "--extensionDevelopmentPath=$PWD/apps/vscode-extension" --new-window "$PWD/workspaces/subagent-smoke" &
```

(Stop the old instance and its managed backend first — the lockfile names the backend pid; a surviving backend is reused without the new code.)

1. Create a team with a proposal kickoff. Card: stepper, two member rows that go from `● working · <tool>` to `✓ reported · Ns ago`, a latest line, counts.
2. Open board: `Kickoff` and `Round 1 · deliberating` chapters; P1 with markdown, assigns row, tally chips, and the footer `⚡ woke review · impl`; `took_up` beats; wrap-up rows whose report expands.
3. A member's mention of the other: the footer `⚡ woke <label>` on that post (or `↪ queued for …` if it was working) and a matching `took_up` / `picked_up` beat.
4. Member tab: profile header (definition, description, stats, stances); chapters with the right why; the latest open; Handed chips; Explored fold; Said; report card.
5. Reload the webview: same board, no duplicate beats.
6. Disband while a member works: its chapter closes as `■ stopped`, the board ends with a `Disbanded` chapter.
7. Restart the backend with a team live: wrap-ups `✗ failed — backend restarted` and a `Failed` chapter.

Record any failure as a finding, fix it with a regression test, re-run the step.
