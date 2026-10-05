# Sub-agents v2 Phase 4 — Team Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The main agent can create a team of sub-agents that talk on a shared board (posts, direct messages, proposals, stances with evidence), and the user can watch the board live — behind `CRUCIBLE_TEAMS_ENABLED`, without the coordinator's rounds yet.

**Architecture:** New package `agentd/teams/` holds storage (three tables on the chat database's connection), validation (mentions, evidence, text limits), a `TeamService` with the board operations, and two tool sources (members' `team_*` tools, the main agent's `create_team`/`post_board`/…). `ChatController` creates member rows, delivers posts to the members they concern (an interim activation policy, replaced by Phase 5's coordinator), feeds each member its inbox delta with a `delivered_seq` cursor, and re-surfaces a `team_status` block in the payload tail. A team channel (`chat:{thread}:team:{team}`) streams posts; the webview renders a team card and a team window with a Board tab plus one tab per member.

**Tech Stack:** Python 3.13 / FastAPI / SQLite; TypeScript (editor-client + Zod, VS Code extension, React webview with vitest + Testing Library).

**Spec:** `docs/superpowers/specs/2026-10-02-subagents-v2-design.md` rev 11 — §7 (structure), §9 (UI), §3.10 (framing), §3.11 (limits), §11.3 phase 4. §8 (the coordinator) is Phase 5 and out of scope; read §8.2 only for the phase names.

## Part index

| Part | Tasks | Area |
|---|---|---|
| A | 1–3 | Backend data: flag + storage, validation, `TeamService` |
| B | 4–7 | Backend runtime: tool sources, controller integration, prompts + status tail, routes + team channel |
| C | 8 | editor-client: schemas, events, methods |
| D | 9–11 | Extension: `TeamViewManager` + host wiring, team card + reducer, team window + board |
| E | 12 | Docs, full suites, live smoke |

## Interim activation policy (Phase 4 only — Phase 5's coordinator replaces it)

Spec §11.3 says Phase 4 stores and delivers posts with "no state machine yet". So someone other than a coordinator must decide when a member runs. This plan uses the smallest policy that lets a team talk, isolated in one controller method (`_team_posted`) that Phase 5 replaces:

- `create_team` activates every member for a `proposal` kickoff, and the `mentions` for a `post` kickoff.
- A board post wakes the members it mentions (`@team` = everyone); a direct message wakes its recipient. Authors never wake themselves.
- An idle member is activated with its inbox delta; a running one gets a marker in its inbox (drained into the delta at its next iteration, §3.6).
- Each member is woken at most `CRUCIBLE_TEAM_MAX_WAKES` (15) times per phase; past that, a `system` post says so and the member is not woken.
- A member's report is recorded on its row and becomes a one-line `system` post (`bob finished (completed)`); it wakes nobody. The team stays in `DELIBERATING`; `adopt_proposal`/`resume_team` answer with the phase they need.

## Global Constraints

- `CRUCIBLE_TEAMS_ENABLED`: default **off** (truthy `1/true/yes/on`); requires sub-agents — explicit on without `CRUCIBLE_SUBAGENTS_ENABLED` logs a startup WARNING; the test conftest sets it off; `/v1/config` gains `teams_enabled`.
- Members per team 2…`CRUCIBLE_TEAM_MAX_MEMBERS` (6); at most `CRUCIBLE_TEAM_MAX_LIVE_PER_THREAD` (2) live teams per thread; labels `[a-z0-9-]{1,32}`, unique per team; members are depth-1 agents with `team_id` set, `dispatcher_id` null, `parent_agent_id` null; members cannot create teams.
- `max_rounds` default 3 (proposal kickoff) / 4 (post kickoff), at most 6. `budget` default `min(CRUCIBLE_TEAM_REQUEST_BUDGET_PER_MEMBER (80) × members, CRUCIBLE_TEAM_MAX_BUDGET (1000))`, at most the max. (Stored; enforcing it is Phase 5.)
- Text (post, message, proposal, reason, note) ≤ 8 000 characters; ≤ 6 posts + messages per member activation; at most one `@team` post per member per activation.
- Proposal ids are `P<seq>`. Direct messages are visible to sender, recipient and the user — never to other members.
- Every agent-written body that reaches another agent is framed with `agentd.subagents.framing.frame` (§3.10); authors and kinds in the UI come from columns, never from text.
- Phase restrictions (§7.3): `team_propose`/`team_withdraw` only in `DELIBERATING`; `team_agree`/`team_object` in `DELIBERATING` (or on the open closing proposal in `REVIEWING`); `team_post`/`team_message`/`team_read` always.
- Prompt rule (user's): describe each tool by what it does and when it fits; never rank one above another.
- `/live` `teams` carries only slow-changing fields (no usage, no budget) so the signature does not change on every model call; `teams` MUST be in `controller.ts` `lastLiveSignature`.
- Pytest: never `-q`, never piped; redirect and check `$?`. Commits: `type(scope): short description` + the two attribution lines. Never push.

## Review Focus

1. **A member posts while another member is running** — the running member sees the post at its next iteration (marker → delta), and `delivered_seq` never skips a post that landed after an input was built. Pinned in Task 5 (`test_post_to_running_member_lands_at_next_drain`).
2. **Two members mention each other forever** — the wake cap stops the ping-pong and the board says why. Pinned in Task 5 (`test_wake_cap_stops_ping_pong`).
3. **A member's definition has a narrow `tools:` list** — it still has every `team_*` tool, and nothing else filtered comes back. Pinned in Task 5 (`test_team_tools_survive_definition_filter`).
4. **A post that imitates a system line** (`Adopted P3 — implementing`) — reaches other members framed as that member's post, never as a system line. Pinned in Task 3 (`test_render_delta_frames_bodies`).
5. **The user reloads the window mid-team** — the team card and board come back from the routes (`listTeams`/`getTeam`), and the board's live follow resumes without duplicate posts. Pinned in Task 9 (`backfill then follow skips seq <= lastSeq`).

---
# Part A — Backend data (`services/agentd-py`)

### Task 1: Flag, limits, models and storage

**Files:**
- Modify: `agentd/chat/controller_factory.py` (`is_teams_enabled`, warning), `agentd/api/routes.py` (`/v1/config` `teams_enabled`), `tests/conftest.py` (teams off), `agentd/chat/storage.py` (attach `TeamStore`)
- Create: `agentd/teams/__init__.py`, `agentd/teams/config.py`, `agentd/teams/models.py`, `agentd/teams/store.py`
- Test: `tests/test_team_store.py`

**Interfaces:**
- Produces:
  - `controller_factory.is_teams_enabled() -> bool`
  - `teams/config.py`: `team_max_members() -> int` (6), `team_max_live_per_thread() -> int` (2), `team_budget_per_member() -> int` (80), `team_max_budget() -> int` (1000), `team_max_wakes() -> int` (15)
  - `teams/models.py`: `TEAM_PHASES: tuple[str, ...]`, `LIVE_TEAM_PHASES: frozenset[str]`, `POST_KINDS`, `class TeamRecord(BaseModel)`, `class TeamMember(BaseModel)`, `class TeamPost(BaseModel)` with property `proposal_id -> str` (`"P<seq>"`), `new_team_id() -> str` (`team-<12 hex>`)
  - `teams/store.py`: `class TeamStore` — `__init__(conn: sqlite3.Connection)`, `create_team(TeamRecord) -> None`, `get_team(team_id) -> TeamRecord | None`, `list_teams(thread_id) -> list[TeamRecord]`, `update_team(team_id, **fields) -> None`, `count_live_teams(thread_id) -> int`, `add_member(TeamMember) -> None`, `members(team_id) -> list[TeamMember]`, `member(team_id, label) -> TeamMember | None`, `member_for_agent(agent_id) -> TeamMember | None`, `set_delivered_seq(team_id, label, seq) -> None` (monotonic: never lowers), `bump_wakes(team_id, label) -> int` (returns the new count), `append_post(team_id, *, author, kind, text, recipient=None, mentions=(), ref_id=None, round=None, payload=None) -> TeamPost`, `posts(team_id, *, since_seq=0, viewer=None) -> list[TeamPost]` (viewer = a member label: board posts plus DMs to/from that member; `None` = every post, for the UI and the main agent), `get_post(team_id, seq) -> TeamPost | None`, `close_proposal(team_id, seq, reason) -> None`, `fail_live_teams(reason) -> list[str]` (restart reap; returns team ids)
  - `ChatThreadStore.teams: TeamStore`

- [ ] **Step 1: Write the failing tests** (`tests/test_team_store.py`)

```python
"""Team storage (spec §7.4)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.teams.models import TeamMember, TeamRecord, new_team_id


def _store(tmp_path: Path) -> ChatThreadStore:
    return ChatThreadStore(tmp_path / "chat.sqlite3")


def _team(thread_id: str = "t1", **over) -> TeamRecord:
    base = dict(team_id=new_team_id(), thread_id=thread_id, name="auth", goal="Add login",
                max_rounds=3, approval_gate=False, budget=160, created_turn_id="turn1",
                created_at=datetime.now(UTC))
    base.update(over)
    return TeamRecord(**base)


def test_team_and_members_round_trip(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    teams.add_member(TeamMember(team_id=team.team_id, agent_id="agent-a", label="alice"))
    teams.add_member(TeamMember(team_id=team.team_id, agent_id="agent-b", label="bob"))
    got = teams.get_team(team.team_id)
    assert got is not None and got.phase == "DELIBERATING" and got.round == 1
    assert [m.label for m in teams.members(team.team_id)] == ["alice", "bob"]
    assert teams.member_for_agent("agent-b").label == "bob"
    assert teams.count_live_teams("t1") == 1
    teams.update_team(team.team_id, phase="DISBANDED", end_reason="user")
    assert teams.count_live_teams("t1") == 0


def test_post_seq_is_per_team_and_monotonic(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    a, b = _team(), _team()
    teams.create_team(a)
    teams.create_team(b)
    p1 = teams.append_post(a.team_id, author="main", kind="proposal", text="kickoff", round=0)
    p2 = teams.append_post(a.team_id, author="alice", kind="post", text="hi")
    q1 = teams.append_post(b.team_id, author="main", kind="post", text="other team")
    assert (p1.seq, p2.seq, q1.seq) == (1, 2, 1)
    assert p1.proposal_id == "P1"


def test_direct_messages_are_private(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    teams.append_post(team.team_id, author="alice", kind="post", text="board")
    teams.append_post(team.team_id, author="alice", kind="post", text="psst", recipient="bob")
    seen = lambda label: [p.text for p in teams.posts(team.team_id, viewer=label)]  # noqa: E731
    assert seen("bob") == ["board", "psst"]
    assert seen("alice") == ["board", "psst"]
    assert seen("carol") == ["board"]
    assert [p.text for p in teams.posts(team.team_id)] == ["board", "psst"]  # UI/main: all


def test_since_seq_and_close(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    p = teams.append_post(team.team_id, author="main", kind="proposal", text="P",
                          payload={"assignments": []})
    teams.append_post(team.team_id, author="bob", kind="agree", text="", ref_id="P1")
    assert [x.seq for x in teams.posts(team.team_id, since_seq=1)] == [2]
    teams.close_proposal(team.team_id, p.seq, "withdrawn")
    assert teams.get_post(team.team_id, 1).closed == "withdrawn"


def test_delivered_seq_never_lowers_and_wakes_count(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    team = _team()
    teams.create_team(team)
    teams.add_member(TeamMember(team_id=team.team_id, agent_id="agent-a", label="alice"))
    teams.set_delivered_seq(team.team_id, "alice", 5)
    teams.set_delivered_seq(team.team_id, "alice", 3)
    assert teams.member(team.team_id, "alice").delivered_seq == 5
    assert [teams.bump_wakes(team.team_id, "alice") for _ in range(3)] == [1, 2, 3]


def test_restart_fails_live_teams(tmp_path: Path) -> None:
    teams = _store(tmp_path).teams
    live, done = _team(), _team(phase="DONE")
    teams.create_team(live)
    teams.create_team(done)
    assert teams.fail_live_teams("backend restarted") == [live.team_id]
    assert teams.get_team(live.team_id).phase == "FAILED"
    assert teams.get_team(done.team_id).phase == "DONE"


def test_flag_default_off_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentd.chat.controller_factory import is_teams_enabled
    monkeypatch.delenv("CRUCIBLE_TEAMS_ENABLED", raising=False)
    assert is_teams_enabled() is False
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")
    assert is_teams_enabled() is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_store.py --color=no > /tmp/p4t1.txt 2>&1; echo exit=$?; tail -5 /tmp/p4t1.txt`
Expected: exit≠0 (`No module named 'agentd.teams'`).

- [ ] **Step 3: Flag and config**

In `controller_factory.py`, after `is_subagents_enabled`:

```python
def is_teams_enabled() -> bool:
    """Agent teams (spec v2 §7–§9). Default OFF while being built; switched on after the
    live smoke (Phase 6). Requires sub-agents: inert without them."""
    return os.getenv("CRUCIBLE_TEAMS_ENABLED", "0").strip().lower() in _TRUTHY
```

and at the end of `warn_if_incoherent_flags`:

```python
    if is_teams_enabled() and not is_subagents_enabled():
        logger.warning(
            "incoherent flags: CRUCIBLE_TEAMS_ENABLED is on but sub-agents are off — "
            "teams need CRUCIBLE_SUBAGENTS_ENABLED.")
```

`routes.py` `/v1/config`: import `is_teams_enabled` next to `is_subagents_enabled` and add `"teams_enabled": is_teams_enabled() and is_subagents_enabled(),`.

`tests/conftest.py`: add a second autouse fixture

```python
@pytest.fixture(autouse=True)
def _teams_off_unless_a_test_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "0")
```

and extend the module docstring: "CRUCIBLE_TEAMS_ENABLED (default off) is forced off for the same reason; team tests opt in."

`agentd/teams/__init__.py`: `"""Agent teams (sub-agents v2 §7–§9)."""`

`agentd/teams/config.py`:

```python
"""Team limits (spec v2 §3.11, §11.1). Read per call, so a test's monkeypatch applies."""
from __future__ import annotations

from agentd.subagents.config import _int_env


def team_max_members() -> int:
    return _int_env("CRUCIBLE_TEAM_MAX_MEMBERS", 6, 2)


def team_max_live_per_thread() -> int:
    return _int_env("CRUCIBLE_TEAM_MAX_LIVE_PER_THREAD", 2, 1)


def team_budget_per_member() -> int:
    return _int_env("CRUCIBLE_TEAM_REQUEST_BUDGET_PER_MEMBER", 80, 1)


def team_max_budget() -> int:
    return _int_env("CRUCIBLE_TEAM_MAX_BUDGET", 1000, 1)


def team_max_wakes() -> int:
    return _int_env("CRUCIBLE_TEAM_MAX_WAKES", 15, 1)
```

- [ ] **Step 4: Models (`agentd/teams/models.py`)**

```python
"""Team records (spec v2 §7.4)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

TEAM_PHASES: tuple[str, ...] = (
    "DELIBERATING", "AWAITING_APPROVAL", "IMPLEMENTING", "REVIEWING", "DEADLOCKED", "PAUSED",
    "DONE", "DISBANDED", "FAILED")
LIVE_TEAM_PHASES = frozenset(TEAM_PHASES[:6])
POST_KINDS = frozenset({"post", "proposal", "agree", "object", "withdraw", "system"})


def new_team_id() -> str:
    return f"team-{uuid4().hex[:12]}"


class TeamRecord(BaseModel):
    team_id: str
    thread_id: str
    name: str
    goal: str
    phase: str = "DELIBERATING"
    paused_reason: str | None = None
    round: int = 1
    max_rounds: int
    round_started_at: datetime | None = None
    approval_gate: bool = False
    adopted_proposal_id: str | None = None
    closing_proposal_id: str | None = None
    review_cycles: int = 0
    stuck_count: int = 0
    budget: int
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    created_turn_id: str
    checkpoint_seq: int = -1
    created_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None


class TeamMember(BaseModel):
    team_id: str
    agent_id: str
    label: str
    in_quorum: bool = True
    delivered_seq: int = 0
    assignment: dict[str, Any] | None = None
    assignment_done: bool = False
    reprompted: bool = False
    wakes_this_phase: int = 0


class TeamPost(BaseModel):
    team_id: str
    seq: int
    author: str          # member label | "main" | "user" | "system"
    kind: str            # post | proposal | agree | object | withdraw | system
    recipient: str | None = None   # None = the board; a label = a direct message
    text: str
    mentions: list[str] = Field(default_factory=list)
    ref_id: str | None = None      # the proposal an agree/object/withdraw targets
    round: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    closed: str | None = None      # proposals: withdrawn | superseded | feedback | adopted
    created_at: datetime

    @property
    def proposal_id(self) -> str:
        return f"P{self.seq}"
```

- [ ] **Step 5: Store (`agentd/teams/store.py`)**

```python
"""Team tables on the chat database (spec v2 §7.4). Shares ChatThreadStore's connection;
the backend is one asyncio thread, so each method's statements run without interleaving."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from agentd.teams.models import LIVE_TEAM_PHASES, TeamMember, TeamPost, TeamRecord

_TEAM_COLUMNS = tuple(TeamRecord.model_fields)
_DATETIME_FIELDS = frozenset({"round_started_at", "created_at", "ended_at"})


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class TeamStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._migrate()

    def _migrate(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                team_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, name TEXT NOT NULL,
                goal TEXT NOT NULL, phase TEXT NOT NULL, paused_reason TEXT,
                round INTEGER NOT NULL, max_rounds INTEGER NOT NULL, round_started_at TEXT,
                approval_gate INTEGER NOT NULL, adopted_proposal_id TEXT,
                closing_proposal_id TEXT, review_cycles INTEGER NOT NULL DEFAULT 0,
                stuck_count INTEGER NOT NULL DEFAULT 0, budget INTEGER NOT NULL,
                requests INTEGER NOT NULL DEFAULT 0, prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0, created_turn_id TEXT NOT NULL,
                checkpoint_seq INTEGER NOT NULL DEFAULT -1, created_at TEXT NOT NULL,
                ended_at TEXT, end_reason TEXT)""")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS teams_by_thread ON teams(thread_id)")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS team_members (
                team_id TEXT NOT NULL, agent_id TEXT NOT NULL, label TEXT NOT NULL,
                in_quorum INTEGER NOT NULL DEFAULT 1, delivered_seq INTEGER NOT NULL DEFAULT 0,
                assignment_json TEXT, assignment_done INTEGER NOT NULL DEFAULT 0,
                reprompted INTEGER NOT NULL DEFAULT 0,
                wakes_this_phase INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (team_id, label))""")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS team_posts (
                team_id TEXT NOT NULL, seq INTEGER NOT NULL, author TEXT NOT NULL,
                kind TEXT NOT NULL, recipient TEXT, text TEXT NOT NULL,
                mentions_json TEXT NOT NULL DEFAULT '[]', ref_id TEXT, round INTEGER,
                payload_json TEXT NOT NULL DEFAULT '{}', closed TEXT, created_at TEXT NOT NULL,
                PRIMARY KEY (team_id, seq))""")
        self._conn.commit()

    # ── teams ────────────────────────────────────────────────────────────────

    def create_team(self, team: TeamRecord) -> None:
        data = team.model_dump()
        values = [data[c].isoformat() if c in _DATETIME_FIELDS and data[c] else data[c]
                  for c in _TEAM_COLUMNS]
        self._conn.execute(
            f"INSERT INTO teams ({', '.join(_TEAM_COLUMNS)}) "  # noqa: S608 — fixed columns
            f"VALUES ({', '.join('?' * len(_TEAM_COLUMNS))})", values)
        self._conn.commit()

    def _team_from_row(self, row: sqlite3.Row) -> TeamRecord:
        data: dict[str, Any] = dict(row)
        for name in _DATETIME_FIELDS:
            data[name] = _dt(data[name])
        data["approval_gate"] = bool(data["approval_gate"])
        return TeamRecord(**data)

    def get_team(self, team_id: str) -> TeamRecord | None:
        row = self._conn.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchone()
        return self._team_from_row(row) if row else None

    def list_teams(self, thread_id: str) -> list[TeamRecord]:
        rows = self._conn.execute(
            "SELECT * FROM teams WHERE thread_id = ? ORDER BY created_at", (thread_id,))
        return [self._team_from_row(r) for r in rows.fetchall()]

    def update_team(self, team_id: str, **fields: object) -> None:
        unknown = set(fields) - set(_TEAM_COLUMNS)
        if unknown:
            raise ValueError(f"unknown team fields: {sorted(unknown)}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [v.isoformat() if isinstance(v, datetime) else v for v in fields.values()]
        self._conn.execute(f"UPDATE teams SET {sets} WHERE team_id = ?",  # noqa: S608
                           [*values, team_id])
        self._conn.commit()

    def count_live_teams(self, thread_id: str) -> int:
        marks = ", ".join("?" * len(LIVE_TEAM_PHASES))
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM teams WHERE thread_id = ? AND phase IN ({marks})",  # noqa: S608
            (thread_id, *sorted(LIVE_TEAM_PHASES))).fetchone()
        return int(row[0])

    def fail_live_teams(self, reason: str) -> list[str]:
        marks = ", ".join("?" * len(LIVE_TEAM_PHASES))
        ids = [r[0] for r in self._conn.execute(
            f"SELECT team_id FROM teams WHERE phase IN ({marks})",  # noqa: S608
            tuple(sorted(LIVE_TEAM_PHASES))).fetchall()]
        now = datetime.now(UTC).isoformat()
        self._conn.executemany(
            "UPDATE teams SET phase = 'FAILED', end_reason = ?, ended_at = ? WHERE team_id = ?",
            [(reason, now, i) for i in ids])
        self._conn.commit()
        return ids

    # ── members ──────────────────────────────────────────────────────────────

    def add_member(self, member: TeamMember) -> None:
        self._conn.execute(
            "INSERT INTO team_members (team_id, agent_id, label, in_quorum, delivered_seq, "
            "assignment_json, assignment_done, reprompted, wakes_this_phase) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (member.team_id, member.agent_id, member.label, int(member.in_quorum),
             member.delivered_seq,
             json.dumps(member.assignment) if member.assignment is not None else None,
             int(member.assignment_done), int(member.reprompted), member.wakes_this_phase))
        self._conn.commit()

    @staticmethod
    def _member_from_row(row: sqlite3.Row) -> TeamMember:
        return TeamMember(
            team_id=row["team_id"], agent_id=row["agent_id"], label=row["label"],
            in_quorum=bool(row["in_quorum"]), delivered_seq=row["delivered_seq"],
            assignment=json.loads(row["assignment_json"]) if row["assignment_json"] else None,
            assignment_done=bool(row["assignment_done"]), reprompted=bool(row["reprompted"]),
            wakes_this_phase=row["wakes_this_phase"])

    def members(self, team_id: str) -> list[TeamMember]:
        rows = self._conn.execute(
            "SELECT * FROM team_members WHERE team_id = ? ORDER BY rowid", (team_id,))
        return [self._member_from_row(r) for r in rows.fetchall()]

    def member(self, team_id: str, label: str) -> TeamMember | None:
        row = self._conn.execute(
            "SELECT * FROM team_members WHERE team_id = ? AND label = ?",
            (team_id, label)).fetchone()
        return self._member_from_row(row) if row else None

    def member_for_agent(self, agent_id: str) -> TeamMember | None:
        row = self._conn.execute(
            "SELECT * FROM team_members WHERE agent_id = ?", (agent_id,)).fetchone()
        return self._member_from_row(row) if row else None

    def set_delivered_seq(self, team_id: str, label: str, seq: int) -> None:
        # Monotonic: a marker drained after a later input never moves the cursor back.
        self._conn.execute(
            "UPDATE team_members SET delivered_seq = MAX(delivered_seq, ?) "
            "WHERE team_id = ? AND label = ?", (seq, team_id, label))
        self._conn.commit()

    def bump_wakes(self, team_id: str, label: str) -> int:
        self._conn.execute(
            "UPDATE team_members SET wakes_this_phase = wakes_this_phase + 1 "
            "WHERE team_id = ? AND label = ?", (team_id, label))
        self._conn.commit()
        member = self.member(team_id, label)
        return member.wakes_this_phase if member else 0

    # ── posts ────────────────────────────────────────────────────────────────

    def append_post(
        self, team_id: str, *, author: str, kind: str, text: str,
        recipient: str | None = None, mentions: tuple[str, ...] | list[str] = (),
        ref_id: str | None = None, round: int | None = None,  # noqa: A002 — spec column
        payload: dict[str, Any] | None = None,
    ) -> TeamPost:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM team_posts WHERE team_id = ?",
            (team_id,)).fetchone()
        post = TeamPost(team_id=team_id, seq=int(row[0]), author=author, kind=kind,
                        recipient=recipient, text=text, mentions=list(mentions), ref_id=ref_id,
                        round=round, payload=payload or {}, created_at=datetime.now(UTC))
        self._conn.execute(
            "INSERT INTO team_posts (team_id, seq, author, kind, recipient, text, "
            "mentions_json, ref_id, round, payload_json, closed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (team_id, post.seq, author, kind, recipient, text, json.dumps(post.mentions),
             ref_id, round, json.dumps(post.payload), post.created_at.isoformat()))
        self._conn.commit()
        return post

    @staticmethod
    def _post_from_row(row: sqlite3.Row) -> TeamPost:
        return TeamPost(
            team_id=row["team_id"], seq=row["seq"], author=row["author"], kind=row["kind"],
            recipient=row["recipient"], text=row["text"],
            mentions=json.loads(row["mentions_json"]), ref_id=row["ref_id"],
            round=row["round"], payload=json.loads(row["payload_json"]), closed=row["closed"],
            created_at=datetime.fromisoformat(row["created_at"]))

    def posts(
        self, team_id: str, *, since_seq: int = 0, viewer: str | None = None,
    ) -> list[TeamPost]:
        if viewer is None:
            rows = self._conn.execute(
                "SELECT * FROM team_posts WHERE team_id = ? AND seq > ? ORDER BY seq",
                (team_id, since_seq))
        else:
            rows = self._conn.execute(
                "SELECT * FROM team_posts WHERE team_id = ? AND seq > ? AND "
                "(recipient IS NULL OR recipient = ? OR author = ?) ORDER BY seq",
                (team_id, since_seq, viewer, viewer))
        return [self._post_from_row(r) for r in rows.fetchall()]

    def get_post(self, team_id: str, seq: int) -> TeamPost | None:
        row = self._conn.execute(
            "SELECT * FROM team_posts WHERE team_id = ? AND seq = ?", (team_id, seq)).fetchone()
        return self._post_from_row(row) if row else None

    def close_proposal(self, team_id: str, seq: int, reason: str) -> None:
        self._conn.execute(
            "UPDATE team_posts SET closed = ? WHERE team_id = ? AND seq = ? AND kind = 'proposal'",
            (reason, team_id, seq))
        self._conn.commit()
```

In `ChatThreadStore.__init__`, after `self._migrate()`:

```python
        # Team tables (sub-agents v2 §7.4) share this connection.
        self.teams = TeamStore(self._conn)
```

with `from agentd.teams.store import TeamStore` at the top.

- [ ] **Step 6: Run and commit**

Run: `.venv/bin/pytest tests/test_team_store.py tests/test_chat_storage*.py --color=no > /tmp/p4t1.txt 2>&1; echo exit=$?; tail -3 /tmp/p4t1.txt`
Expected: `exit=0` (if `tests/test_chat_storage*.py` matches nothing, drop it from the command).

```bash
git add agentd/teams agentd/chat/controller_factory.py agentd/chat/storage.py agentd/api/routes.py tests/conftest.py tests/test_team_store.py
git commit -m "feat(teams): flag, limits, and team/member/post storage"
```

### Task 2: Validation — labels, mentions, text and evidence

**Files:**
- Create: `agentd/teams/validation.py`
- Test: `tests/test_team_validation.py`

**Interfaces:**
- Produces:
  - `LABEL_RE`, `TEXT_MAX = 8000`, `MAX_POSTS_PER_ACTIVATION = 6`
  - `class TeamInputError(ValueError)` — its message is shown to the model verbatim
  - `check_label(label: str) -> str` (normalized, raises)
  - `check_text(text: object, what: str) -> str` (raises on empty / non-string / over 8 000)
  - `effective_mentions(text: str, explicit: object, roster: list[str]) -> list[str]` — union of the `mentions` array and `@label` tokens, case-folded, leading `@` stripped, roster order then `team`; an unknown label in `explicit` raises with the roster listed; unknown `@tokens` in text are ignored (they may be emails or handles)
  - `parse_proposal_id(raw: object) -> int` (`"P12"`/`"p12"`/`12` → `12`; raises)
  - `validate_evidence(raw: object, *, workspace: Path, assignment_files: set[str], post_exists: Callable[[int], bool]) -> dict[str, object]`

- [ ] **Step 1: Write the failing tests**

```python
"""Team input validation (spec v2 §7.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.teams.validation import (
    TeamInputError,
    check_label,
    check_text,
    effective_mentions,
    parse_proposal_id,
    validate_evidence,
)

ROSTER = ["alice", "bob", "carol"]


def test_labels() -> None:
    assert check_label("api-1") == "api-1"
    for bad in ("Alice", "a b", "", "x" * 33, "@bob"):
        with pytest.raises(TeamInputError):
            check_label(bad)


def test_text_limits() -> None:
    assert check_text("  hi  ", "post") == "hi"
    for bad in ("", "   ", None, "x" * 8001):
        with pytest.raises(TeamInputError):
            check_text(bad, "post")


def test_mentions_union_casefold_and_team() -> None:
    assert effective_mentions("Ping @Bob and @team", ["@carol"], ROSTER) == ["bob", "carol", "team"]
    assert effective_mentions("mail me at a@b.com", None, ROSTER) == []


def test_unknown_explicit_mention_lists_roster() -> None:
    with pytest.raises(TeamInputError, match="alice, bob, carol"):
        effective_mentions("hi", ["dave"], ROSTER)


def test_proposal_ids() -> None:
    assert [parse_proposal_id(x) for x in ("P3", "p12", 7)] == [3, 12, 7]
    for bad in ("Q3", "P", "", None, "P-1"):
        with pytest.raises(TeamInputError):
            parse_proposal_id(bad)


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("one\ntwo\nthree\n")
    return tmp_path


def test_evidence_file_and_line(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ev = validate_evidence({"files": ["src/a.py"], "line": 2}, workspace=ws,
                           assignment_files=set(), post_exists=lambda s: False)
    assert ev == {"files": ["src/a.py"], "line": 2}


def test_evidence_command_output_and_quote(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    assert validate_evidence({"command": "pytest", "output": "1 failed"}, workspace=ws,
                             assignment_files=set(), post_exists=lambda s: False)["command"] == "pytest"
    assert validate_evidence({"quote_seq": 4}, workspace=ws, assignment_files=set(),
                             post_exists=lambda s: s == 4) == {"quote_seq": 4}


@pytest.mark.parametrize("raw,needle", [
    ({}, "at least one of"),
    ({"files": ["src/a.py"]}, "at least one of"),
    ({"files": ["../outside.py"], "line": 1}, "outside the workspace"),
    ({"files": ["src/missing.py"], "line": 1}, "does not exist"),
    ({"files": ["src/a.py"], "line": 9}, "has 3 lines"),
    ({"quote_seq": 99}, "no post"),
    ("not a dict", "must be an object"),
])
def test_evidence_refusals(tmp_path: Path, raw, needle) -> None:
    with pytest.raises(TeamInputError, match=needle):
        validate_evidence(raw, workspace=_ws(tmp_path), assignment_files=set(),
                          post_exists=lambda s: False)


def test_evidence_may_name_a_file_the_proposal_assigns(tmp_path: Path) -> None:
    ev = validate_evidence({"files": ["src/new.py"], "line": 1}, workspace=_ws(tmp_path),
                           assignment_files={"src/new.py"}, post_exists=lambda s: False)
    assert ev["files"] == ["src/new.py"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_validation.py --color=no > /tmp/p4t2.txt 2>&1; echo exit=$?; tail -3 /tmp/p4t2.txt`
Expected: exit≠0.

- [ ] **Step 3: Implement `agentd/teams/validation.py`**

```python
"""Team input validation (spec v2 §7.3). Every refusal is a TeamInputError whose message
the model sees verbatim, so each one says what was wrong and what is allowed."""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

LABEL_RE = re.compile(r"^[a-z0-9-]{1,32}$")
TEXT_MAX = 8000
MAX_POSTS_PER_ACTIVATION = 6
_MENTION_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9-]{1,32})\b")
_PROPOSAL_RE = re.compile(r"^[Pp]?(\d+)$")


class TeamInputError(ValueError):
    """Invalid team tool input; shown to the model as the tool's error."""


def check_label(label: str) -> str:
    if not LABEL_RE.match(label):
        raise TeamInputError(
            f"label {label!r} must be 1-32 characters of a-z, 0-9 and '-' (it is written @label)")
    return label


def check_text(text: object, what: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise TeamInputError(f"{what} is empty")
    if len(text) > TEXT_MAX:
        raise TeamInputError(f"{what} is {len(text)} characters; the limit is {TEXT_MAX}")
    return text.strip()


def _norm(label: str) -> str:
    return label.strip().lstrip("@").casefold()


def effective_mentions(text: str, explicit: object, roster: list[str]) -> list[str]:
    known = set(roster) | {"team"}
    found: set[str] = set()
    if explicit is not None:
        if not isinstance(explicit, list):
            raise TeamInputError("mentions must be a list of member labels")
        for raw in explicit:
            label = _norm(str(raw))
            if label not in known:
                raise TeamInputError(
                    f"unknown member {raw!r}; the team is: {', '.join(roster)} (or @team)")
            found.add(label)
    for token in _MENTION_RE.findall(text):
        label = token.casefold()
        if label in known:
            found.add(label)  # unknown @tokens are emails or handles, not mentions
    return [label for label in [*roster, "team"] if label in found]


def parse_proposal_id(raw: object) -> int:
    match = _PROPOSAL_RE.match(str(raw).strip()) if raw is not None else None
    if match is None:
        raise TeamInputError(f"{raw!r} is not a proposal id (write it as P<number>, e.g. P3)")
    return int(match.group(1))


def _canonical(workspace: Path, raw: str) -> str:
    root = workspace.resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise TeamInputError(f"evidence file {raw!r} is outside the workspace")
    return target.relative_to(root).as_posix()


def validate_evidence(
    raw: object, *, workspace: Path, assignment_files: set[str],
    post_exists: Callable[[int], bool],
) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise TeamInputError("evidence must be an object: {files, line} | {command, output} "
                             "| {quote_seq}")
    files_raw = raw.get("files") or []
    if not isinstance(files_raw, list):
        raise TeamInputError("evidence.files must be a list of paths")
    files = [_canonical(workspace, str(f)) for f in files_raw]
    line = raw.get("line")
    command = str(raw.get("command") or "").strip()
    output = str(raw.get("output") or "").strip()
    quote_seq = raw.get("quote_seq")
    has_file_line = bool(files) and isinstance(line, int)
    has_command = bool(command) and bool(output)
    has_quote = isinstance(quote_seq, int)
    if not (has_file_line or has_command or has_quote):
        raise TeamInputError(
            "evidence needs at least one of: files + line, command + output, or quote_seq")
    for path in files:
        if not (workspace.resolve() / path).is_file() and path not in assignment_files:
            raise TeamInputError(f"evidence file {path!r} does not exist")
    out: dict[str, object] = {}
    if files:
        out["files"] = files
    if isinstance(line, int):
        first = workspace.resolve() / files[0] if files else None
        if first is not None and first.is_file():
            count = len(first.read_text(encoding="utf-8", errors="replace").splitlines())
            if not 1 <= line <= count:
                raise TeamInputError(f"line {line} is out of range: {files[0]} has {count} lines")
        out["line"] = line
    if command:
        out["command"] = command
    if output:
        out["output"] = output[:4000]
    if has_quote:
        assert isinstance(quote_seq, int)
        if not post_exists(quote_seq):
            raise TeamInputError(f"quote_seq {quote_seq}: there is no post with that seq")
        out["quote_seq"] = quote_seq
    return out
```

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/pytest tests/test_team_validation.py --color=no > /tmp/p4t2.txt 2>&1; echo exit=$?; tail -3 /tmp/p4t2.txt`
Expected: `exit=0`.

```bash
git add agentd/teams/validation.py tests/test_team_validation.py
git commit -m "feat(teams): validate labels, mentions, text and objection evidence"
```

### Task 3: `TeamService` — board operations, member input, status

**Files:**
- Create: `agentd/teams/service.py`
- Test: `tests/test_team_service.py`

**Interfaces:**
- Consumes: Tasks 1–2; `agentd.subagents.framing.frame`.
- Produces:
  - `@dataclass class ActivationCounters: posts: int = 0; team_mentions: int = 0` (one per member activation)
  - `@dataclass(frozen=True) class AgentInfo: name: str; description: str; status: str`
  - `class TeamService` — `__init__(store: TeamStore, workspace: Path, agent_info: Callable[[str], AgentInfo], on_post: Callable[[TeamRecord, TeamPost], None] = …)`
    - `post(team_id, author, text, mentions=None, counters=None) -> TeamPost`
    - `message(team_id, author, member, text, counters=None) -> TeamPost`
    - `propose(team_id, author, text, assignments, shared_files=None, supersedes=None, counters=None) -> TeamPost`
    - `agree(team_id, author, proposal_id, note=None) -> TeamPost`
    - `object(team_id, author, proposal_id, reason, evidence) -> TeamPost` (the method is named `object_` — `object` shadows a builtin)
    - `withdraw(team_id, author, proposal_id) -> TeamPost`
    - `read(team_id, viewer, since_seq=0) -> list[TeamPost]`
    - `stances(team_id) -> dict[int, dict[str, str]]` (proposal seq → label → `agree`/`object`; latest wins)
    - `render_delta(team_id, label) -> tuple[str, int]` (framed input, highest seq it covers; does NOT advance the cursor)
    - `status_text(team_id, label) -> str` (the payload tail)
    - `summary(team_id) -> dict[str, object]` (main `team_status`)
    - `system_post(team_id, text) -> TeamPost`
  - Every refusal raises `TeamInputError` (Task 2). `on_post` fires after every stored post (the controller broadcasts and wakes there).

- [ ] **Step 1: Write the failing tests** (`tests/test_team_service.py`)

```python
"""Board operations (spec v2 §7.3, §7.6)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService
from agentd.teams.validation import TeamInputError


def _setup(tmp_path: Path, phase: str = "DELIBERATING"):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "auth.py").write_text("def login():\n    pass\n")
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="Add login",
                      phase=phase, max_rounds=3, budget=160, created_turn_id="turn1",
                      created_at=datetime.now(UTC))
    teams.create_team(team)
    for label in ("alice", "bob", "carol"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    posted: list = []
    info = lambda agent_id: AgentInfo("general-purpose", "does things", "running")  # noqa: E731
    service = TeamService(teams, ws, info, on_post=lambda t, p: posted.append(p))
    return service, team.team_id, teams, posted


def test_post_mentions_and_callback(tmp_path: Path) -> None:
    service, tid, _, posted = _setup(tmp_path)
    post = service.post(tid, "alice", "Found a bug @bob", mentions=["carol"])
    assert post.mentions == ["bob", "carol"] and posted == [post]


def test_activation_limits(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    counters = ActivationCounters()
    service.post(tid, "alice", "all hands @team", counters=counters)
    with pytest.raises(TeamInputError, match="one @team"):
        service.post(tid, "alice", "again @team", counters=counters)
    for i in range(5):
        service.post(tid, "alice", f"note {i}", counters=counters)
    with pytest.raises(TeamInputError, match="6 posts"):
        service.post(tid, "alice", "one too many", counters=counters)


def test_message_is_private_and_checked(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    service.message(tid, "alice", "bob", "psst")
    assert [p.text for p in service.read(tid, "carol")] == []
    assert [p.text for p in service.read(tid, "bob")] == ["psst"]
    with pytest.raises(TeamInputError, match="alice, bob, carol"):
        service.message(tid, "alice", "dave", "hi")
    with pytest.raises(TeamInputError, match="yourself"):
        service.message(tid, "alice", "alice", "hi")


def test_propose_validates_and_supersedes(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    p1 = service.propose(tid, "alice", "Plan A", [{"member": "bob", "part": "api", "files": ["src/auth.py"]}])
    assert p1.payload["assignments"][0]["files"] == ["src/auth.py"]
    with pytest.raises(TeamInputError, match="unknown member"):
        service.propose(tid, "alice", "Plan B", [{"member": "dave", "part": "x", "files": []}])
    p2 = service.propose(tid, "alice", "Plan A2", [], supersedes=["P1"])
    assert teams.get_post(tid, p1.seq).closed == "superseded"
    assert p2.payload["supersedes"] == ["P1"]


def test_stance_required_before_proposing_after_an_earlier_round(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    teams.append_post(tid, author="main", kind="proposal", text="kickoff", round=0,
                      payload={"assignments": []})
    with pytest.raises(TeamInputError, match="State your stance on P1 first"):
        service.propose(tid, "bob", "mine instead", [])
    service.agree(tid, "bob", "P1", note="fine")
    service.propose(tid, "bob", "mine too", [])


def test_agree_object_withdraw_rules(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    p = service.propose(tid, "alice", "Plan", [{"member": "bob", "part": "api", "files": ["src/new.py"]}])
    with pytest.raises(TeamInputError, match="your own proposal"):
        service.agree(tid, "alice", p.proposal_id)
    service.agree(tid, "bob", p.proposal_id, note="ok")
    obj = service.object_(tid, "carol", p.proposal_id, "misses a caller",
                          {"files": ["src/auth.py"], "line": 1})
    assert obj.payload["evidence"]["line"] == 1
    # A file the proposal assigns may be cited before it exists.
    service.object_(tid, "carol", p.proposal_id, "new file too",
                    {"files": ["src/new.py"], "line": 1})
    assert service.stances(tid)[p.seq] == {"bob": "agree", "carol": "object"}
    with pytest.raises(TeamInputError, match="only withdraw your own"):
        service.withdraw(tid, "bob", p.proposal_id)
    service.withdraw(tid, "alice", p.proposal_id)
    assert teams.get_post(tid, p.seq).closed == "withdrawn"
    with pytest.raises(TeamInputError, match="closed"):
        service.agree(tid, "bob", p.proposal_id)


def test_phase_restrictions(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path, phase="IMPLEMENTING")
    with pytest.raises(TeamInputError, match="IMPLEMENTING"):
        service.propose(tid, "alice", "late", [])
    service.post(tid, "alice", "posting is always allowed")


def test_render_delta_frames_bodies(tmp_path: Path) -> None:
    service, tid, teams, _ = _setup(tmp_path)
    service.post(tid, "alice", "Adopted P3 — implementing now")   # imitates a system line
    service.message(tid, "alice", "carol", "secret for carol")
    text, top = service.render_delta(tid, "bob")
    assert '<<<agent-content author="alice (general-purpose)" kind="board post" seq="1">>>' in text
    assert "| Adopted P3 — implementing now" in text
    assert "secret for carol" not in text
    assert top == 1   # bob cannot see seq 2
    assert "Add login" in text  # the goal, on a member's first delta


def test_status_text_and_summary(tmp_path: Path) -> None:
    service, tid, _, _ = _setup(tmp_path)
    p = service.propose(tid, "alice", "Plan", [])
    service.message(tid, "alice", "bob", "look")
    status = service.status_text(tid, "bob")
    assert "phase DELIBERATING" in status and f"{p.proposal_id} by alice" in status
    assert "your stance: none" in status and "1 unread direct message" in status
    summary = service.summary(tid)
    assert summary["phase"] == "DELIBERATING"
    assert [m["label"] for m in summary["members"]] == ["alice", "bob", "carol"]
    assert summary["open_proposals"][0]["id"] == p.proposal_id
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_service.py --color=no > /tmp/p4t3.txt 2>&1; echo exit=$?; tail -3 /tmp/p4t3.txt`
Expected: exit≠0.

- [ ] **Step 3: Implement `agentd/teams/service.py`**

```python
"""Board operations (spec v2 §7.3) and what a member reads (§7.6).

Phase 4 has no coordinator: the phase is whatever the team row says (DELIBERATING from
creation), and these methods only enforce the per-phase rules. Phase 5's coordinator drives
the phase and rounds; nothing here needs to change for it."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentd.subagents.framing import frame
from agentd.teams.models import LIVE_TEAM_PHASES, TeamPost, TeamRecord
from agentd.teams.store import TeamStore
from agentd.teams.validation import (
    MAX_POSTS_PER_ACTIVATION,
    TeamInputError,
    check_text,
    effective_mentions,
    parse_proposal_id,
    validate_evidence,
)

_STANCE_KINDS = ("agree", "object")


@dataclass
class ActivationCounters:
    posts: int = 0
    team_mentions: int = 0


@dataclass(frozen=True)
class AgentInfo:
    name: str
    description: str
    status: str


class TeamService:
    def __init__(
        self, store: TeamStore, workspace: Path, agent_info: Callable[[str], AgentInfo],
        on_post: Callable[[TeamRecord, TeamPost], None] = lambda _t, _p: None,
    ) -> None:
        self._store = store
        self._workspace = workspace
        self._agent_info = agent_info
        self._on_post = on_post

    # ── helpers ─────────────────────────────────────────────────────────────

    def _team(self, team_id: str) -> TeamRecord:
        team = self._store.get_team(team_id)
        if team is None:
            raise TeamInputError(f"no team {team_id!r}")
        if team.phase not in LIVE_TEAM_PHASES:
            raise TeamInputError(f"team {team.name!r} has ended ({team.phase})")
        return team

    def _roster(self, team_id: str) -> list[str]:
        return [m.label for m in self._store.members(team_id)]

    def _count(self, counters: ActivationCounters | None, mentions: list[str]) -> None:
        if counters is None:
            return
        if counters.posts >= MAX_POSTS_PER_ACTIVATION:
            raise TeamInputError(
                f"you have sent {MAX_POSTS_PER_ACTIVATION} posts and messages this activation "
                "(the limit) — report, or wait for replies")
        if "team" in mentions and counters.team_mentions >= 1:
            raise TeamInputError("only one @team post per activation — mention members by name")
        counters.posts += 1
        if "team" in mentions:
            counters.team_mentions += 1

    def _emit(self, team: TeamRecord, post: TeamPost) -> TeamPost:
        self._on_post(team, post)
        return post

    def _open_proposal(self, team_id: str, raw: object) -> TeamPost:
        seq = parse_proposal_id(raw)
        post = self._store.get_post(team_id, seq)
        if post is None or post.kind != "proposal":
            raise TeamInputError(f"P{seq} is not a proposal on this board")
        if post.closed is not None:
            raise TeamInputError(f"P{seq} is closed ({post.closed})")
        return post

    def _require_phase(self, team: TeamRecord, allowed: tuple[str, ...], action: str) -> None:
        if team.phase not in allowed:
            raise TeamInputError(
                f"{action} is not allowed while the team is {team.phase} "
                f"(allowed in: {', '.join(allowed)})")

    def _stance_phase(self, team: TeamRecord, proposal: TeamPost, action: str) -> None:
        if team.phase == "DELIBERATING":
            return
        if team.phase == "REVIEWING" and team.closing_proposal_id == proposal.proposal_id:
            return
        raise TeamInputError(
            f"{action} is not allowed while the team is {team.phase} (only in DELIBERATING, "
            "or on the closing proposal in REVIEWING)")

    def open_proposals(self, team_id: str) -> list[TeamPost]:
        return [p for p in self._store.posts(team_id) if p.kind == "proposal" and p.closed is None]

    def stances(self, team_id: str) -> dict[int, dict[str, str]]:
        out: dict[int, dict[str, str]] = {}
        for post in self._store.posts(team_id):
            if post.kind in _STANCE_KINDS and post.ref_id:
                out.setdefault(parse_proposal_id(post.ref_id), {})[post.author] = post.kind
        return out

    # ── operations ──────────────────────────────────────────────────────────

    def post(self, team_id: str, author: str, text: object, mentions: object = None,
             counters: ActivationCounters | None = None) -> TeamPost:
        team = self._team(team_id)
        body = check_text(text, "post")
        effective = effective_mentions(body, mentions, self._roster(team_id))
        self._count(counters, effective)
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="post", text=body, mentions=effective,
            round=team.round if team.phase == "DELIBERATING" else None))

    def message(self, team_id: str, author: str, member: object, text: object,
                counters: ActivationCounters | None = None) -> TeamPost:
        team = self._team(team_id)
        roster = self._roster(team_id)
        target = str(member or "").strip().lstrip("@").casefold()
        if target not in roster:
            raise TeamInputError(f"unknown member {member!r}; the team is: {', '.join(roster)}")
        if target == author:
            raise TeamInputError("you cannot message yourself")
        body = check_text(text, "message")
        self._count(counters, [])
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="post", text=body, recipient=target,
            mentions=[target]))

    def propose(self, team_id: str, author: str, text: object, assignments: object,
                shared_files: object = None, supersedes: object = None,
                counters: ActivationCounters | None = None) -> TeamPost:
        team = self._team(team_id)
        self._require_phase(team, ("DELIBERATING",), "team_propose")
        body = check_text(text, "proposal")
        roster = self._roster(team_id)
        owed = [p.proposal_id for p in self.open_proposals(team_id)
                if p.author != author and (p.round or 0) < team.round
                and author not in self.stances(team_id).get(p.seq, {})]
        if owed:
            raise TeamInputError(
                f"State your stance on {', '.join(owed)} first (team_agree, with a note for "
                "small changes, or team_object).")
        if not isinstance(assignments, list):
            raise TeamInputError("assignments must be a list of {member, part, files}")
        parts: list[dict[str, object]] = []
        for i, item in enumerate(assignments):
            if not isinstance(item, dict):
                raise TeamInputError(f"assignments[{i}] must be an object")
            member = str(item.get("member", "")).lstrip("@").casefold()
            if member not in roster:
                raise TeamInputError(
                    f"assignments[{i}]: unknown member {item.get('member')!r}; "
                    f"the team is: {', '.join(roster)}")
            files = item.get("files") or []
            if not isinstance(files, list):
                raise TeamInputError(f"assignments[{i}].files must be a list of paths")
            parts.append({"member": member, "part": check_text(item.get("part"), "part"),
                          "files": [str(f) for f in files]})
        shared = [str(f) for f in shared_files] if isinstance(shared_files, list) else []
        closes: list[TeamPost] = []
        if supersedes is not None:
            if not isinstance(supersedes, list):
                raise TeamInputError("supersedes must be a list of proposal ids")
            closes = [self._open_proposal(team_id, raw) for raw in supersedes]
        self._count(counters, [])
        post = self._store.append_post(
            team_id, author=author, kind="proposal", text=body, round=team.round,
            payload={"assignments": parts, "shared_files": shared,
                     "supersedes": [p.proposal_id for p in closes]})
        for old in closes:
            self._store.close_proposal(team_id, old.seq, "superseded")
        return self._emit(team, post)

    def agree(self, team_id: str, author: str, proposal_id: object,
              note: object = None) -> TeamPost:
        team = self._team(team_id)
        proposal = self._open_proposal(team_id, proposal_id)
        self._stance_phase(team, proposal, "team_agree")
        if proposal.author == author:
            raise TeamInputError("your own proposal already counts as your agreement")
        payload = {"note": check_text(note, "note")} if note not in (None, "") else {}
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="agree", text=str(payload.get("note", "")),
            ref_id=proposal.proposal_id, round=team.round, payload=payload))

    def object_(self, team_id: str, author: str, proposal_id: object, reason: object,
                evidence: object) -> TeamPost:
        team = self._team(team_id)
        proposal = self._open_proposal(team_id, proposal_id)
        self._stance_phase(team, proposal, "team_object")
        body = check_text(reason, "reason")
        assigned = {f for a in proposal.payload.get("assignments", []) for f in a.get("files", [])}
        assigned |= set(proposal.payload.get("shared_files", []))
        checked = validate_evidence(
            evidence, workspace=self._workspace, assignment_files=assigned,
            post_exists=lambda seq: self._store.get_post(team_id, seq) is not None)
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="object", text=body, ref_id=proposal.proposal_id,
            round=team.round, payload={"evidence": checked}))

    def withdraw(self, team_id: str, author: str, proposal_id: object) -> TeamPost:
        team = self._team(team_id)
        self._require_phase(team, ("DELIBERATING",), "team_withdraw")
        proposal = self._open_proposal(team_id, proposal_id)
        if proposal.author != author:
            raise TeamInputError(f"you can only withdraw your own proposals; "
                                 f"{proposal.proposal_id} is {proposal.author}'s")
        self._store.close_proposal(team_id, proposal.seq, "withdrawn")
        return self._emit(team, self._store.append_post(
            team_id, author=author, kind="withdraw", text="", ref_id=proposal.proposal_id,
            round=team.round))

    def read(self, team_id: str, viewer: str, since_seq: int = 0) -> list[TeamPost]:
        return self._store.posts(team_id, since_seq=since_seq, viewer=viewer)

    def system_post(self, team_id: str, text: str) -> TeamPost:
        team = self._store.get_team(team_id)
        assert team is not None
        return self._emit(team, self._store.append_post(
            team_id, author="system", kind="system", text=text))

    # ── what members and the main agent read ────────────────────────────────

    def _who(self, team_id: str, label: str) -> str:
        if label in ("main", "user", "system"):
            return label
        member = self._store.member(team_id, label)
        name = self._agent_info(member.agent_id).name if member else "?"
        return f"{label} ({name})"

    def _render_post(self, team_id: str, post: TeamPost, viewer: str) -> str:
        if post.kind == "system":
            return f"● system: {post.text}"   # system-written: never model text
        if post.kind == "proposal":
            kind = f"proposal {post.proposal_id}"
            lines = [post.text]
            for a in post.payload.get("assignments", []):
                files = ", ".join(a.get("files", [])) or "no files"
                lines.append(f"assignment — {a['member']}: {a['part']} ({files})")
            if post.payload.get("shared_files"):
                lines.append("shared files: " + ", ".join(post.payload["shared_files"]))
            if post.payload.get("supersedes"):
                lines.append("supersedes: " + ", ".join(post.payload["supersedes"]))
            body = "\n".join(lines)
        elif post.kind == "agree":
            kind = f"agrees with {post.ref_id}"
            body = post.text or "(no note)"
        elif post.kind == "object":
            kind = f"objects to {post.ref_id}"
            body = post.text + "\nevidence: " + json.dumps(post.payload.get("evidence", {}))
        elif post.kind == "withdraw":
            kind = f"withdraws {post.ref_id}"
            body = "(withdrawn)"
        elif post.recipient is not None:
            kind = "direct message to you" if post.recipient == viewer else (
                f"direct message to {post.recipient}")
            body = post.text
        else:
            kind = "board post"
            body = post.text
        return frame(self._who(team_id, post.author), kind, body, seq=post.seq)

    def _header(self, team: TeamRecord, first: bool) -> str:
        lines = [f"Team {team.name!r} — phase {team.phase}, round {team.round} of "
                 f"{team.max_rounds}."]
        if first:
            lines.append(f"Goal: {team.goal}")
        lines.append("Check the claims relevant to your role, then state your stance on each "
                     "open proposal (team_agree with a note for small changes, or team_object "
                     "with evidence). Post what others need to know; report when you are done.")
        return "\n".join(lines)

    def render_delta(self, team_id: str, label: str) -> tuple[str, int]:
        team = self._store.get_team(team_id)
        member = self._store.member(team_id, label)
        assert team is not None and member is not None
        visible = self._store.posts(team_id, since_seq=member.delivered_seq, viewer=label)
        top = max((p.seq for p in visible), default=member.delivered_seq)
        others = [p for p in visible if p.author != label]
        body = "\n\n".join(self._render_post(team_id, p, label) for p in others)
        header = self._header(team, first=member.delivered_seq == 0)
        return (f"{header}\n\n{body}" if body else f"{header}\n\nNo new posts."), top

    def status_text(self, team_id: str, label: str) -> str:
        team = self._store.get_team(team_id)
        member = self._store.member(team_id, label)
        assert team is not None and member is not None
        stances = self.stances(team_id)
        lines = [f"team {team.name!r}: phase {team.phase}, round {team.round} of "
                 f"{team.max_rounds}", f"goal: {team.goal}", "roster:"]
        for m in self._store.members(team_id):
            info = self._agent_info(m.agent_id)
            you = " (you)" if m.label == label else ""
            lines.append(f"- {m.label}{you}: {info.name} — {info.description} [{info.status}]")
        open_ps = self.open_proposals(team_id)
        lines.append("open proposals:" if open_ps else "open proposals: none")
        for p in open_ps:
            mine = "yours" if p.author == label else stances.get(p.seq, {}).get(label, "none")
            lines.append(f"- {p.proposal_id} by {p.author} — your stance: {mine}")
        if member.assignment:
            lines.append(f"your assignment: {json.dumps(member.assignment)}")
        unread = self._store.posts(team_id, since_seq=member.delivered_seq, viewer=label)
        dms = sum(1 for p in unread if p.recipient == label)
        mentions = sum(1 for p in unread if p.recipient is None and
                       (label in p.mentions or "team" in p.mentions) and p.author != label)
        lines.append(f"unread: {dms} unread direct message{'s' if dms != 1 else ''}, "
                     f"{mentions} mention{'s' if mentions != 1 else ''}")
        return "\n".join(lines)

    def summary(self, team_id: str) -> dict[str, object]:
        team = self._store.get_team(team_id)
        if team is None:
            raise TeamInputError(f"no team {team_id!r}")
        stances = self.stances(team_id)
        return {
            "team_id": team.team_id, "name": team.name, "goal": team.goal, "phase": team.phase,
            "round": team.round, "max_rounds": team.max_rounds,
            "paused_reason": team.paused_reason,
            "members": [{"label": m.label, "agent_id": m.agent_id,
                         "status": self._agent_info(m.agent_id).status}
                        for m in self._store.members(team_id)],
            "open_proposals": [
                {"id": p.proposal_id, "author": p.author, "text": p.text[:600],
                 "stances": stances.get(p.seq, {})}
                for p in self.open_proposals(team_id)],
            "usage": {"requests": team.requests, "budget": team.budget},
        }
```

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/pytest tests/test_team_service.py --color=no > /tmp/p4t3.txt 2>&1; echo exit=$?; tail -3 /tmp/p4t3.txt`
Expected: `exit=0`.

```bash
git add agentd/teams/service.py tests/test_team_service.py
git commit -m "feat(teams): board operations, member delta, status tail and summary"
```

---

# Part B — Backend runtime

### Task 4: Tool sources — members' `team_*` tools and the main agent's team tools

**Files:**
- Create: `agentd/teams/tools.py`
- Test: `tests/test_team_tools.py`

**Interfaces:**
- Consumes: `TeamService`, `ActivationCounters` (Task 3); `TeamInputError`, `check_label`, `check_text` (Task 2); `team_max_members`, `team_budget_per_member`, `team_max_budget` (Task 1); `AgentDefinition`; `ToolDefinition`, `ToolOutput`.
- Produces:
  - `MEMBER_TOOL_NAMES: frozenset[str]` = `team_post`, `team_message`, `team_propose`, `team_agree`, `team_object`, `team_withdraw`, `team_read`
  - `MAIN_TOOL_NAMES: frozenset[str]` = `create_team`, `post_board`, `team_status`, `adopt_proposal`, `resume_team`, `disband_team`
  - `class TeamToolSource` — `__init__(service: TeamService, team_id: str, label: str, counters: ActivationCounters)`; `name = "team"`; `definitions()`, `owns(tool)`, `async execute(tool, args) -> ToolOutput`
  - `@dataclass(frozen=True) class TeamMemberSpec: label: str; agent: AgentDefinition`
  - `@dataclass(frozen=True) class CreateTeamRequest: name: str; goal: str; members: list[TeamMemberSpec]; approval_gate: bool; max_rounds: int; budget: int; kickoff_kind: str; kickoff_text: str; kickoff_assignments: list[dict[str, object]]; kickoff_shared_files: list[str]; kickoff_mentions: list[str]`
  - `@dataclass(frozen=True) class MainTeamOps: create: Callable[[CreateTeamRequest], Awaitable[dict[str, object]]]; resolve: Callable[[str], str]` (name or id → team id, raises `TeamInputError`); `post: Callable[[str, str, object], dict[str, object]]`; `status: Callable[[str], dict[str, object]]`; `disband: Callable[[str], Awaitable[dict[str, object]]]`
  - `class MainTeamToolSource` — `__init__(catalog: dict[str, AgentDefinition], ops: MainTeamOps, *, first_turn_team_ids: set[str])`; `name = "teams"`; `definitions()`, `owns()`, `execute()`
  - `parse_create_team(args, catalog) -> CreateTeamRequest` (raises `TeamInputError`)

- [ ] **Step 1: Write the failing tests** (`tests/test_team_tools.py`)

```python
"""Team tool sources (spec v2 §7.1–§7.3)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentd.chat.storage import ChatThreadStore
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.models import TeamMember, TeamRecord, new_team_id
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService
from agentd.teams.tools import (
    MAIN_TOOL_NAMES,
    MEMBER_TOOL_NAMES,
    MainTeamOps,
    MainTeamToolSource,
    TeamToolSource,
    parse_create_team,
)
from agentd.teams.validation import TeamInputError


def _service(tmp_path: Path):
    teams = ChatThreadStore(tmp_path / "chat.sqlite3").teams
    team = TeamRecord(team_id=new_team_id(), thread_id="t1", name="auth", goal="g",
                      max_rounds=3, budget=160, created_turn_id="x", created_at=datetime.now(UTC))
    teams.create_team(team)
    for label in ("alice", "bob"):
        teams.add_member(TeamMember(team_id=team.team_id, agent_id=f"agent-{label}", label=label))
    svc = TeamService(teams, tmp_path, lambda _a: AgentInfo("explore", "reads", "running"))
    return svc, team.team_id


@pytest.mark.asyncio
async def test_member_tools_post_read_and_errors(tmp_path: Path) -> None:
    svc, tid = _service(tmp_path)
    source = TeamToolSource(svc, tid, "alice", ActivationCounters())
    assert {d.name for d in source.definitions()} == MEMBER_TOOL_NAMES
    out = await source.execute("team_post", {"text": "hello @bob"})
    assert not out.is_error and json.loads(out.output)["seq"] == 1
    bad = await source.execute("team_message", {"member": "dave", "text": "x"})
    assert bad.is_error and "alice, bob" in bad.output
    bob = TeamToolSource(svc, tid, "bob", ActivationCounters())
    read = await bob.execute("team_read", {})
    assert "hello @bob" in read.output and "<<<agent-content" in read.output


@pytest.mark.asyncio
async def test_member_propose_and_stances(tmp_path: Path) -> None:
    svc, tid = _service(tmp_path)
    alice = TeamToolSource(svc, tid, "alice", ActivationCounters())
    out = json.loads((await alice.execute("team_propose", {
        "text": "Plan", "assignments": [{"member": "bob", "part": "api", "files": []}]})).output)
    assert out["proposal_id"] == "P1"
    bob = TeamToolSource(svc, tid, "bob", ActivationCounters())
    assert not (await bob.execute("team_agree", {"proposal_id": "P1", "note": "ok"})).is_error


def _catalog():
    return dict(BUILTIN_AGENTS)


def _args(**over):
    args = {"name": "auth", "goal": "Add login",
            "members": [{"label": "alice", "agent": "explore"},
                        {"label": "bob", "agent": "general-purpose"}],
            "kickoff": {"kind": "post", "text": "Propose a design", "mentions": ["alice"]}}
    args.update(over)
    return args


def test_parse_create_team_defaults() -> None:
    req = parse_create_team(_args(), _catalog())
    assert req.max_rounds == 4 and req.budget == 160 and req.kickoff_mentions == ["alice"]
    proposal = parse_create_team(_args(kickoff={"kind": "proposal", "text": "Do X",
                                               "assignments": []}), _catalog())
    assert proposal.max_rounds == 3


@pytest.mark.parametrize("over,needle", [
    ({"members": [{"label": "alice", "agent": "explore"}]}, "2 to 6 members"),
    ({"members": [{"label": "alice", "agent": "explore"}, {"label": "alice", "agent": "explore"}]},
     "duplicate label"),
    ({"members": [{"label": "alice", "agent": "nope"}, {"label": "bob", "agent": "explore"}]},
     "unknown agent"),
    ({"members": [{"label": "Bad Label", "agent": "explore"}, {"label": "bob", "agent": "explore"}]},
     "label"),
    ({"kickoff": {"kind": "vote", "text": "x"}}, "kickoff.kind"),
    ({"max_rounds": 7}, "max_rounds"),
    ({"budget": 5000}, "budget"),
    ({"kickoff": {"kind": "post", "text": "x", "mentions": ["dave"]}}, "unknown member"),
])
def test_parse_create_team_refusals(over, needle) -> None:
    with pytest.raises(TeamInputError, match=needle):
        parse_create_team(_args(**over), _catalog())


@pytest.mark.asyncio
async def test_main_tools_phase_refusals_and_status() -> None:
    async def create(req):
        return {"team_id": "team-1"}

    async def disband(team_id):
        return {"team_id": team_id, "phase": "DISBANDED"}

    ops = MainTeamOps(
        create=create, resolve=lambda t: "team-1",
        post=lambda tid, text, mentions: {"seq": 2},
        status=lambda tid: {"team_id": tid, "phase": "DELIBERATING"}, disband=disband)
    source = MainTeamToolSource(_catalog(), ops, first_turn_team_ids={"team-1"})
    assert {d.name for d in source.definitions()} == MAIN_TOOL_NAMES
    adopt = await source.execute("adopt_proposal", {"team": "auth", "proposal_id": "P1"})
    assert adopt.is_error and "DEADLOCKED" in adopt.output and "DELIBERATING" in adopt.output
    resume = await source.execute("resume_team", {"team": "auth"})
    assert resume.is_error and "PAUSED" in resume.output
    status = json.loads((await source.execute("team_status", {"team": "auth"})).output)
    assert "runs in the background" in status["note"]  # same turn as create_team
    created = json.loads((await source.execute("create_team", _args())).output)
    assert created["team_id"] == "team-1"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_tools.py --color=no > /tmp/p4t4.txt 2>&1; echo exit=$?; tail -3 /tmp/p4t4.txt`
Expected: exit≠0.

- [ ] **Step 3: Implement `agentd/teams/tools.py`**

```python
"""Team tools (spec v2 §7.1–§7.3). Member tools are prefixed team_ so a model cannot take
them for action types; the main agent's tools are separate and never offered to members.
Descriptions say what each tool does and when it fits — none is ranked above another."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentd.subagents.definitions import AgentDefinition
from agentd.teams.config import team_budget_per_member, team_max_budget, team_max_members
from agentd.teams.service import ActivationCounters, TeamService
from agentd.teams.validation import TeamInputError, check_label, check_text, effective_mentions
from agentd.tools.registry import ToolDefinition, ToolOutput

MEMBER_TOOL_NAMES = frozenset({
    "team_post", "team_message", "team_propose", "team_agree", "team_object", "team_withdraw",
    "team_read"})
MAIN_TOOL_NAMES = frozenset({
    "create_team", "post_board", "team_status", "adopt_proposal", "resume_team", "disband_team"})
# Phase 4 has no milestones yet (the coordinator is Phase 5): nothing wakes the main agent.
BACKGROUND_NOTE = ("The team runs in the background and the user can watch its board. "
                   "Answer the user now.")
_OBJ = "object"
_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
_ASSIGNMENTS = {"type": "array", "items": {"type": _OBJ, "properties": {
    "member": _STR, "part": _STR, "files": _STRS}, "required": ["member", "part", "files"]}}
_EVIDENCE = {"type": _OBJ, "properties": {
    "files": _STRS, "line": {"type": "integer"}, "command": _STR, "output": _STR,
    "quote_seq": {"type": "integer"}}}


def _ok(payload: dict[str, object]) -> ToolOutput:
    return ToolOutput(output=json.dumps(payload, indent=2))


class TeamToolSource:
    name = "team"

    def __init__(self, service: TeamService, team_id: str, label: str,
                 counters: ActivationCounters) -> None:
        self._svc = service
        self._team_id = team_id
        self._label = label
        self._counters = counters

    def definitions(self) -> list[ToolDefinition]:
        def tool(name: str, description: str, props: dict[str, object],
                 required: list[str]) -> ToolDefinition:
            return ToolDefinition(name=name, description=description, parameters={
                "type": _OBJ, "properties": props, "required": required})
        return [
            tool("team_post", "Post on the team board, which every member reads. Mention "
                 "members with @label (or the mentions list) to bring a post to their "
                 "attention; @team reaches everyone.",
                 {"text": _STR, "mentions": _STRS}, ["text"]),
            tool("team_message", "Send one member a direct message only they (and the user) "
                 "see — for something that concerns one person.",
                 {"member": _STR, "text": _STR}, ["member", "text"]),
            tool("team_propose", "Propose how the team does the work: the approach, and who "
                 "does which part with which files. shared_files may be edited by any "
                 "assignee. supersedes closes earlier proposals yours replaces.",
                 {"text": _STR, "assignments": _ASSIGNMENTS, "shared_files": _STRS,
                  "supersedes": _STRS}, ["text", "assignments"]),
            tool("team_agree", "Agree with an open proposal. A note carries a small change "
                 "or an open question without a competing proposal.",
                 {"proposal_id": _STR, "note": _STR}, ["proposal_id"]),
            tool("team_object", "Object to an open proposal, with evidence: files + line, "
                 "command + output, or quote_seq (a board post).",
                 {"proposal_id": _STR, "reason": _STR, "evidence": _EVIDENCE},
                 ["proposal_id", "reason", "evidence"]),
            tool("team_withdraw", "Withdraw one of your own open proposals.",
                 {"proposal_id": _STR}, ["proposal_id"]),
            tool("team_read", "Re-read board posts and your direct messages after since_seq "
                 "(all of them when omitted).", {"since_seq": {"type": "integer"}}, []),
        ]

    def owns(self, tool: str) -> bool:
        return tool in MEMBER_TOOL_NAMES

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        tid, me, n = self._team_id, self._label, self._counters
        try:
            if tool == "team_post":
                post = self._svc.post(tid, me, args.get("text"), args.get("mentions"), n)
                return _ok({"seq": post.seq, "mentions": post.mentions})
            if tool == "team_message":
                post = self._svc.message(tid, me, args.get("member"), args.get("text"), n)
                return _ok({"seq": post.seq, "to": post.recipient})
            if tool == "team_propose":
                post = self._svc.propose(tid, me, args.get("text"), args.get("assignments"),
                                         args.get("shared_files"), args.get("supersedes"), n)
                return _ok({"proposal_id": post.proposal_id, "seq": post.seq})
            if tool == "team_agree":
                post = self._svc.agree(tid, me, args.get("proposal_id"), args.get("note"))
                return _ok({"seq": post.seq, "agreed": post.ref_id})
            if tool == "team_object":
                post = self._svc.object_(tid, me, args.get("proposal_id"), args.get("reason"),
                                         args.get("evidence"))
                return _ok({"seq": post.seq, "objected": post.ref_id})
            if tool == "team_withdraw":
                post = self._svc.withdraw(tid, me, args.get("proposal_id"))
                return _ok({"seq": post.seq, "withdrawn": post.ref_id})
            if tool == "team_read":
                since = args.get("since_seq")
                posts = self._svc.read(tid, me, since if isinstance(since, int) else 0)
                rendered = [self._svc._render_post(tid, p, me) for p in posts]
                return ToolOutput(output="\n\n".join(rendered) or "No posts.")
        except TeamInputError as exc:
            return ToolOutput(output=f"Error: {exc}", is_error=True)
        return ToolOutput(output=f"Error: unknown tool {tool!r}", is_error=True)


@dataclass(frozen=True)
class TeamMemberSpec:
    label: str
    agent: AgentDefinition


@dataclass(frozen=True)
class CreateTeamRequest:
    name: str
    goal: str
    members: list[TeamMemberSpec]
    approval_gate: bool
    max_rounds: int
    budget: int
    kickoff_kind: str
    kickoff_text: str
    kickoff_assignments: list[dict[str, object]]
    kickoff_shared_files: list[str]
    kickoff_mentions: list[str]


def parse_create_team(args: dict[str, object], catalog: dict[str, AgentDefinition]) -> CreateTeamRequest:
    name = check_text(args.get("name"), "name")[:80]
    goal = check_text(args.get("goal"), "goal")
    raw_members = args.get("members")
    limit = team_max_members()
    if not isinstance(raw_members, list) or not 2 <= len(raw_members) <= limit:
        raise TeamInputError(f"a team has 2 to {limit} members")
    members: list[TeamMemberSpec] = []
    for i, item in enumerate(raw_members):
        if not isinstance(item, dict):
            raise TeamInputError(f"members[{i}] must be {{label, agent}}")
        label = check_label(str(item.get("label", "")))
        if any(m.label == label for m in members):
            raise TeamInputError(f"duplicate label {label!r}")
        agent = catalog.get(str(item.get("agent", "")))
        if agent is None:
            raise TeamInputError(f"members[{i}]: unknown agent {item.get('agent')!r}; "
                                 f"available: {', '.join(catalog)}")
        members.append(TeamMemberSpec(label, agent))
    kickoff = args.get("kickoff")
    if not isinstance(kickoff, dict) or kickoff.get("kind") not in ("proposal", "post"):
        raise TeamInputError('kickoff.kind must be "proposal" or "post"')
    kind = str(kickoff["kind"])
    text = check_text(kickoff.get("text"), "kickoff.text")
    roster = [m.label for m in members]
    mentions = (effective_mentions(text, kickoff.get("mentions"), roster)
                if kind == "post" else [])
    assignments = kickoff.get("assignments") or []
    if kind == "proposal" and not isinstance(assignments, list):
        raise TeamInputError("kickoff.assignments must be a list of {member, part, files}")
    raw_rounds = args.get("max_rounds")
    max_rounds = raw_rounds if isinstance(raw_rounds, int) else (3 if kind == "proposal" else 4)
    if not 1 <= max_rounds <= 6:
        raise TeamInputError("max_rounds must be between 1 and 6")
    cap = team_max_budget()
    raw_budget = args.get("budget")
    budget = raw_budget if isinstance(raw_budget, int) else min(
        team_budget_per_member() * len(members), cap)
    if not 1 <= budget <= cap:
        raise TeamInputError(f"budget must be between 1 and {cap} requests")
    shared = kickoff.get("shared_files")
    return CreateTeamRequest(
        name=name, goal=goal, members=members, approval_gate=bool(args.get("approval_gate")),
        max_rounds=max_rounds, budget=budget, kickoff_kind=kind, kickoff_text=text,
        kickoff_assignments=[a for a in assignments if isinstance(a, dict)],
        kickoff_shared_files=[str(f) for f in shared] if isinstance(shared, list) else [],
        kickoff_mentions=mentions)


@dataclass(frozen=True)
class MainTeamOps:
    create: Callable[[CreateTeamRequest], Awaitable[dict[str, object]]]
    resolve: Callable[[str], str]
    post: Callable[[str, str, object], dict[str, object]]
    status: Callable[[str], dict[str, object]]
    disband: Callable[[str], Awaitable[dict[str, object]]]


class MainTeamToolSource:
    name = "teams"

    def __init__(self, catalog: dict[str, AgentDefinition], ops: MainTeamOps, *,
                 first_turn_team_ids: set[str]) -> None:
        self._catalog = catalog
        self._ops = ops
        # Teams created in this turn: their team_status repeats the background note.
        self._created = first_turn_team_ids

    def definitions(self) -> list[ToolDefinition]:
        member = {"type": _OBJ, "properties": {"label": _STR, "agent": {
            "type": "string", "enum": list(self._catalog)}}, "required": ["label", "agent"]}
        kickoff = {"type": _OBJ, "properties": {
            "kind": {"type": "string", "enum": ["proposal", "post"]}, "text": _STR,
            "assignments": _ASSIGNMENTS, "shared_files": _STRS, "mentions": _STRS},
            "required": ["kind", "text"]}
        team = {"team": _STR}
        return [
            ToolDefinition(name="create_team", description=(
                "Start a team of agents that deliberate on a shared board and then implement "
                "together. Each member's role is its agent definition. kickoff \"proposal\" "
                "opens with your own plan for them to check; \"post\" asks the mentioned "
                "members to propose. Returns at once; the team runs in the background."),
                parameters={"type": _OBJ, "properties": {
                    "name": _STR, "goal": _STR, "members": {"type": "array", "items": member},
                    "approval_gate": {"type": "boolean"}, "max_rounds": {"type": "integer"},
                    "budget": {"type": "integer"}, "kickoff": kickoff},
                    "required": ["name", "goal", "members", "kickoff"]}),
            ToolDefinition(name="post_board", description=(
                "Post on a team's board as the main agent — how the user's requests reach "
                "the team. Mention members with @label."),
                parameters={"type": _OBJ, "properties": {**team, "text": _STR,
                                                         "mentions": _STRS},
                            "required": ["team", "text"]}),
            ToolDefinition(name="team_status", description=(
                "A team's phase, round, members and their status, open proposals with each "
                "member's stance, and usage against its budget."),
                parameters={"type": _OBJ, "properties": team, "required": ["team"]}),
            ToolDefinition(name="adopt_proposal", description=(
                "Adopt a proposal for a DEADLOCKED team."),
                parameters={"type": _OBJ, "properties": {**team, "proposal_id": _STR},
                            "required": ["team", "proposal_id"]}),
            ToolDefinition(name="resume_team", description=(
                "Resume a PAUSED team, optionally raising its request budget."),
                parameters={"type": _OBJ, "properties": {**team,
                                                         "extra_budget": {"type": "integer"}},
                            "required": ["team"]}),
            ToolDefinition(name="disband_team", description=(
                "End a team: stop its members and close its board."),
                parameters={"type": _OBJ, "properties": team, "required": ["team"]}),
        ]

    def owns(self, tool: str) -> bool:
        return tool in MAIN_TOOL_NAMES

    async def execute(self, tool: str, args: dict[str, object]) -> ToolOutput:
        try:
            if tool == "create_team":
                result = await self._ops.create(parse_create_team(args, self._catalog))
                self._created.add(str(result.get("team_id", "")))
                return _ok({**result, "note": BACKGROUND_NOTE})
            team_id = self._ops.resolve(str(args.get("team", "")))
            if tool == "post_board":
                return _ok(self._ops.post(team_id, check_text(args.get("text"), "text"),
                                          args.get("mentions")))
            status = self._ops.status(team_id)
            if tool == "team_status":
                if team_id in self._created:
                    status = {**status, "note": BACKGROUND_NOTE}
                return _ok(status)
            if tool == "adopt_proposal":
                raise TeamInputError(f"adopt_proposal is only for a DEADLOCKED team; this team "
                                     f"is {status.get('phase')}")
            if tool == "resume_team":
                raise TeamInputError(f"resume_team is only for a PAUSED team; this team is "
                                     f"{status.get('phase')}")
            if tool == "disband_team":
                return _ok(await self._ops.disband(team_id))
        except TeamInputError as exc:
            return ToolOutput(output=f"Error: {exc}", is_error=True)
        return ToolOutput(output=f"Error: unknown tool {tool!r}", is_error=True)
```

`adopt_proposal` and `resume_team` refuse in every phase Phase 4 can reach (`DELIBERATING`, `DISBANDED`, `FAILED`); Phase 5 replaces those two branches with coordinator calls. `team_read` reaches `TeamService._render_post` across the module boundary on purpose (same package); rename it to `render_post` if a reviewer prefers — then update Task 3's two call sites too.

- [ ] **Step 4: Run and commit**

Run: `.venv/bin/pytest tests/test_team_tools.py --color=no > /tmp/p4t4.txt 2>&1; echo exit=$?; tail -3 /tmp/p4t4.txt`
Expected: `exit=0`.

```bash
git add agentd/teams/tools.py tests/test_team_tools.py
git commit -m "feat(teams): member team_* tools and the main agent's team tools"
```

### Task 5: Controller integration — members, delivery, the inbox delta, reports, disband

**Files:**
- Modify: `agentd/subagents/inbox.py` (`InboxItem.kind` gains `"team"`), `agentd/chat/controller_loop.py` (append `team` items raw), `agentd/chat/controller.py` (below)
- Test: `tests/test_team_controller.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces (all on `ChatController`):
  - `self._teams: TeamService | None` (built in `__init__` when sub-agents are on)
  - `_main_team_source(thread_id, turn_id) -> MainTeamToolSource`
  - `async _create_team(thread_id, turn_id, req: CreateTeamRequest) -> dict[str, object]`
  - `_team_posted(team: TeamRecord, post: TeamPost, *, wake_all: bool = False) -> None` — the interim activation policy
  - `_wake_member(team: TeamRecord, member: TeamMember) -> None`
  - `_drain_member(agent_id, team_id, label) -> list[InboxItem]`
  - `async disband_team(thread_id, team_id) -> dict[str, object]`
  - `team_channel(thread_id, team_id) -> str` (module function: `chat:{thread}:team:{team}`)
  - `TEAM_DELTA = "\x00team-delta\x00"` — the activation-input sentinel `_activate` replaces with the rendered delta
  - Behaviour: members keep `awaiting_peer` (no `partial` mapping); a member's report becomes `system` post `"<label> finished (<status>)"`; `stop_all_agents` also disbands the thread's live teams; `reap_subagents` fails live teams.

- [ ] **Step 1: Write the failing tests** (`tests/test_team_controller.py`)

```python
"""Teams inside the controller (spec v2 §7, Phase 4's interim activation policy)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentd.chat.controller import ChatController
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.subagents.definitions import BUILTIN_AGENTS, AgentDefinition
from agentd.subagents.inbox import InboxItem
from agentd.teams.tools import CreateTeamRequest, TeamMemberSpec
from agentd.workspace.shadow import ShadowWorkspaceManager


class _NoopReasoning:
    async def create_plan(self, *a, **k): raise NotImplementedError
    async def create_patch(self, *a, **k): raise NotImplementedError
    async def create_tool_step(self, *a, **k): raise NotImplementedError
    async def create_planning_step(self, *a, **k): raise NotImplementedError


class _Validator:
    async def run(self, workspace_path): raise NotImplementedError


class _Recording(ScriptedReasoningEngine):
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.seen: list[tuple[str, list[dict[str, object]], list[str], dict[str, object]]] = []

    async def create_controller_step(self, plan_context, history, tool_definitions, **kwargs):  # type: ignore[no-untyped-def]
        label = getattr(kwargs.get("render_ctx"), "agent_label", "")
        names = [str(d.get("name")) for d in tool_definitions]
        self.seen.append((label, [dict(m) for m in history], names, dict(plan_context)))
        return await super().create_controller_step(
            plan_context, history, tool_definitions, **kwargs)


REPORT = {"type": "report", "thought": "t", "summary": "done here", "status": "completed"}
WAITING = {"type": "report", "thought": "t", "summary": "waiting on alice", "status": "awaiting_peer"}


def _make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scripts: dict[str, list[dict[str, object]]]):
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(ws), title="t").thread_id
    engine = _Recording(None, [], controller_step_responses=[
        {"type": "submit_changes", "thought": "d", "summary": "done"}], agent_scripts=scripts)
    orchestrator = AgentOrchestrator(
        store=InMemoryTaskStore(), reasoning_engine=_NoopReasoning(), validator=_Validator(),
        patch_engine=PatchEngine(),
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "shadows"))
    ctrl = ChatController(
        workspace_path=str(ws), reasoning_engine=engine, thread_store=store,
        orchestrator=orchestrator, broadcaster=EventBroadcaster(), retrieval_client=None)
    return ctrl, store, tid, engine


def _request(kind: str = "proposal", agent: AgentDefinition | None = None, **over) -> CreateTeamRequest:
    gp = agent or BUILTIN_AGENTS["general-purpose"]
    base = dict(name="auth", goal="Add login",
                members=[TeamMemberSpec("alice", gp), TeamMemberSpec("bob", gp)],
                approval_gate=False, max_rounds=3, budget=160, kickoff_kind=kind,
                kickoff_text="Plan: alice does the API, bob the tests",
                kickoff_assignments=[], kickoff_shared_files=[],
                kickoff_mentions=[] if kind == "proposal" else ["alice"])
    base.update(over)
    return CreateTeamRequest(**base)


async def _settle(ctrl: ChatController) -> None:
    for _ in range(200):
        tasks = [h.task for h in ctrl._subagents.registry._handles.values()  # type: ignore[union-attr]
                 if h.task is not None and not h.task.done()]
        if not tasks:
            await asyncio.sleep(0)
            if not [h for h in ctrl._subagents.registry._handles.values()  # type: ignore[union-attr]
                    if h.task is not None and not h.task.done()]:
                return
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_create_team_rows_kickoff_and_activation(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [WAITING]})
    result = await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    team_id = str(result["team_id"])
    rows = {r.label: r for r in store.list_agents(tid)}
    assert set(rows) == {"alice", "bob"}
    assert all(r.team_id == team_id and r.dispatcher_id is None and r.depth == 1
               for r in rows.values())
    assert rows["bob"].status == "awaiting_peer"     # members keep it (no partial mapping)
    posts = store.teams.posts(team_id)
    assert (posts[0].seq, posts[0].author, posts[0].kind, posts[0].round) == (1, "main", "proposal", 0)
    assert {p.text for p in posts if p.kind == "system"} == {
        "alice finished (completed)", "bob finished (awaiting_peer)"}
    assert store.teams.member(team_id, "alice").delivered_seq == 1
    alice_first = next(h for label, h, _, _ in engine.seen if label == "alice")
    assert "Plan: alice does the API" in str(alice_first[-1]["content"])
    assert any(m.type == "team_created" for m in store.get_thread(tid).messages)


@pytest.mark.asyncio
async def test_post_kickoff_wakes_only_mentions(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    await ctrl._create_team(tid, "turn1", _request(kind="post"))
    await _settle(ctrl)
    assert {label for label, *_ in engine.seen} == {"alice"}


@pytest.mark.asyncio
async def test_mention_wakes_an_idle_member_with_its_delta(tmp_path, monkeypatch) -> None:
    post_to_bob = {"type": "tool_call", "thought": "tell bob", "tool": "team_post",
                   "args": {"text": "@bob the login route needs a test"}}
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch,
                                     {"alice": [post_to_bob, REPORT], "bob": [REPORT]})
    result = await ctrl._create_team(tid, "turn1", _request(kind="post"))
    await _settle(ctrl)
    bob_calls = [h for label, h, _, _ in engine.seen if label == "bob"]
    assert bob_calls, "bob was never woken by the mention"
    assert "the login route needs a test" in str(bob_calls[0][-1]["content"])
    assert "<<<agent-content author=\"alice (general-purpose)\"" in str(bob_calls[0][-1]["content"])
    team_id = str(result["team_id"])
    assert store.teams.member(team_id, "bob").delivered_seq >= 2


@pytest.mark.asyncio
async def test_post_to_running_member_lands_at_next_drain(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    result = await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    team_id = str(result["team_id"])
    bob = store.teams.member(team_id, "bob")
    monkeypatch.setattr(ctrl._subagents, "is_active", lambda agent_id: agent_id == bob.agent_id)
    ctrl._teams.post(team_id, "alice", "@bob new finding")   # type: ignore[union-attr]
    items = ctrl._drain_member(bob.agent_id, team_id, "bob")
    assert [i.kind for i in items] == ["team"]
    assert "new finding" in items[0].text
    assert store.teams.member(team_id, "bob").delivered_seq == store.teams.posts(team_id)[-1].seq
    # A second marker with nothing new behind it (woken twice) renders nothing.
    ctrl._subagents.deliver(bob.agent_id, InboxItem(kind="team", text="", wakes=True))
    assert ctrl._drain_member(bob.agent_id, team_id, "bob") == []


@pytest.mark.asyncio
async def test_wake_cap_stops_ping_pong(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRUCIBLE_TEAM_MAX_WAKES", "1")
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    result = await ctrl._create_team(tid, "turn1", _request(kind="post"))  # wakes alice once
    await _settle(ctrl)
    team_id = str(result["team_id"])
    ctrl._teams.post(team_id, "bob", "@alice again")   # type: ignore[union-attr]
    await _settle(ctrl)
    system = [p.text for p in store.teams.posts(team_id) if p.kind == "system"]
    assert any("alice has been woken 1 time" in t for t in system)


@pytest.mark.asyncio
async def test_team_tools_survive_definition_filter(tmp_path, monkeypatch) -> None:
    narrow = AgentDefinition(name="narrow", description="reads only", permission="plan",
                             tools=frozenset({"read_file"}), persona="Read.")
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    await ctrl._create_team(tid, "turn1", _request(agent=narrow))
    await _settle(ctrl)
    _, _, names, _ = next(s for s in engine.seen if s[0] == "alice")
    assert {"team_post", "team_agree", "team_object", "team_read"} <= set(names)
    assert "read_file" in names
    assert "run_command" not in names and "search_code" not in names


@pytest.mark.asyncio
async def test_live_team_limit_and_disband(tmp_path, monkeypatch) -> None:
    from agentd.teams.validation import TeamInputError
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    first = await ctrl._create_team(tid, "turn1", _request())
    await ctrl._create_team(tid, "turn1", _request(name="two"))
    with pytest.raises(TeamInputError, match="2 live teams"):
        await ctrl._create_team(tid, "turn1", _request(name="three"))
    await _settle(ctrl)
    out = await ctrl.disband_team(tid, str(first["team_id"]))
    assert out["phase"] == "DISBANDED"
    assert store.teams.get_team(str(first["team_id"])).phase == "DISBANDED"


@pytest.mark.asyncio
async def test_reap_fails_live_teams(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, _ = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    result = await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    ctrl.reap_subagents()
    assert store.teams.get_team(str(result["team_id"])).phase == "FAILED"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_controller.py --color=no --timeout=120 > /tmp/p4t5.txt 2>&1; echo exit=$?; tail -5 /tmp/p4t5.txt`
Expected: exit≠0 (`ChatController` has no `_create_team`).

- [ ] **Step 3: Inbox kind and the loop**

`agentd/subagents/inbox.py`: `kind: Literal["report", "note", "user", "team"]  # "team": a member's rendered board delta (§7.6) — system-written, bodies already framed`.

`controller_loop.py`, in the inbox drain block, change `if item.kind == "user":` to `if item.kind in ("user", "team"):` and its comment to: `# The user's own words (spec §5.3), or a team member's board delta (§7.6) whose header is system-written and whose bodies are already framed — never framed again.`

- [ ] **Step 4: Controller — construction and the main agent's tools**

Imports in `controller.py`: `from agentd.chat.controller_factory import is_teams_enabled` (next to `is_subagents_enabled`), `from agentd.subagents.definitions import definition_from_json` (if not already imported), and

```python
from agentd.teams.config import team_max_live_per_thread, team_max_wakes
from agentd.teams.models import LIVE_TEAM_PHASES, TeamMember, TeamPost, TeamRecord, new_team_id
from agentd.teams.service import ActivationCounters, AgentInfo, TeamService
from agentd.teams.tools import (
    MEMBER_TOOL_NAMES,
    CreateTeamRequest,
    MainTeamOps,
    MainTeamToolSource,
    TeamToolSource,
)
from agentd.teams.validation import TeamInputError
```

Module-level, near `agent_channel` usage:

```python
TEAM_DELTA = "\x00team-delta\x00"   # replaced by the member's rendered delta in _activate


def team_channel(thread_id: str, team_id: str) -> str:
    return f"chat:{thread_id}:team:{team_id}"
```

In `__init__`, after `self._agent_catalog_loader = …`:

```python
        # Agent teams (spec v2 §7): the board service; None when sub-agents are off.
        self._teams: TeamService | None = (
            TeamService(thread_store.teams, Path(workspace_path), self._agent_info,
                        on_post=self._team_posted)
            if is_subagents_enabled() else None)
```

(`workspace_path` and `thread_store` are `__init__`'s own parameter names.)

In `_run_loop`, extend `dispatch_sources`:

```python
        dispatch_sources: list[object] = (
            [self._dispatch_source(thread_id, turn_id, None)]
            if self._subagents is not None and turn_id else [])
        if dispatch_sources and is_teams_enabled() and self._teams is not None:
            dispatch_sources.append(self._main_team_source(thread_id, turn_id))
```

New methods (place after `_dispatch`):

```python
    def _agent_info(self, agent_id: str) -> AgentInfo:
        record = self._store.get_agent(agent_id)
        if record is None:
            return AgentInfo("?", "", "unknown")
        description = str((record.definition or {}).get("description", ""))
        status = record.status
        if self._subagents is not None:
            handle = self._subagents.registry.get(agent_id)
            if handle is not None and self._subagents.is_active(agent_id):
                status = handle.status
        return AgentInfo(record.name, description, status)

    def _main_team_source(self, thread_id: str, turn_id: str) -> MainTeamToolSource:
        assert self._teams is not None
        teams = self._teams

        def post(team_id: str, text: str, mentions: object) -> dict[str, object]:
            p = teams.post(team_id, "main", text, mentions)
            return {"seq": p.seq, "mentions": p.mentions}

        return MainTeamToolSource(self._agent_catalog(), MainTeamOps(
            create=partial(self._create_team, thread_id, turn_id),
            resolve=partial(self._resolve_team, thread_id),
            post=post, status=teams.summary,
            disband=partial(self.disband_team, thread_id)),
            first_turn_team_ids=set())

    def _resolve_team(self, thread_id: str, ref: str) -> str:
        teams = self._store.teams.list_teams(thread_id)
        for team in reversed(teams):   # newest first: names may repeat across teams
            if ref in (team.team_id, team.name):
                return team.team_id
        names = ", ".join(t.name for t in teams) or "none"
        raise TeamInputError(f"no team {ref!r} in this thread (teams: {names})")
```

- [ ] **Step 5: Controller — creating a team**

```python
    async def _create_team(
        self, thread_id: str, turn_id: str, req: CreateTeamRequest,
    ) -> dict[str, object]:
        """create_team (spec v2 §7.1): rows, the kickoff post, then the first activations.
        Returns at once; the team runs in the background."""
        assert self._teams is not None and self._subagents is not None
        log = self._write_log_for(thread_id)
        assert log is not None
        live_teams = self._store.teams.count_live_teams(thread_id)
        if live_teams >= team_max_live_per_thread():
            raise TeamInputError(
                f"this thread already has {live_teams} live teams (limit "
                f"{team_max_live_per_thread()}) — disband one first")
        live_agents = self._store.count_live_agents(thread_id)
        if live_agents + len(req.members) > subagent_max_live_per_thread():
            raise TeamInputError(
                f"{live_agents} agents are running in this thread; {len(req.members)} more "
                f"would pass the limit of {subagent_max_live_per_thread()}")
        now = datetime.now(UTC)
        team = TeamRecord(
            team_id=new_team_id(), thread_id=thread_id, name=req.name, goal=req.goal,
            max_rounds=req.max_rounds, round_started_at=now, approval_gate=req.approval_gate,
            budget=req.budget, created_turn_id=turn_id,
            checkpoint_seq=self._store.current_checkpoint_seq(thread_id), created_at=now)
        self._store.teams.create_team(team)
        thread_channel = f"chat:{thread_id}"
        members: list[dict[str, str]] = []
        for spec in req.members:
            context, definition = self._context_for(
                agent_id=new_agent_id(), name=spec.agent.name, label=spec.label, depth=1,
                parent_agent_id=None, snapshot=spec.agent, inherited={})
            log.register_agent(context.agent_id)
            self._store.insert_agent(AgentRecord(
                agent_id=context.agent_id, thread_id=thread_id, turn_id=turn_id,
                parent_agent_id=None, depth=1, name=context.name, label=context.label,
                prompt=f"Member {context.label} of team {req.name!r}: {req.goal}",
                status="awaiting_peer", definition=definition_to_json(definition),
                dispatcher_id=None, team_id=team.team_id, checkpoint_seq=team.checkpoint_seq))
            self._store.teams.add_member(TeamMember(
                team_id=team.team_id, agent_id=context.agent_id, label=context.label))
            self._broadcaster.broadcast(thread_channel, {"type": "agent_started", "payload": {
                "agent_id": context.agent_id, "parent_agent_id": None, "depth": 1,
                "name": context.name, "label": context.label}})
            members.append({"label": context.label, "agent_id": context.agent_id})
        anchor = ChatMessage(role="agent", content="", type="team_created", metadata={
            "team_id": team.team_id, "name": team.name, "turn_id": turn_id,
            "agent_ids": [m["agent_id"] for m in members]})
        self._mark_pills_boundary(thread_id)
        self._store.append_message(thread_id, anchor)
        self._broadcaster.broadcast(thread_channel, {
            "type": "team_created", "payload": {"message": anchor.model_dump(mode="json")}})
        kickoff = self._store.teams.append_post(
            team.team_id, author="main",
            kind="proposal" if req.kickoff_kind == "proposal" else "post",
            text=req.kickoff_text, mentions=req.kickoff_mentions, round=0,
            payload=({"assignments": req.kickoff_assignments,
                      "shared_files": req.kickoff_shared_files, "supersedes": []}
                     if req.kickoff_kind == "proposal" else {}))
        self._team_posted(team, kickoff, wake_all=req.kickoff_kind == "proposal")
        return {"team_id": team.team_id, "members": members, "phase": team.phase,
                "round": team.round}
```

(`subagent_max_live_per_thread`, `definition_to_json`, `AgentRecord` and `ChatMessage` are already imported by the controller for `_dispatch`; so is `new_agent_id` (from `agentd.subagents.context`).)

- [ ] **Step 6: Controller — delivery (the interim activation policy) and the delta**

```python
    def _team_posted(self, team: TeamRecord, post: TeamPost, *, wake_all: bool = False) -> None:
        """Every stored post: stream it, then wake whom it concerns. Phase 4's interim
        policy — Phase 5's coordinator replaces the waking half (spec v2 §8)."""
        self._broadcaster.broadcast(team_channel(team.thread_id, team.team_id), {
            "type": "team_post", "seq": post.seq,
            "payload": {"post": post.model_dump(mode="json")}})
        if post.kind == "system" or team.phase not in LIVE_TEAM_PHASES:
            return
        members = self._store.teams.members(team.team_id)
        if post.recipient is not None:
            targets = [m for m in members if m.label == post.recipient]
        elif wake_all or "team" in post.mentions:
            targets = members
        else:
            targets = [m for m in members if m.label in post.mentions]
        for member in targets:
            if member.label != post.author:
                self._wake_member(team, member)

    def _wake_member(self, team: TeamRecord, member: TeamMember) -> None:
        assert self._subagents is not None and self._teams is not None
        if self._subagents.is_active(member.agent_id):
            # Running: a marker; the next drain renders whatever is new by then (§3.6).
            self._subagents.deliver(member.agent_id, InboxItem(
                kind="team", text="", wakes=True, source_id=f"team:{team.team_id}",
                author="team board"))
            return
        cap = team_max_wakes()
        wakes = self._store.teams.bump_wakes(team.team_id, member.label)
        if wakes > cap:
            if wakes == cap + 1:
                self._teams.system_post(
                    team.team_id, f"{member.label} has been woken {cap} time"
                    f"{'s' if cap != 1 else ''} this phase — not waking it again in this phase")
            return
        handle = self._handle_from_record(team.thread_id, member.agent_id)
        handle.activation_input = TEAM_DELTA
        self._subagents.enqueue(handle, self._activate)

    def _drain_member(self, agent_id: str, team_id: str, label: str) -> list[InboxItem]:
        """A member's inbox: every team marker collapses into one rendered delta, and
        delivered_seq advances to the highest seq that delta actually covers (§7.6)."""
        assert self._teams is not None
        items = self._drain_and_mark(agent_id)
        others = [i for i in items if i.kind != "team"]
        if len(others) == len(items):
            return items
        member = self._store.teams.member(team_id, label)
        delta, top = self._teams.render_delta(team_id, label)
        if member is None or top <= member.delivered_seq:
            # The marker's posts already arrived in this activation's input (a member woken
            # twice before it started): nothing new to show.
            return others
        self._store.teams.set_delivered_seq(team_id, label, top)
        return [InboxItem(kind="team", text=delta, wakes=False, author="team board"), *others]
```

`_on_leftover` — a team member's leftovers restart it on its delta (any helper reports in them are framed after it):

```python
    def _on_leftover(self, handle: AgentHandle, items: list[InboxItem]) -> None:
        """Input that arrived after the last drain re-activates a lone agent (spec §3.6).
        Scheduled, not run inline: the supervisor calls this from inside the finishing
        activation's task."""
        others = [i for i in items if i.kind != "team"]
        text = "\n\n".join(
            "New message:\n" + frame(i.author or i.source_id or "agent", i.kind, i.text)
            for i in others)
        if self._store.teams.member_for_agent(handle.agent_id) is not None:
            text = TEAM_DELTA + ("\n\n" + text if text else "")
        asyncio.get_running_loop().call_soon(self._start_activation, handle, text)
```

- [ ] **Step 7: Controller — `_activate` for members**

Near the top of `_activate`, after `activation_input = handle.activation_input or handle.prompt`:

```python
        membership = self._store.teams.member_for_agent(ctx.agent_id)
        team_counters = ActivationCounters()
        if membership is not None and activation_input.startswith(TEAM_DELTA):
            assert self._teams is not None
            # The delta is rendered now, when the input lands in the history, and the
            # cursor advances only to what it covers (spec §7.6).
            delta, top = self._teams.render_delta(membership.team_id, membership.label)
            self._store.teams.set_delivered_seq(membership.team_id, membership.label, top)
            activation_input = delta + activation_input[len(TEAM_DELTA):]
```

In `sources(render_ctx)`, before `return built`:

```python
            if membership is not None and self._teams is not None:
                built.append(TeamToolSource(self._teams, membership.team_id,
                                            membership.label, team_counters))
```

After `names = child_tool_names(...)`:

```python
        if membership is not None:
            # Members can always take part (spec §7.3): only the team tools are exempt from
            # the definition's tools/disallowedTools filter — nothing else is re-admitted.
            names = names | MEMBER_TOOL_NAMES
```

`loop.run(...)`'s `inbox_drain` becomes:

```python
                inbox_drain=(
                    partial(self._drain_member, ctx.agent_id, membership.team_id,
                            membership.label)
                    if membership is not None else partial(self._drain_and_mark, ctx.agent_id)),
```

and the report mapping:

```python
                if status == "awaiting_peer" and membership is None:
                    status = "partial"  # a lone agent has no peer to wait on
```

- [ ] **Step 8: Controller — reports, disband, stop-all, reap**

At the top of `_route_report`, after `record = self._store.get_agent(...)`:

```python
        if record is not None and record.team_id is not None:
            self._team_member_reported(record, result)
            return
```

and:

```python
    def _team_member_reported(self, record: AgentRecord, result: ChildResult) -> None:
        """Phase 4: a member's report is a system line on the board (its full text stays on
        the agent row). It wakes nobody; Phase 5's coordinator gives reports their meaning."""
        if self._teams is None or record.team_id is None:
            return
        team = self._store.teams.get_team(record.team_id)
        if team is None or team.phase not in LIVE_TEAM_PHASES:
            return
        self._teams.system_post(team.team_id, f"{record.label} finished ({result.status})")

    async def disband_team(self, thread_id: str, team_id: str) -> dict[str, object]:
        """Stop every member, close the board (spec v2 §8.9's DISBANDED)."""
        team = self._store.teams.get_team(team_id)
        if team is None or team.thread_id != thread_id:
            raise TeamInputError(f"no team {team_id!r} in this thread")
        if team.phase in LIVE_TEAM_PHASES:
            # Ended first, so a member's stopped report posts no "finished" line.
            self._store.teams.update_team(team_id, phase="DISBANDED", end_reason="disbanded",
                                          ended_at=datetime.now(UTC))
            assert self._subagents is not None and self._teams is not None
            for member in self._store.teams.members(team_id):
                await self._subagents.stop(member.agent_id, "disband")
            # system_post streams it on the team channel; _team_posted wakes nobody for it.
            self._teams.system_post(team_id, "The team was disbanded.")
            self._broadcaster.broadcast(team_channel(thread_id, team_id), {
                "type": "team_phase", "payload": {"phase": "DISBANDED", "round": team.round,
                                                  "paused_reason": None}})
        return {"team_id": team_id, "phase": "DISBANDED"}
```

`stop_all_agents` — before the agent loop:

```python
        for team in self._store.teams.list_teams(thread_id):
            if team.phase in LIVE_TEAM_PHASES:
                await self.disband_team(thread_id, team.team_id)
```

`reap_subagents` — after `reaped = …`: `failed_teams = self._store.teams.fail_live_teams("backend restarted")` and add `teams=%d` / `len(failed_teams)` to its log line.

- [ ] **Step 9: Run and commit**

Run: `.venv/bin/pytest tests/test_team_controller.py tests/test_agent_activation.py tests/test_background_dispatch.py tests/test_subagent_lifecycle.py --color=no --timeout=120 > /tmp/p4t5.txt 2>&1; echo exit=$?; tail -5 /tmp/p4t5.txt`
Expected: `exit=0`.

```bash
git add agentd/subagents/inbox.py agentd/chat/controller_loop.py agentd/chat/controller.py tests/test_team_controller.py
git commit -m "feat(teams): create teams, deliver posts to members, member inbox deltas"
```

### Task 6: Prompts — the `<<team>>` tag, `_TEAM_BLOCK`, the main agent's TEAMS block, the status tail

**Files:**
- Modify: `agentd/prompting/tagged.py` (`RenderContext.team_brief`, the `team` tag), `agentd/teams/service.py` (`brief`), `agentd/chat/controller_prompts.py` (`_TEAM_BLOCK`, `_TEAMS_MAIN_BLOCK`, `team_status` in the payload tail), `agentd/chat/controller_loop.py` (`status_tail`, the tool-name-as-type correction), `agentd/chat/controller.py` (pass both into a member's loop)
- Create: `tests/test_prompt_goldens_teams.py`, `tests/goldens/controller_prompt_teams.json` (captured)
- Modify: `tests/test_prompt_leak_lint.py` (team vocabulary)
- Test: `tests/test_team_prompts.py`

**Interfaces:**
- Consumes: `TeamService` (Task 3), `MEMBER_TOOL_NAMES`/`MAIN_TOOL_NAMES`/`TeamToolSource`/`MainTeamToolSource` (Task 4), the member branch of `_activate` (Task 5).
- Produces:
  - `RenderContext.team_brief: str = ""`, `RenderContext.has_team -> bool`; `RenderContext.for_agent(..., team_brief: str = "")`
  - tag `<<team>>…<</team>>` — renders only for a child whose `team_brief` is non-empty
  - `TeamService.brief(team_id, label) -> str` — goal + roster, fixed for the team's life (cache-stable system prompt)
  - `controller_prompts._TEAM_BLOCK`, `controller_prompts._TEAMS_MAIN_BLOCK`, `controller_prompts.TEAM_FRAMING` (the spec §7.5 text, verbatim)
  - `build_controller_step_payload` emits `team_status` in the tail when `plan_context["team_status"]` is a non-empty string
  - `ControllerLoop.run(..., status_tail: Callable[[], str] | None = None)` — called every iteration, result stored in `plan_context["team_status"]`
  - `controller_loop._tool_name_as_type_correction(atype, tool_names) -> str | None`

The roster and goal go into the **system prompt** (they never change during a team's life, so the cached prefix holds); the phase, round, stances and unread counts go into the **payload tail** every iteration (spec §7.6). `_TEAM_BLOCK` renders after the persona so "your persona, above" is literally true. A member's role block still says "dispatcher": `_TEAM_BLOCK` says who that is for a member (the main agent that created the team) rather than adding a negated tag to `_AGENT_ROLE_BLOCK`, which keeps every existing child prompt byte-identical.

- [ ] **Step 1: Write the failing tests** (`tests/test_team_prompts.py`)

```python
"""Team prompt text (spec v2 §7.5) and the status tail (§7.6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentd.chat.controller_loop import _tool_name_as_type_correction
from agentd.chat.controller_prompts import (
    TEAM_FRAMING,
    build_controller_step_payload,
    format_controller_system_prompt,
)
from agentd.chat.storage import ChatThreadStore
from agentd.prompting.tagged import PromptTemplateError, RenderContext, render_prompt, tagged
from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.models import TeamMember, TeamRecord
from agentd.teams.service import AgentInfo, TeamService
from agentd.teams.tools import MainTeamOps, MainTeamToolSource


def _ctx(team_brief: str = "") -> RenderContext:
    agent = AgentContext(
        agent_id="agent-1", name="general-purpose", label="alice", depth=1,
        parent_agent_id=None, permission="default",
        allowed_types=("tool_call", "edit", "progress", "report"), persona="", max_iters=10)
    return RenderContext.for_agent(
        agent, tools=frozenset({"read_file", "team_post", "team_agree"}),
        shell_policy="ask", team_brief=team_brief)


def test_team_tag_renders_only_for_members() -> None:
    template = tagged("t", "a<<team>>B<</team>>c")
    assert render_prompt(template, _ctx("goal: x")) == "aBc"
    assert render_prompt(template, _ctx()) == "ac"
    assert render_prompt(template, RenderContext.main()) == "ac"


def test_team_tag_takes_no_argument() -> None:
    with pytest.raises(PromptTemplateError, match="unknown tag"):
        tagged("t", "<<team:x>>a<</team:x>>")


def test_member_prompt_has_framing_brief_and_examples_after_persona() -> None:
    text = format_controller_system_prompt(
        [{"name": "team_post"}], task_subsystem_enabled=False, memory_enabled=False,
        render_ctx=_ctx("Team 'auth'. Goal: add login.\nRoster:\n- alice (you)\n- bob"),
        persona="You review code.")
    assert TEAM_FRAMING in text
    assert "Goal: add login." in text
    assert text.index("You review code.") < text.index(TEAM_FRAMING)
    assert '"tool":"team_agree"' in text and '"tool":"team_object"' in text
    assert "awaiting_peer" in text


def test_non_team_child_and_main_never_see_member_text() -> None:
    child = format_controller_system_prompt(
        [{"name": "read_file"}], task_subsystem_enabled=False, memory_enabled=False,
        render_ctx=_ctx())
    main = format_controller_system_prompt(
        [{"name": "read_file"}], task_subsystem_enabled=False, memory_enabled=False)
    for text in (child, main):
        assert TEAM_FRAMING not in text
        assert "team_agree" not in text and "TEAM" not in text


async def _never(*a, **k):  # type: ignore[no-untyped-def]
    raise AssertionError("not called")


def test_main_teams_block_keyed_off_create_team() -> None:
    tools = [d.model_dump() for d in MainTeamToolSource(BUILTIN_AGENTS, MainTeamOps(
        create=_never, resolve=lambda r: r, post=lambda *a: {}, status=lambda t: {},
        disband=_never), first_turn_team_ids=set()).definitions()]
    text = format_controller_system_prompt(tools, task_subsystem_enabled=False,
                                           memory_enabled=False)
    assert "TEAMS (create_team" in text
    assert '"kind":"proposal"' in text and '"kind":"post"' in text
    assert "60–90 requests per member" in text
    assert TEAM_FRAMING not in text


def test_status_tail_rides_the_payload_tail() -> None:
    payload = build_controller_step_payload(
        {"goal": "g", "team_status": "team 'auth': phase DELIBERATING"},
        [{"role": "user", "content": "x"}], [], phase="AGENT")
    keys = list(payload)
    assert payload["team_status"] == "team 'auth': phase DELIBERATING"
    assert keys.index("team_status") > keys.index("conversation_history")
    bare = build_controller_step_payload({"goal": "g", "team_status": ""}, [], [], phase="AGENT")
    assert "team_status" not in bare


def test_tool_name_as_type_gets_the_wrapper() -> None:
    text = _tool_name_as_type_correction("team_agree", {"team_agree", "read_file"})
    assert text is not None
    assert '"type":"tool_call"' in text and '"tool":"team_agree"' in text
    assert _tool_name_as_type_correction("team_agree", {"read_file"}) is None
    assert _tool_name_as_type_correction("read_file", {"read_file"}) is None  # not a team tool


def test_brief_names_goal_and_roster(tmp_path: Path) -> None:
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    from datetime import UTC, datetime
    store.teams.create_team(TeamRecord(
        team_id="team-1", thread_id="t1", name="auth", goal="Add login", max_rounds=3,
        round_started_at=datetime.now(UTC), approval_gate=False, budget=160,
        created_turn_id="turn", checkpoint_seq=0, created_at=datetime.now(UTC)))
    for label in ("alice", "bob"):
        store.teams.add_member(TeamMember(team_id="team-1", agent_id=f"a-{label}", label=label))
    svc = TeamService(store.teams, tmp_path,
                      lambda aid: AgentInfo("reviewer", "Reviews diffs", "idle"),
                      on_post=lambda team, post: None)
    brief = svc.brief("team-1", "alice")
    assert "Team 'auth'" in brief and "Goal: Add login" in brief
    assert "- alice (you): reviewer — Reviews diffs" in brief
    assert "- bob: reviewer — Reviews diffs" in brief
    assert "[idle]" not in brief   # status changes; it belongs in the tail, not here
```

Read `TeamRecord`'s required fields from Task 1 before running; if the constructor above misses one, add it (the record must match Task 1's model exactly).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_prompts.py --color=no --timeout=120 > /tmp/p4t6.txt 2>&1; echo exit=$?; tail -5 /tmp/p4t6.txt`
Expected: exit≠0 (`TEAM_FRAMING` does not exist).

- [ ] **Step 3: The tag**

`agentd/prompting/tagged.py`:

```python
    agent_id: str = ""
    agent_label: str = ""
    # A team member's goal + roster (spec v2 §7.5); empty for everyone else. Fixed for the
    # team's life, so it can sit in the cached system prompt.
    team_brief: str = ""
```

`for_agent` gains `team_brief: str = ""` and passes it through. Add:

```python
    @property
    def has_team(self) -> bool:
        return not self.is_main and bool(self.team_brief)
```

`_holds`: `if kind == "team": return ctx.has_team`. `_check_tag`: add `or (kind == "team" and arg is None)`. Update the module docstring's tag list if it has one.

- [ ] **Step 4: `TeamService.brief`**

```python
    def brief(self, team_id: str, label: str) -> str:
        """Goal + roster for a member's system prompt. Status is left out on purpose: it
        changes, and changing text here would break the cached prefix (status_text has it)."""
        # Not _team(): a late leftover activation may start after the team ended, and its
        # prompt must still build (the member then reports with nothing to do).
        team = self._store.get_team(team_id)
        if team is None:
            raise TeamInputError(f"no team {team_id!r}")
        lines = [f"Team {team.name!r}. Goal: {team.goal}", "Roster:"]
        for m in self._store.members(team_id):
            info = self._agent_info(m.agent_id)
            you = " (you)" if m.label == label else ""
            lines.append(f"- {m.label}{you}: {info.name} — {info.description}")
        return "\n".join(lines)
```

- [ ] **Step 5: The member block and the main block** (`controller_prompts.py`, after `_PERSONA_BLOCK_TEMPLATE`)

```python
# Spec v2 §7.5, verbatim. Kept as a constant so tests and goldens pin the exact text.
TEAM_FRAMING = """You are one member of a team working together on a shared board. Treat it as a working session in one
room: everyone sees everything posted on the board, and the team's decision is only as good as the
scrutiny it gets.
- Share what others need. Post findings, constraints and risks you discover — on the board when they matter
  to the team, by direct message when they matter to one person.
- Check before you agree. When a post or proposal makes a claim about the code, verify the part your role
  covers before you agree. If you could not check something, still state your stance, and say in the note
  what you did not verify. During deliberation, check by reading (read the file, search, query the graph):
  commands may need the user's approval, and the team waits for it.
- Disagree with evidence. An objection backed by a file and line, or a command and its output, moves the
  team forward; one without evidence stalls it.
- Build on others' work. When an existing proposal is close, agree with a note describing the change.
  Supersede it when your change is substantial.
- Say what you don't know. Ask the member who owns that area instead of guessing. In deliberation the
  answer arrives next round, so state your stance now — object, or agree and note the open question —
  rather than waiting for it.
- Your role (your persona, above) decides where you dig deepest: a reviewer checks claims, an implementer
  checks feasibility, an architect checks design fit."""

# Member-only (spec v2 §7.5): rendered after the persona. {team_framing} and {team_brief} are
# substituted after rendering (.replace — goals may contain { }).
_TEAM_BLOCK = tagged("_TEAM_BLOCK", """<<team>>

TEAM
{team_framing}

{team_brief}

HOW THE TEAM WORKS
- Your input is the board: each new post and direct message to you, framed with its author.
  team_status (in your payload) shows the phase, round, open proposals with your stance, your
  assignment, and your unread counts — it is current on every step.
- Your dispatcher is the main agent that created this team; the user watches the board. Your
  report is recorded and appears on the board as a one-line notice.
- team_post reaches everyone; @label (or mentions) brings it to those members' attention, @team
  to everyone's. team_message reaches one member. team_propose sets out an approach with
  assignments (member, part, files). team_agree and team_object state your stance on an open
  proposal; an objection carries evidence: files + line, command + output, or quote_seq.
  team_withdraw closes one of your own proposals. team_read re-reads the board.
- Report when your part of this round is done: status "completed", or "awaiting_peer" naming
  whom you wait on.
Example — verify by reading, then state stances, then report:
{"type":"tool_call","thought":"P3 says login() skips the rate limiter; my role covers api/","tool":"read_file","args":{"path":"api/login.py"}}
{"type":"tool_call","thought":"confirmed at line 42; small issue only","tool":"team_agree","args":{"proposal_id":"P3","note":"Confirmed: login() calls check_token before the limiter (api/login.py:42). Also cover the refresh route."}}
{"type":"tool_call","thought":"does P5 list every caller?","tool":"search_code","args":{"pattern":"check_token\\\\(","path_filter":"*.py"}}
{"type":"tool_call","thought":"P5 misses a caller","tool":"team_object","args":{"proposal_id":"P5","reason":"P5 changes check_token's signature but misses the caller in api/admin.py.","evidence":{"files":["api/admin.py"],"line":17}}}
{"type":"tool_call","thought":"P7's latency claim needs a benchmark; reading cannot check it","tool":"team_agree","args":{"proposal_id":"P7","note":"not verified: the latency claim — needs a benchmark in review"}}
{"type":"report","thought":"stances stated","summary":"Agreed P3 (with refresh-route note) and P7 (latency unverified); objected to P5 (missed caller api/admin.py:17).","status":"completed"}
Example — a proposal that is close: agree with a note describing the change, rather than a
competing proposal:
{"type":"tool_call","thought":"P4 is right apart from one file name","tool":"team_agree","args":{"proposal_id":"P4","note":"Use api/limits.py, not api/limit.py — the latter does not exist."}}
Example — implementing, when you need a change in a file another member owns:
{"type":"edit","thought":"my part: the limiter","patch_ops":[{"op":"create_file","file":"api/limiter.py","content":"class Limiter:\\n    pass\\n","reason":"limiter skeleton"}]}
{"type":"tool_call","thought":"routes.py is bob's file","tool":"team_message","args":{"member":"bob","text":"routes.py needs `from api.limiter import Limiter` and Limiter() in login — your file."}}
{"type":"report","thought":"blocked on bob","summary":"Limiter in api/limiter.py done; waiting on bob to wire it into routes.py.","status":"awaiting_peer"}
Example — a message that only acknowledges ("got it", "thanks") adds nothing to the board: keep
working, or report awaiting_peer instead.
Example — reviewing: run the tests, then state your stance on the closing proposal:
{"type":"tool_call","thought":"verify from my role's angle","tool":"run_command","args":{"command":"pytest tests/test_login.py"}}
{"type":"tool_call","thought":"one test fails","tool":"team_object","args":{"proposal_id":"P9","reason":"The refresh route is still unlimited.","evidence":{"command":"pytest tests/test_login.py","output":"FAILED tests/test_login.py::test_refresh_is_limited"}}}
<</team>>""")

# Main-only: appended when create_team is offered (CRUCIBLE_TEAMS_ENABLED).
_TEAMS_MAIN_BLOCK = tagged("_TEAMS_MAIN_BLOCK", """<<main>>

TEAMS (create_team, post_board, team_status, adopt_proposal, resume_team, disband_team)
create_team starts a team of agents that talk on a shared board: they post findings, propose
approaches with assignments, and agree or object with evidence. Each member's role is its agent
definition. It fits work where several perspectives should check one plan before and while it
is built; dispatch_agents fits independent parts that need no discussion.
- kickoff "proposal" opens with your own plan for the members to check; kickoff "post" asks the
  mentioned members to propose.
- A full run costs roughly 60–90 requests per member (deliberation, implementation, review):
  set budget with that in mind.
- After create_team, answer the user: the team runs in the background and the user watches its
  board. post_board is how the user's later requests reach the team. team_status shows where it
  stands when the user asks.
Example — you have a plan and want it checked:
{"type":"tool_call","thought":"two reviewers should check my plan","tool":"create_team","args":{"name":"auth","goal":"Add rate-limited login","members":[{"label":"api","agent":"general-purpose"},{"label":"review","agent":"explore"}],"kickoff":{"kind":"proposal","text":"api adds api/limiter.py and wires it into api/routes.py; review checks the callers.","assignments":[{"member":"api","part":"limiter + wiring","files":["api/limiter.py","api/routes.py"]}]}}}
Example — you want the members to propose:
{"type":"tool_call","thought":"let the members propose a design","tool":"create_team","args":{"name":"cache","goal":"Cache user lookups","members":[{"label":"arch","agent":"general-purpose"},{"label":"impl","agent":"general-purpose"}],"kickoff":{"kind":"post","text":"@arch @impl propose how to cache get_user without stale reads.","mentions":["arch","impl"]}}}
{"type":"answer","thought":"it runs in the background","answer":"I started a team for the cache design; you can follow its board in the team window."}
<</main>>""")
```

In `format_controller_system_prompt`, inside the `if not ctx.is_main:` branch, after the persona:

```python
        if ctx.has_team:
            base += (render_prompt(_TEAM_BLOCK, ctx)
                     .replace("{team_framing}", TEAM_FRAMING)
                     .replace("{team_brief}", ctx.team_brief))
```

and after the dispatch block:

```python
    if ctx.is_main and any(str((d or {}).get("name", "")) == "create_team"
                           for d in tool_definitions if isinstance(d, dict)):
        base += render_prompt(_TEAMS_MAIN_BLOCK, ctx)
```

Escaping check: the examples sit in a normal (non-raw) Python string, so `\\n` renders as `\n` and `\\\\(` as `\\(` — the same escaping `_DISPATCH_BLOCK`'s examples use. If `tagged()` rejects `<<` anywhere in the examples, there is none; `check_token\\(` has no `<`.

In `build_controller_step_payload`, right after the `todo_status` block:

```python
    # A team member's phase, stances and unread counts (spec v2 §7.6): rebuilt every
    # iteration, tail-only so the cached prefix holds; omitted for everyone else.
    team_status = plan_context.get("team_status")
    if isinstance(team_status, str) and team_status:
        payload["team_status"] = team_status
```

- [ ] **Step 6: The loop**

`ControllerLoop.run` gains `status_tail: Callable[[], str] | None = None` and passes it to `_iterate` (add the same parameter there). In `_iterate`, next to `plan_context["todo_status"] = self._ledger.render()`:

```python
            if status_tail is not None:
                # Rebuilt from the database every iteration (spec v2 §7.6) — cheap, and the
                # only copy of the phase state that survives a long activation or compaction.
                plan_context["team_status"] = status_tail()
```

Below `_reserved_tool_name_correction`:

```python
_TOOL_AS_TYPE_TEMPLATE = tagged("tool_as_type", (
    "'{tool}' is a tool, not an action type. Call it with a tool_call: "
    '{"type":"tool_call","thought":"…","tool":"{tool}","args":{…}}'))


def _tool_name_as_type_correction(atype: str, tool_names: set[str] | frozenset[str]) -> str | None:
    """The reverse of _reserved_tool_name_correction (spec v2 §7.3): an action whose type is
    a team tool's name gets the tool_call wrapper shown, not the generic malformed text."""
    from agentd.teams.tools import MAIN_TOOL_NAMES, MEMBER_TOOL_NAMES

    if atype not in tool_names or atype not in (MEMBER_TOOL_NAMES | MAIN_TOOL_NAMES):
        return None
    return render_prompt(_TOOL_AS_TYPE_TEMPLATE, _MAIN).replace("{tool}", atype)
```

(Move the import to the top of the module if `agentd.teams.tools` does not import `controller_loop` — check with `python -c "import agentd.chat.controller_loop"` after moving; the CLAUDE.md rule is imports at the top.) In the correction chain:

```python
            correction = (
                (_tool_name_as_type_correction(atype, tool_names)
                 or malformed_correction(self._render_ctx, self._allowed_action_types()))
                if atype not in self._allowed_action_types()
                else ...  # unchanged
```

`tool_names` is the `frozenset` of offered tool names assigned near the top of `_iterate`.

- [ ] **Step 7: The controller passes the brief and the tail**

In `_activate` (Task 5's member branch is above it):

```python
        render_ctx = RenderContext.for_agent(
            ctx, tools=names,
            shell_policy="allow_all" if self._shell_policy == ShellPolicy.ALLOW_ALL else "ask",
            team_brief=(self._teams.brief(membership.team_id, membership.label)
                        if membership is not None and self._teams is not None else ""))
```

and in `loop.run(...)`:

```python
                status_tail=(
                    partial(self._teams.status_text, membership.team_id, membership.label)
                    if membership is not None and self._teams is not None else None),
```

Add to `tests/test_team_controller.py`:

```python
@pytest.mark.asyncio
async def test_member_sees_team_block_and_status_tail(tmp_path, monkeypatch) -> None:
    ctrl, store, tid, engine = _make(tmp_path, monkeypatch, {"alice": [REPORT], "bob": [REPORT]})
    await ctrl._create_team(tid, "turn1", _request())
    await _settle(ctrl)
    _, _, _, plan_context = next(s for s in engine.seen if s[0] == "alice")
    assert "phase DELIBERATING" in str(plan_context["team_status"])
    assert "- alice (you)" in str(plan_context["team_status"])
```

(`_Recording` copies `plan_context` per call, so this reads the context the engine actually got.)

- [ ] **Step 8: Leak lint and golden**

`tests/test_prompt_leak_lint.py`: add `"_TEAM_BLOCK"` and `"_TEAMS_MAIN_BLOCK"` to the `TEMPLATES` names, plus `"tool_as_type": cl._TOOL_AS_TYPE_TEMPLATE`. Then add:

```python
_TEAM_VOCABULARY = ("team_post", "team_agree", "team_object", "team_propose", "TEAM",
                    "shared board")


@pytest.mark.parametrize("permission", ["default", "acceptEdits", "dontAsk", "plan"])
def test_team_text_never_reaches_a_non_team_child(permission: str) -> None:
    from agentd.subagents.context import AgentContext
    agent = AgentContext(agent_id="a", name="general-purpose", label="x", depth=1,
                         parent_agent_id=None, permission=permission,
                         allowed_types=("tool_call", "progress", "report"), persona="", max_iters=10)
    ctx = RenderContext.for_agent(agent, tools=frozenset({"read_file"}), shell_policy="ask")
    for name, template in TEMPLATES.items():
        rendered = render_prompt(template, ctx)
        leaked = [w for w in _TEAM_VOCABULARY if w in rendered]
        assert not leaked, f"{name} leaks {leaked} to a non-team child"
```

If the existing lint's `_MARKERS` check flags `_TEAMS_MAIN_BLOCK` (it contains `"type":"answer"`), that is expected and passes only because the text sits inside `<<main>>` — that is exactly what the lint verifies; do not move the answer example outside the region.

`tests/test_prompt_goldens_teams.py`:

```python
"""Team prompt text (spec v2 §7.5): changes to it must be deliberate."""
import json
from pathlib import Path

from agentd.chat.controller_prompts import format_controller_system_prompt
from agentd.prompting.tagged import RenderContext
from agentd.subagents.context import AgentContext
from agentd.subagents.definitions import BUILTIN_AGENTS
from agentd.teams.tools import MainTeamOps, MainTeamToolSource

GOLDEN = Path(__file__).parent / "goldens" / "controller_prompt_teams.json"
BRIEF = "Team 'auth'. Goal: Add login\nRoster:\n- alice (you): general-purpose — edits\n- bob: explore — reads"


async def _never(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise AssertionError("not called")


def _live() -> dict[str, str]:
    main_tools = [d.model_dump() for d in MainTeamToolSource(BUILTIN_AGENTS, MainTeamOps(
        create=_never, resolve=lambda r: r, post=lambda *a: {}, status=lambda t: {},
        disband=_never), first_turn_team_ids=set()).definitions()]
    agent = AgentContext(agent_id="agent-1", name="general-purpose", label="alice", depth=1,
                         parent_agent_id=None, permission="default",
                         allowed_types=("tool_call", "edit", "progress", "report"),
                         persona="", max_iters=10)
    member = RenderContext.for_agent(agent, tools=frozenset({"read_file", "team_post"}),
                                     shell_policy="ask", team_brief=BRIEF)
    return {
        "system/teams_main": format_controller_system_prompt(
            main_tools, task_subsystem_enabled=False, memory_enabled=False),
        "system/teams_member": format_controller_system_prompt(
            [{"name": "team_post"}], task_subsystem_enabled=False, memory_enabled=False,
            render_ctx=member, persona="You edit code."),
    }


def test_teams_text_matches_golden() -> None:
    assert _live() == json.loads(GOLDEN.read_text(encoding="utf-8"))


if __name__ == "__main__":  # python -m tests.test_prompt_goldens_teams  → re-capture
    GOLDEN.write_text(json.dumps(_live(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

`AgentContext` (`agentd/subagents/context.py`) requires `persona` and `max_iters` besides the identity fields; the tests pass both.

Capture: `.venv/bin/python -m tests.test_prompt_goldens_teams`. Read the captured JSON once — the member prompt must show the persona, then TEAM, the framing, the brief, the protocol, the examples; the main prompt the TEAMS block last.

- [ ] **Step 9: Run and commit**

Run: `.venv/bin/pytest tests/test_team_prompts.py tests/test_team_controller.py tests/test_prompt_goldens_teams.py tests/test_prompt_goldens_subagents.py tests/test_prompt_goldens.py tests/test_prompt_leak_lint.py tests/test_tagged_prompt.py tests/test_controller_prompts_tagged.py --color=no --timeout=120 > /tmp/p4t6.txt 2>&1; echo exit=$?; tail -5 /tmp/p4t6.txt`
Expected: `exit=0`. The two existing goldens must pass unchanged — if either moved, a tag leaked; fix the template, never re-capture an old golden.

```bash
git add agentd/prompting/tagged.py agentd/teams/service.py agentd/chat/controller_prompts.py agentd/chat/controller_loop.py agentd/chat/controller.py tests/test_team_prompts.py tests/test_team_controller.py tests/test_prompt_goldens_teams.py tests/goldens/controller_prompt_teams.json tests/test_prompt_leak_lint.py
git commit -m "feat(teams): member and main-agent team prompts, status tail, tool-as-type correction"
```

### Task 7: Routes, `/live` teams and the team channel

**Files:**
- Modify: `agentd/api/routes.py`, `agentd/chat/models.py` (`ThreadLiveState.teams`), `agentd/chat/controller.py` (`live_teams`, `team_detail`), `tests/test_get_routes_read_only.py`
- Test: `tests/test_team_routes.py`

**Interfaces:**
- Consumes: `TeamStore` (Task 1), `TeamService.summary` (Task 3), `disband_team`/`team_channel` (Task 5).
- Produces:
  - `GET /v1/chat/threads/{thread_id}/teams` → `{"teams": [summary, …]}` (oldest first; `summary` is `TeamService.summary` plus `created_at`)
  - `GET /v1/chat/threads/{thread_id}/teams/{team_id}` → `{**summary, "created_at", "posts": [TeamPost json, …], "last_seq": int}` — every post, direct messages included (the user sees them, §7.4); 404 for an unknown team or one in another thread
  - `POST /v1/chat/threads/{thread_id}/teams/{team_id}/disband` → `{"team_id", "phase"}`; 404 as above; `{"ok": false}`-style soft answer when teams are off (`{"team_id": …, "phase": null}`)
  - `ThreadLiveState.teams: list[dict] | None` — `ChatController.live_teams(thread_id)`: live teams only, each `{team_id, name, phase, round, max_rounds, paused_reason, members: [{label, agent_id, status}]}` (spec §9). No usage, no stances: those change on every model call and would churn the `/live` signature (Global Constraints). A member's status is its row's status, which changes only at activation boundaries.
  - Team channel events (Task 5 broadcasts `team_post`, `team_phase`): `team_post {seq, payload: {post}}`, `team_phase {payload: {phase, round, paused_reason}}`. `team_usage` is Phase 5: Phase 4 does not count requests, so no event would carry a real number.

- [ ] **Step 1: Write the failing tests** (`tests/test_team_routes.py`)

```python
"""Team routes and /live teams (spec v2 §9, §11.3 phase 4)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentd.api.routes import build_router
from agentd.chat.controller import ChatController
from agentd.chat.models import AgentRecord
from agentd.chat.storage import ChatThreadStore
from agentd.orchestrator.broadcaster import EventBroadcaster
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.storage.in_memory import InMemoryTaskStore
from agentd.teams.models import TeamMember, TeamRecord
from agentd.workspace.shadow import ShadowWorkspaceManager


def _client(tmp_path: Path, ctrl: ChatController) -> AsyncClient:
    app = FastAPI()
    app.include_router(build_router(
        store=InMemoryTaskStore(), orchestrator=None,
        workspace_manager=ShadowWorkspaceManager(root_path=tmp_path / "s"),
        retrieval_client=None, chat_agent=ctrl))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CRUCIBLE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")
    store = ChatThreadStore(tmp_path / "c.sqlite3")
    tid = store.create_thread(str(tmp_path), title="t").thread_id
    other = store.create_thread(str(tmp_path), title="o").thread_id
    now = datetime.now(UTC)
    store.teams.create_team(TeamRecord(
        team_id="team-1", thread_id=tid, name="auth", goal="Add login", max_rounds=3,
        round_started_at=now, approval_gate=False, budget=160, created_turn_id="turn-1",
        checkpoint_seq=0, created_at=now))
    for label in ("alice", "bob"):
        store.insert_agent(AgentRecord(
            agent_id=f"agent-{label}", thread_id=tid, turn_id="turn-1", depth=1,
            name="general-purpose", label=label, prompt="p", status="awaiting_peer",
            team_id="team-1"))
        store.teams.add_member(TeamMember(team_id="team-1", agent_id=f"agent-{label}",
                                          label=label))
    store.teams.append_post("team-1", author="main", kind="proposal", text="plan", round=0,
                            payload={"assignments": [], "shared_files": [], "supersedes": []})
    store.teams.append_post("team-1", author="alice", kind="post", text="dm", recipient="bob")
    ctrl = ChatController(
        workspace_path=str(tmp_path), reasoning_engine=ScriptedReasoningEngine(None, []),
        thread_store=store, orchestrator=None, broadcaster=EventBroadcaster(),
        retrieval_client=None)
    return store, ctrl, tid, other


@pytest.mark.asyncio
async def test_list_detail_live_and_disband(tmp_path: Path, monkeypatch) -> None:
    store, ctrl, tid, other = _seed(tmp_path, monkeypatch)
    async with _client(tmp_path, ctrl) as client:
        listed = (await client.get(f"/v1/chat/threads/{tid}/teams")).json()["teams"]
        detail = (await client.get(f"/v1/chat/threads/{tid}/teams/team-1")).json()
        foreign = await client.get(f"/v1/chat/threads/{other}/teams/team-1")
        missing_thread = await client.get("/v1/chat/threads/nope/teams")
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
        disbanded = (await client.post(f"/v1/chat/threads/{tid}/teams/team-1/disband")).json()
        live_after = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
    assert [t["team_id"] for t in listed] == ["team-1"]
    assert listed[0]["phase"] == "DELIBERATING" and "created_at" in listed[0]
    assert [p["text"] for p in detail["posts"]] == ["plan", "dm"]   # the user sees DMs
    assert detail["last_seq"] == 2
    assert foreign.status_code == 404 and missing_thread.status_code == 404
    assert live["teams"] == [{
        "team_id": "team-1", "name": "auth", "phase": "DELIBERATING", "round": 1,
        "max_rounds": 3, "paused_reason": None,
        "members": [{"label": "alice", "agent_id": "agent-alice", "status": "awaiting_peer"},
                    {"label": "bob", "agent_id": "agent-bob", "status": "awaiting_peer"}]}]
    assert disbanded == {"team_id": "team-1", "phase": "DISBANDED"}
    assert live_after["teams"] is None


@pytest.mark.asyncio
async def test_routes_soft_when_teams_off(tmp_path: Path, monkeypatch) -> None:
    store, ctrl, tid, _ = _seed(tmp_path, monkeypatch)
    monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "0")
    async with _client(tmp_path, ctrl) as client:
        listed = (await client.get(f"/v1/chat/threads/{tid}/teams")).json()
        live = (await client.get(f"/v1/chat/threads/{tid}/live")).json()
    # Rows written while the flag was on stay readable; /live stays quiet.
    assert [t["team_id"] for t in listed["teams"]] == ["team-1"]
    assert live["teams"] is None
```

The expected `/live` dict relies on `TeamRecord`'s defaults from Task 1: `phase="DELIBERATING"`, `round=1`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_team_routes.py --color=no --timeout=120 > /tmp/p4t7.txt 2>&1; echo exit=$?; tail -5 /tmp/p4t7.txt`
Expected: exit≠0 (404 on `/teams`).

- [ ] **Step 3: Controller read helpers** (`controller.py`, next to `live_agents`)

```python
    def live_teams(self, thread_id: str) -> list[dict[str, object]]:
        """Slow-changing fields only — this rides /live, whose dedup signature must not
        change on every model call (no usage or stances; a row status moves only at
        activation boundaries)."""
        if self._teams is None or not is_teams_enabled():
            return []
        out: list[dict[str, object]] = []
        for team in self._store.teams.list_teams(thread_id):
            if team.phase not in LIVE_TEAM_PHASES:
                continue
            out.append({
                "team_id": team.team_id, "name": team.name, "phase": team.phase,
                "round": team.round, "max_rounds": team.max_rounds,
                "paused_reason": team.paused_reason,
                "members": [{"label": m.label, "agent_id": m.agent_id,
                             "status": self._member_row_status(m.agent_id)}
                            for m in self._store.teams.members(team.team_id)]})
        return out

    def _member_row_status(self, agent_id: str) -> str:
        record = self._store.get_agent(agent_id)
        return record.status if record is not None else "unknown"

    def team_summaries(self, thread_id: str) -> list[dict[str, object]]:
        if self._teams is None:
            return []
        return [{**self._teams.summary(t.team_id), "created_at": t.created_at.isoformat()}
                for t in self._store.teams.list_teams(thread_id)]

    def team_detail(self, thread_id: str, team_id: str) -> dict[str, object] | None:
        team = self._store.teams.get_team(team_id)
        if self._teams is None or team is None or team.thread_id != thread_id:
            return None
        posts = self._store.teams.posts(team_id)   # viewer=None: the user sees every post
        return {**self._teams.summary(team_id), "created_at": team.created_at.isoformat(),
                "posts": [p.model_dump(mode="json") for p in posts],
                "last_seq": max((p.seq for p in posts), default=0)}
```

`list_teams` returns oldest first (Task 1's `ORDER BY created_at`); `_resolve_team` relies on that when it iterates `reversed(...)`.

- [ ] **Step 4: Routes** (`routes.py`, after `post_stop_agent`)

```python
        # ── Teams (spec v2 §9) ───────────────────────────────────────────────
        # Read routes stay readable with the flag off (rows written while it was on);
        # /live only reports live teams when it is on (ChatController.live_teams).
        @router.get("/chat/threads/{thread_id}/teams")
        async def list_thread_teams(thread_id: str) -> dict:
            if _chat_agent._store.get_thread(thread_id) is None:
                raise HTTPException(status_code=404, detail="Thread not found")
            summaries = getattr(_chat_agent, "team_summaries", None)
            return {"teams": summaries(thread_id) if summaries is not None else []}

        @router.get("/chat/threads/{thread_id}/teams/{team_id}")
        async def get_thread_team(thread_id: str, team_id: str) -> dict:
            detail = getattr(_chat_agent, "team_detail", None)
            found = detail(thread_id, team_id) if detail is not None else None
            if found is None:
                raise HTTPException(status_code=404, detail="Team not found")
            return found

        @router.post("/chat/threads/{thread_id}/teams/{team_id}/disband")
        async def post_disband_team(thread_id: str, team_id: str) -> dict:
            from agentd.teams.validation import TeamInputError

            disband = getattr(_chat_agent, "disband_team", None)
            if disband is None:
                return {"team_id": team_id, "phase": None}
            try:
                return await disband(thread_id, team_id)  # type: ignore[misc]
            except TeamInputError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
```

Move the `TeamInputError` import to the top of `routes.py` with the other `agentd` imports (CLAUDE.md: imports at the top) — it is shown inline above only to make the dependency obvious.

In `get_thread_live`, after the `agents` block:

```python
            _live_teams = getattr(_chat_agent, "live_teams", None)
            if _live_teams is not None:
                live.teams = _live_teams(thread_id) or None
```

`ThreadLiveState` (`chat/models.py`), after `agents_running`:

```python
    # Live teams in the thread (spec v2 §9): slow-changing fields only, so the /live dedup
    # signature does not churn. None when there are none (or teams are off).
    teams: list[dict[str, Any]] | None = None
```

- [ ] **Step 5: The GET allowlist**

Add to `REVIEWED_READ_ONLY_GET_ROUTES` in `tests/test_get_routes_read_only.py`:

```python
    ("api/routes.py", "/chat/threads/{thread_id}/teams"),
    ("api/routes.py", "/chat/threads/{thread_id}/teams/{team_id}"),
```

Both are read-only: `team_summaries`/`team_detail` only `SELECT` (`TeamService.summary` reads the store and `_agent_info`, which reads rows and the in-memory registry).

- [ ] **Step 6: Run and commit**

Run: `.venv/bin/pytest tests/test_team_routes.py tests/test_subagent_routes.py tests/test_get_routes_read_only.py tests/test_team_controller.py --color=no --timeout=120 > /tmp/p4t7.txt 2>&1; echo exit=$?; tail -5 /tmp/p4t7.txt`
Expected: `exit=0`.

```bash
git add agentd/api/routes.py agentd/chat/models.py agentd/chat/controller.py tests/test_team_routes.py tests/test_get_routes_read_only.py
git commit -m "feat(teams): team routes, /live teams and the disband route"
```

---

# Part C — editor-client (`apps/editor-client`)

### Task 8: editor-client — team schemas, stream events, methods, `/live` teams

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts`, `apps/editor-client/src/client/http-backend-client.ts`
- Test: `apps/editor-client/test/team-contracts.test.ts`

**Interfaces:**
- Consumes: Task 7's routes and `/live` `teams`; Task 1's `/v1/config` `teams_enabled`.
- Produces (all exported from the package index via `task-contracts.ts` / `http-backend-client.ts`):
  - `TeamPostSchema`/`TeamPost`: `{teamId, seq, author, kind, recipient: string|null, text, mentions: string[], refId: string|null, round: number|null, payload: Record<string, unknown>, closed: string|null, createdAt}`
  - `TeamSummarySchema`/`TeamSummary`: `{teamId, name, goal, phase, round, maxRounds, pausedReason: string|null, members: {label, agentId, status}[], openProposals: {id, author, text, stances: Record<string,string>}[], usage: {requests, budget}, createdAt}`
  - `TeamDetailSchema`/`TeamDetail` = summary + `{posts: TeamPost[], lastSeq}`
  - `TeamLiveSchema`/`TeamLive`: `{teamId, name, phase, round, maxRounds, pausedReason, members: {label, agentId, status}[]}`; `ThreadLiveState.teams: TeamLive[] | null`
  - `BackendConfig.teamsEnabled: boolean` (default false)
  - `StreamEvent` gains `{type: "team_post"; payload: {post: Record<string, unknown>}}` and `{type: "team_phase"; payload: {phase: string; round: number; paused_reason: string | null}}` (team-channel events; `seq` rides `SequencedStreamEvent`)
  - `BackendTaskClient.listTeams(threadId): Promise<TeamSummary[]>`, `getTeam(threadId, teamId): Promise<TeamDetail>`, `disbandTeam(threadId, teamId): Promise<{phase: string | null}>`
  - `parseWireTeamPost(raw: Record<string, unknown>): TeamPost` — the host maps a `team_post` event's snake_case post with it

- [ ] **Step 1: Write the failing test** (`test/team-contracts.test.ts`)

```ts
import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient, parseWireTeamPost } from "../src/client/http-backend-client";

function respond(body: unknown) {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => body });
}

const POST = {
  team_id: "team-1", seq: 3, author: "alice", kind: "object", recipient: null,
  text: "misses a caller", mentions: ["bob"], ref_id: "P1", round: 1,
  payload: { evidence: { files: ["api/a.py"], line: 4 } }, closed: null,
  created_at: "2026-10-05T00:00:00Z",
};
const SUMMARY = {
  team_id: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  max_rounds: 3, paused_reason: null,
  members: [{ label: "alice", agent_id: "agent-a", status: "running" }],
  open_proposals: [{ id: "P1", author: "main", text: "plan", stances: { alice: "object" } }],
  usage: { requests: 0, budget: 160 }, created_at: "2026-10-05T00:00:00Z",
};

describe("team contracts", () => {
  it("lists, gets and disbands teams with camelCase mapping", async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ teams: [SUMMARY] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        ...SUMMARY, posts: [POST], last_seq: 3 }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        team_id: "team-1", phase: "DISBANDED" }) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const [team] = await c.listTeams("t");
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/chat/threads/t/teams");
    expect(team).toMatchObject({ teamId: "team-1", maxRounds: 3, pausedReason: null,
      members: [{ label: "alice", agentId: "agent-a", status: "running" }],
      openProposals: [{ id: "P1", stances: { alice: "object" } }] });
    const detail = await c.getTeam("t", "team-1");
    expect(detail.lastSeq).toBe(3);
    expect(detail.posts[0]).toMatchObject({ refId: "P1", mentions: ["bob"], recipient: null,
      payload: { evidence: { files: ["api/a.py"], line: 4 } } });
    expect(await c.disbandTeam("t", "team-1")).toEqual({ phase: "DISBANDED" });
    expect(fetchFn.mock.calls[2][0]).toBe("http://x/v1/chat/threads/t/teams/team-1/disband");
    expect(fetchFn.mock.calls[2][1]).toMatchObject({ method: "POST" });
  });

  it("parses a wire post and maps /live teams and the config flag", async () => {
    expect(parseWireTeamPost(POST)).toMatchObject({ teamId: "team-1", seq: 3, refId: "P1" });
    const live = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      active_task_id: null, status: null, pending_gates: [], plan: null, turn_active: false,
      teams: [{ team_id: "team-1", name: "auth", phase: "DELIBERATING", round: 1,
                max_rounds: 3, paused_reason: null,
                members: [{ label: "alice", agent_id: "agent-a", status: "running" }] }],
    }) });
    const state = await live.getThreadLiveState("t");
    expect(state.teams?.[0]).toEqual({ teamId: "team-1", name: "auth", phase: "DELIBERATING",
      round: 1, maxRounds: 3, pausedReason: null,
      members: [{ label: "alice", agentId: "agent-a", status: "running" }] });
    const none = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      active_task_id: null, status: null, pending_gates: [], plan: null }) });
    expect((await none.getThreadLiveState("t")).teams).toBeNull();
    const cfg = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      task_subsystem_enabled: false, chat_controller_enabled: true, memory_enabled: false,
      skills_enabled: false, mcp_enabled: false, subagents_enabled: true,
      teams_enabled: true, provider: null }) });
    expect((await cfg.getConfig()).teamsEnabled).toBe(true);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (from `apps/editor-client`): `npx vitest run test/team-contracts.test.ts > /tmp/p4t8.txt 2>&1; echo exit=$?; tail -15 /tmp/p4t8.txt`
Expected: exit≠0 (`parseWireTeamPost` is not exported).

- [ ] **Step 3: Schemas and events** (`task-contracts.ts`)

After `AgentDetailSchema`:

```ts
// Agent teams (spec v2 §7, §9). A post's author and kind come from columns, never from
// its text — the UI renders them from these fields only (§3.10).
export const TeamPostSchema = z.object({
  teamId: z.string(),
  seq: z.number(),
  author: z.string(),
  kind: z.string(),
  recipient: z.string().nullable(),
  text: z.string(),
  mentions: z.array(z.string()).default([]),
  refId: z.string().nullable(),
  round: z.number().nullable(),
  payload: z.record(z.unknown()).default({}),
  closed: z.string().nullable(),
  createdAt: z.string(),
});
export type TeamPost = z.infer<typeof TeamPostSchema>;

export const TeamSummarySchema = z.object({
  teamId: z.string(),
  name: z.string(),
  goal: z.string(),
  phase: z.string(),
  round: z.number(),
  maxRounds: z.number(),
  pausedReason: z.string().nullable(),
  members: z.array(z.object({ label: z.string(), agentId: z.string(), status: z.string() })),
  openProposals: z.array(z.object({
    id: z.string(), author: z.string(), text: z.string(),
    stances: z.record(z.string(), z.string()),
  })),
  usage: z.object({ requests: z.number(), budget: z.number() }),
  createdAt: z.string(),
});
export type TeamSummary = z.infer<typeof TeamSummarySchema>;

export const TeamDetailSchema = TeamSummarySchema.extend({
  posts: z.array(TeamPostSchema),
  lastSeq: z.number(),
});
export type TeamDetail = z.infer<typeof TeamDetailSchema>;

// /live carries only slow-changing team fields (the dedup signature must not churn).
export const TeamLiveSchema = z.object({
  teamId: z.string(),
  name: z.string(),
  phase: z.string(),
  round: z.number(),
  maxRounds: z.number(),
  pausedReason: z.string().nullable(),
  members: z.array(z.object({ label: z.string(), agentId: z.string(), status: z.string() })),
});
export type TeamLive = z.infer<typeof TeamLiveSchema>;
```

`TeamSummarySchema` must be declared before `ThreadLiveStateSchema`, which then gains (after `agentsRunning`):

```ts
  // Live teams (spec v2 §9). Null when there are none.
  teams: z.array(TeamLiveSchema).nullable().default(null),
```

`StreamEvent`, before `retry_status`:

```ts
  // Team channel (chat:{thread}:team:{team}); seq = the post's seq.
  | { type: "team_post"; payload: { post: Record<string, unknown> } }
  | { type: "team_phase"; payload: { phase: string; round: number; paused_reason: string | null } }
```

`BackendConfigSchema`: `teamsEnabled: z.boolean().default(false),` after `subagentsEnabled`.

`BackendTaskClient`, after `stopAgent`:

```ts
  // Agent teams (spec v2 §9).
  listTeams(threadId: string): Promise<TeamSummary[]>;
  getTeam(threadId: string, teamId: string): Promise<TeamDetail>;
  disbandTeam(threadId: string, teamId: string): Promise<{ phase: string | null }>;
```

- [ ] **Step 4: Client** (`http-backend-client.ts`)

Import the new schemas/types next to `AgentSummarySchema`. After `stopAgent`:

```ts
  async listTeams(threadId: string): Promise<TeamSummary[]> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/teams`
    ) as Record<string, unknown>;
    const teams = Array.isArray(raw["teams"]) ? raw["teams"] as Record<string, unknown>[] : [];
    return teams.map((t) => TeamSummarySchema.parse(HttpBackendClient.toTeamSummary(t)));
  }

  async getTeam(threadId: string, teamId: string): Promise<TeamDetail> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/teams/${encodeURIComponent(teamId)}`
    ) as Record<string, unknown>;
    const posts = Array.isArray(raw["posts"]) ? raw["posts"] as Record<string, unknown>[] : [];
    return TeamDetailSchema.parse({
      ...HttpBackendClient.toTeamSummary(raw),
      posts: posts.map((p) => HttpBackendClient.toTeamPost(p)),
      lastSeq: raw["last_seq"] ?? 0,
    });
  }

  async disbandTeam(threadId: string, teamId: string): Promise<{ phase: string | null }> {
    const raw = await this.fetchJson(
      `/v1/chat/threads/${encodeURIComponent(threadId)}/teams/${encodeURIComponent(teamId)}/disband`,
      { method: "POST", body: "{}" }
    ) as Record<string, unknown>;
    return { phase: typeof raw["phase"] === "string" ? raw["phase"] : null };
  }
```

Next to `toAgentSummary`:

```ts
  static toTeamPost(p: Record<string, unknown>): Record<string, unknown> {
    return {
      teamId: p["team_id"], seq: p["seq"], author: p["author"], kind: p["kind"],
      recipient: p["recipient"] ?? null, text: p["text"] ?? "", mentions: p["mentions"] ?? [],
      refId: p["ref_id"] ?? null, round: p["round"] ?? null, payload: p["payload"] ?? {},
      closed: p["closed"] ?? null, createdAt: p["created_at"],
    };
  }

  private static toTeamCore(t: Record<string, unknown>): Record<string, unknown> {
    return {
      teamId: t["team_id"], name: t["name"], phase: t["phase"], round: t["round"],
      maxRounds: t["max_rounds"], pausedReason: t["paused_reason"] ?? null,
    };
  }

  private static toTeamSummary(t: Record<string, unknown>): Record<string, unknown> {
    const members = Array.isArray(t["members"]) ? t["members"] as Record<string, unknown>[] : [];
    const proposals = Array.isArray(t["open_proposals"])
      ? t["open_proposals"] as Record<string, unknown>[] : [];
    return {
      ...HttpBackendClient.toTeamCore(t),
      goal: t["goal"] ?? "",
      members: members.map((m) => ({ label: m["label"], agentId: m["agent_id"],
                                     status: m["status"] ?? "" })),
      // Proposal keys (id/author/text) are camel-safe; stances are label → stance.
      openProposals: proposals,
      usage: t["usage"] ?? { requests: 0, budget: 0 },
      createdAt: t["created_at"] ?? "",
    };
  }

  private static toTeamLive(t: Record<string, unknown>): Record<string, unknown> {
    const members = Array.isArray(t["members"]) ? t["members"] as Record<string, unknown>[] : [];
    return {
      ...HttpBackendClient.toTeamCore(t),
      members: members.map((m) => ({ label: m["label"], agentId: m["agent_id"],
                                     status: m["status"] ?? "" })),
    };
  }
```

(`toTeamPost` is public `static`, like `toChatMessage`, so the module-level `parseWireTeamPost` can reach it.)

In `getThreadLiveState`, after `agentsRunning`:

```ts
      teams: Array.isArray(raw["teams"])
        ? (raw["teams"] as Record<string, unknown>[]).map((t) => HttpBackendClient.toTeamLive(t))
        : null,
```

In `getConfig`: `teamsEnabled: raw["teams_enabled"] ?? false,` after `subagentsEnabled`.

At the bottom, next to `parseWireChatMessage`:

```ts
export function parseWireTeamPost(raw: Record<string, unknown>): TeamPost {
  return TeamPostSchema.parse(HttpBackendClient.toTeamPost(raw));
}
```

- [ ] **Step 5: Run, build and commit**

Run (from `apps/editor-client`): `npx vitest run > /tmp/p4t8.txt 2>&1; echo exit=$?; tail -6 /tmp/p4t8.txt` then `npm run build > /tmp/p4t8b.txt 2>&1; echo exit=$?` (the extension types off `dist/` — CLAUDE.md build order) and from the repo root `npm run -w crucible-vscode-extension typecheck > /tmp/p4t8c.txt 2>&1; echo exit=$?`.
Expected: three `exit=0`.

```bash
git add apps/editor-client/src/contracts/task-contracts.ts apps/editor-client/src/client/http-backend-client.ts apps/editor-client/test/team-contracts.test.ts
git commit -m "feat(editor-client): team schemas, stream events and team methods"
```

---

# Part D — Extension (`apps/vscode-extension`)

### Task 9: Host — `TeamViewManager`, controller wiring, panel routing

**Files:**
- Create: `apps/vscode-extension/src/team-views.ts`, `apps/vscode-extension/test/team-views.test.ts`
- Modify: `apps/vscode-extension/src/controller.ts`, `apps/vscode-extension/src/chat-panel.ts`, `apps/vscode-extension/src/extension.ts`, `apps/vscode-extension/test/controller.test.ts`

**Interfaces:**
- Consumes: Task 8 (`listTeams`, `getTeam`, `disbandTeam`, `parseWireTeamPost`, `TeamSummary`, `TeamDetail`, `TeamLive`, `TeamPost`, `ThreadLiveState.teams`).
- Produces:
  - `team-views.ts`: `TERMINAL_TEAM_PHASES` (`DONE`, `DISBANDED`, `FAILED`), `teamChannel(threadId, teamId)`, `type TeamViewEvent = {type: "team_post"; post: TeamPost} | {type: "team_phase"; phase: string; round: number; pausedReason: string | null}`, `interface TeamViewSink {detail(teamId, detail: TeamDetail); event(teamId, event: TeamViewEvent)}`, `class TeamViewManager` (`setOpen(threadId, teamIds)`, `closeAll()`)
  - `ControllerUI` gains `renderTeams(teams: TeamSummary[])`, `renderLiveTeams(teams: TeamLive[])`, `teamDetail(teamId, detail: TeamDetail)`, `teamEvent(teamId, event: TeamViewEvent)`
  - `CrucibleController.setOpenTeams(teamIds: string[])`, `CrucibleController.disbandTeam(teamId: string): Promise<void>`
  - Webview → host: `setOpenTeams {teamIds}`, `disbandTeam {teamId}`. Host → webview: `renderTeams {teams}`, `renderLiveTeams {teams}`, `teamDetail {teamId, detail}`, `teamEvent {teamId, event}`.

When summaries load (spec §9): `listTeams` on thread open/switch and on the `turnActive` true→false edge, plus whenever a team **leaves** `/live` `teams` (it ended — `/live` lists live teams only, so without this refresh the card would keep showing its last live phase). `/live` `teams` renders on every changed poll.

- [ ] **Step 1: Write the failing tests** (`test/team-views.test.ts`)

```ts
import type { TeamDetail, SequencedStreamEvent } from "@crucible/editor-client";
import { describe, expect, test } from "vitest";

import { TeamViewManager, teamChannel, type TeamViewEvent } from "../src/team-views.js";

function detail(phase: string, lastSeq: number): TeamDetail {
  return {
    teamId: "team-1", name: "auth", goal: "g", phase, round: 1, maxRounds: 3,
    pausedReason: null, members: [], openProposals: [],
    usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z",
    posts: [], lastSeq,
  };
}

const post = (seq: number) => ({
  team_id: "team-1", seq, author: "alice", kind: "post", recipient: null, text: `p${seq}`,
  mentions: [], ref_id: null, round: 1, payload: {}, closed: null,
  created_at: "2026-10-05T00:00:00Z",
});

function channel(events: SequencedStreamEvent[], hold: boolean) {
  return async function* (_id: string, signal?: AbortSignal) {
    for (const e of events) yield e;
    if (hold) {
      await new Promise<void>((_, reject) => signal?.addEventListener(
        "abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }))));
    }
  };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("TeamViewManager", () => {
  test("backfill then follow skips seq <= lastSeq", async () => {
    const seen: Array<string | number> = [];
    const subscribed: string[] = [];
    const client = {
      getTeam: async () => detail("DELIBERATING", 4),
      streamChannel: (id: string, signal?: AbortSignal) => {
        subscribed.push(id);
        return channel([
          { type: "team_post", payload: { post: post(4) }, seq: 4 },
          { type: "team_post", payload: { post: post(5) }, seq: 5 },
        ] as SequencedStreamEvent[], true)(id, signal);
      },
    };
    const m = new TeamViewManager(() => client, {
      detail: (_id, d) => seen.push(`detail:${d.lastSeq}`),
      event: (_id, e: TeamViewEvent) => seen.push(e.type === "team_post" ? e.post.seq : e.phase),
    }, 0);
    m.setOpen("t", ["team-1"]);
    await flush(); await flush();
    expect(subscribed).toEqual([teamChannel("t", "team-1")]);
    expect(seen).toEqual(["detail:4", 5]);   // the post the backfill had is not repeated
    m.closeAll();
  });

  test("a terminal phase event ends the follow with one final backfill", async () => {
    let backfills = 0;
    const client = {
      getTeam: async () => detail(backfills++ === 0 ? "DELIBERATING" : "DISBANDED", 0),
      streamChannel: channel([{ type: "team_phase", payload: {
        phase: "DISBANDED", round: 1, paused_reason: null } } as SequencedStreamEvent], true),
    };
    const phases: string[] = [];
    const m = new TeamViewManager(() => client, {
      detail: (_id, d) => phases.push(d.phase), event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    for (let i = 0; i < 6; i++) await flush();
    expect(phases).toEqual(["DELIBERATING", "DISBANDED"]);
    expect(backfills).toBe(2);
    m.closeAll();
  });

  test("a terminal team is backfilled once and never subscribed", async () => {
    const subscribed: string[] = [];
    const client = {
      getTeam: async () => detail("DONE", 7),
      streamChannel: (id: string) => { subscribed.push(id); return channel([], false)(id); },
    };
    const m = new TeamViewManager(() => client, { detail: () => {}, event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    await flush(); await flush();
    expect(subscribed).toEqual([]);
  });

  test("an idle channel re-backfills while the team is live; closing stops it", async () => {
    let backfills = 0;
    const client = {
      getTeam: async () => { backfills++; return detail("DELIBERATING", 0); },
      streamChannel: channel([], false),   // ends at once, like the idle timeout
    };
    const m = new TeamViewManager(() => client, { detail: () => {}, event: () => {} }, 0);
    m.setOpen("t", ["team-1"]);
    for (let i = 0; i < 6; i++) await flush();
    m.setOpen("t", []);
    const after = backfills;
    for (let i = 0; i < 4; i++) await flush();
    expect(after).toBeGreaterThan(1);
    expect(backfills).toBe(after);
  });
});
```

Add to `test/controller.test.ts` (inside the sub-agent `describe` that defines `setup`, so it reuses `setup`, `NULL_LIVE_STATE` and `createUi`; extend `createUi`'s defaults with `renderTeams: () => {}, renderLiveTeams: () => {}, teamDetail: () => {}, teamEvent: () => {},`):

```ts
  test("team summaries load on open, /live teams are in the signature, and a team leaving /live refreshes", async () => {
    const TEAM = { teamId: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 1,
      maxRounds: 3, pausedReason: null, members: [], openProposals: [],
      usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z" };
    const LIVE = { teamId: "team-1", name: "auth", phase: "DELIBERATING", round: 1,
      maxRounds: 3, pausedReason: null, members: [] };
    const teamLists: string[] = [];
    const summaries: unknown[][] = [];
    const lives: unknown[][] = [];
    const { state, controller } = setup({
      listTeams: async (threadId: string) => { teamLists.push(threadId); return [TEAM]; },
    }, {
      renderTeams: (t) => { summaries.push(t); },
      renderLiveTeams: (t) => { lives.push(t); },
    });
    await controller.switchChatThread("chat-1");
    await Promise.resolve(); await Promise.resolve();
    controller.dispose();
    expect(teamLists).toEqual(["chat-1"]);
    expect(summaries).toHaveLength(1);

    state.liveResponse = { ...NULL_LIVE_STATE, teams: [LIVE] };
    await controller.pollThreadLiveState();
    await controller.pollThreadLiveState();
    expect(lives).toHaveLength(1);                       // unchanged → deduped
    state.liveResponse = { ...NULL_LIVE_STATE, teams: [{ ...LIVE, round: 2 }] };
    await controller.pollThreadLiveState();
    expect(lives).toHaveLength(2);                       // teams ARE in the signature

    teamLists.length = 0;
    state.liveResponse = NULL_LIVE_STATE;                // the team ended
    await controller.pollThreadLiveState();
    await Promise.resolve(); await Promise.resolve();
    expect(teamLists).toEqual(["chat-1"]);
  });
```

`setup` gains a second parameter for UI overrides: change its signature to `function setup(extra: Record<string, unknown> = {}, uiExtra: Partial<ControllerUI> = {})` and spread `...uiExtra` last into its `createUi({...})` call. Existing callers pass one argument and are unaffected.

- [ ] **Step 2: Run to verify failure**

Run (from `apps/vscode-extension`): `npx vitest run test/team-views.test.ts test/controller.test.ts > /tmp/p4t9.txt 2>&1; echo exit=$?; tail -15 /tmp/p4t9.txt`
Expected: exit≠0 (`../src/team-views.js` does not exist).

- [ ] **Step 3: `src/team-views.ts`**

```ts
import {
  parseWireTeamPost,
  type BackendTaskClient,
  type SequencedStreamEvent,
  type TeamDetail,
  type TeamPost,
} from "@crucible/editor-client";

// Phases a team never leaves (spec v2 §8.2): a final backfill, then no follow.
export const TERMINAL_TEAM_PHASES: ReadonlySet<string> = new Set(["DONE", "DISBANDED", "FAILED"]);

export function teamChannel(threadId: string, teamId: string): string {
  return `chat:${threadId}:team:${teamId}`;
}

export type TeamViewEvent =
  | { type: "team_post"; post: TeamPost }
  | { type: "team_phase"; phase: string; round: number; pausedReason: string | null };

export interface TeamViewSink {
  detail(teamId: string, detail: TeamDetail): void;
  event(teamId: string, event: TeamViewEvent): void;
}

type TeamClient = Pick<BackendTaskClient, "getTeam" | "streamChannel">;

interface OpenTeam {
  threadId: string;
  closed: boolean;
  abort: AbortController | null;
}

/**
 * Live data for the teams the user has open (spec v2 §9), modelled on AgentViewManager:
 * backfill with getTeam, follow the team channel skipping seq <= lastSeq, re-backfill when
 * the channel idles out. A terminal phase (event or backfill) ends the follow after one
 * final backfill. Member tabs use AgentViewManager, not this.
 */
export class TeamViewManager {
  private readonly views = new Map<string, OpenTeam>();

  constructor(
    private readonly client: () => TeamClient,
    private readonly sink: TeamViewSink,
    private readonly retryDelayMs = 1000,
  ) {}

  /** The full set of teams open in the webview; anything not in it is closed. */
  setOpen(threadId: string, teamIds: readonly string[]): void {
    const wanted = new Set(teamIds);
    for (const [id, view] of [...this.views]) {
      if (!wanted.has(id) || view.threadId !== threadId) this.close(id);
    }
    for (const id of wanted) {
      if (this.views.has(id)) continue;
      const view: OpenTeam = { threadId, closed: false, abort: null };
      this.views.set(id, view);
      void this.run(id, view);
    }
  }

  closeAll(): void {
    for (const id of [...this.views.keys()]) this.close(id);
  }

  private close(teamId: string): void {
    const view = this.views.get(teamId);
    if (!view) return;
    view.closed = true;
    view.abort?.abort();
    this.views.delete(teamId);
  }

  private async run(teamId: string, view: OpenTeam): Promise<void> {
    while (!view.closed) {
      let detail: TeamDetail;
      try {
        detail = await this.client().getTeam(view.threadId, teamId);
      } catch {
        await delay(this.retryDelayMs);
        continue;
      }
      if (view.closed) return;
      this.sink.detail(teamId, detail);
      if (TERMINAL_TEAM_PHASES.has(detail.phase)) return;
      let lastSeq = detail.lastSeq;
      view.abort = new AbortController();
      try {
        const stream = this.client().streamChannel(
          teamChannel(view.threadId, teamId), view.abort.signal);
        for await (const raw of stream) {
          if (view.closed) return;
          const event = toViewEvent(raw);
          if (event === null) continue;
          if (event.type === "team_post") {
            if (event.post.seq <= lastSeq) continue;
            lastSeq = event.post.seq;
          }
          this.sink.event(teamId, event);
          // The final backfill carries the closing system post and the ended state.
          if (event.type === "team_phase" && TERMINAL_TEAM_PHASES.has(event.phase)) break;
        }
      } catch {
        // Aborted (closed) or the connection dropped: re-backfill.
      } finally {
        view.abort = null;
      }
    }
  }
}

function toViewEvent(event: SequencedStreamEvent): TeamViewEvent | null {
  if (event.type === "team_post") {
    return { type: "team_post", post: parseWireTeamPost(event.payload.post) };
  }
  if (event.type === "team_phase") {
    return { type: "team_phase", phase: event.payload.phase, round: event.payload.round,
             pausedReason: event.payload.paused_reason };
  }
  return null;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
```

The terminal-phase test expects the loop to re-backfill after `break`: `break` leaves the `for await`, the `while` runs again, `getTeam` returns `DISBANDED`, and the terminal check returns. Breaking out of `for await` calls the generator's `return()`, which ends the stream cleanly.

- [ ] **Step 4: Controller** (`controller.ts`)

Imports: `TeamViewManager, type TeamViewEvent` from `./team-views.js`; `TeamDetail, TeamLive, TeamSummary` types from `@crucible/editor-client`.

`ControllerUI`, after `agentEvent`:

```ts
  // Agent teams (spec v2 §9): summaries, live slow fields, and the open teams' boards.
  renderTeams(teams: TeamSummary[]): void;
  renderLiveTeams(teams: TeamLive[]): void;
  teamDetail(teamId: string, detail: TeamDetail): void;
  teamEvent(teamId: string, event: TeamViewEvent): void;
```

Fields, after `agentViews`:

```ts
  private readonly teamViews = new TeamViewManager(() => this.clientForChat(), {
    detail: (teamId, detail) => this.ui.teamDetail(teamId, detail),
    event: (teamId, event) => this.ui.teamEvent(teamId, event),
  });
  // Live team ids from the last changed /live: one that drops out has ended (spec §9).
  private lastLiveTeamIds: ReadonlySet<string> = new Set();
```

Everywhere `this.agentViews.closeAll()` appears (new thread, switch thread, `dispose`), add `this.teamViews.closeAll();` and, in the two thread-change sites, `this.lastLiveTeamIds = new Set();`. Everywhere `void this.refreshAgentRoster(<id>)` appears on thread open/switch, add `void this.refreshTeams(<id>);` beside it.

Methods, after `setOpenAgents`:

```ts
  /** The webview's full set of open team windows. */
  setOpenTeams(teamIds: string[]): void {
    if (!this.activeThreadId) return;
    this.teamViews.setOpen(this.activeThreadId, teamIds);
  }

  async disbandTeam(teamId: string): Promise<void> {
    const threadId = this.activeThreadId;
    if (!threadId) return;
    try {
      await this.clientForChat().disbandTeam(threadId, teamId);
    } catch (error) {
      this.ui.showError(`Failed to disband team: ${formatError(error)}`);
      return;
    }
    this.lastLiveSignature = null;
    void this.pollThreadLiveState();
    void this.refreshTeams(threadId);
  }

  /** Team summaries from the routes (spec §9): thread open, turn end, a team ending. */
  private async refreshTeams(threadId: string): Promise<void> {
    let teams: TeamSummary[];
    try {
      teams = await this.clientForChat().listTeams(threadId);
    } catch {
      return; // transient, or a backend without teams
    }
    if (threadId !== this.activeThreadId) return;
    if (teams.length > 0) this.ui.renderTeams(teams);
  }
```

In `pollThreadLiveState`, add to the signature object after `agentsRunning`:

```ts
      // INVARIANT (CLAUDE.md /live dedup): team rows are consumed after this gate.
      teams: live.teams,
```

and after the agents block (before the turn-end edge):

```ts
    const liveTeamIds = new Set((live.teams ?? []).map((t) => t.teamId));
    if (live.teams && live.teams.length > 0) this.ui.renderLiveTeams(live.teams);
    // /live lists live teams only: one that left has ended — reload its summary.
    if ([...this.lastLiveTeamIds].some((id) => !liveTeamIds.has(id))) {
      void this.refreshTeams(threadId);
    }
    this.lastLiveTeamIds = liveTeamIds;
```

and on the turn-end edge line:

```ts
    if (this.lastTurnActive && !live.turnActive) {
      void this.refreshAgentRoster(threadId);
      void this.refreshTeams(threadId);
    }
```

- [ ] **Step 5: Panel and extension**

`chat-panel.ts` — two constructor parameters after `onStopAllAgents` (keep defaults so existing constructions compile):

```ts
    private readonly onStopAllAgents: () => Promise<void> = async () => {},
    private readonly onSetOpenTeams: (teamIds: string[]) => void = () => {},
    private readonly onDisbandTeam: (teamId: string) => Promise<void> = async () => {}
```

Message routing, after the `stopAllAgents` branch:

```ts
      } else if (m["type"] === "setOpenTeams") {
        const ids = Array.isArray(m["teamIds"])
          ? (m["teamIds"] as unknown[]).filter((x): x is string => typeof x === "string")
          : [];
        this.onSetOpenTeams(ids);
        return;
      } else if (m["type"] === "disbandTeam") {
        p = this.onDisbandTeam(String(m["teamId"] ?? ""));
```

Posting methods, after `agentEvent`:

```ts
  renderTeams(teams: TeamSummary[]): void {
    this.panel?.webview.postMessage({ type: "renderTeams", teams });
  }

  renderLiveTeams(teams: TeamLive[]): void {
    this.panel?.webview.postMessage({ type: "renderLiveTeams", teams });
  }

  teamDetail(teamId: string, detail: TeamDetail): void {
    this.panel?.webview.postMessage({ type: "teamDetail", teamId, detail });
  }

  teamEvent(teamId: string, event: TeamViewEvent): void {
    this.panel?.webview.postMessage({ type: "teamEvent", teamId, event });
  }
```

(Import the types; `TeamViewEvent` from `./team-views.js`.)

`extension.ts` — the `ChatPanel` construction gains, after `() => controller.stopAllAgents()`:

```ts
    () => controller.stopAllAgents(),
    (teamIds) => controller.setOpenTeams(teamIds),
    (teamId) => controller.disbandTeam(teamId)
```

and the `ui` object, after `agentEvent`:

```ts
    renderTeams: (teams) => {
      chatPanel.renderTeams(teams);
    },
    renderLiveTeams: (teams) => {
      chatPanel.renderLiveTeams(teams);
    },
    teamDetail: (teamId, detail) => {
      chatPanel.teamDetail(teamId, detail);
    },
    teamEvent: (teamId, event) => {
      chatPanel.teamEvent(teamId, event);
    },
```

- [ ] **Step 6: Run and commit**

Run (from `apps/vscode-extension`): `npx vitest run > /tmp/p4t9.txt 2>&1; echo exit=$?; tail -6 /tmp/p4t9.txt` and `npm run typecheck > /tmp/p4t9b.txt 2>&1; echo exit=$?`.
Expected: two `exit=0`.

```bash
git add apps/vscode-extension/src/team-views.ts apps/vscode-extension/src/controller.ts apps/vscode-extension/src/chat-panel.ts apps/vscode-extension/src/extension.ts apps/vscode-extension/test/team-views.test.ts apps/vscode-extension/test/controller.test.ts
git commit -m "feat(extension): team view manager, team summaries and /live teams"
```

### Task 10: Webview — team state, reducer and the team card

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/teams.ts`, `apps/vscode-extension/webview-ui/src/components/teams/TeamsContext.tsx`, `apps/vscode-extension/webview-ui/src/components/teams/TeamCard.tsx`, `apps/vscode-extension/webview-ui/src/test/teams.test.ts`, `apps/vscode-extension/webview-ui/src/test/teamCard.test.tsx`
- Modify: `webview-ui/src/types.ts`, `webview-ui/src/hooks/useAppState.ts`, `webview-ui/src/components/MessageRow.tsx`, `webview-ui/src/components/agents/AgentRosterCard.tsx` (export the row), `webview-ui/src/test/useAppState.test.ts`

**Interfaces:**
- Consumes: Task 9's host → webview messages (`renderTeams`, `renderLiveTeams`, `teamDetail`, `teamEvent`).
- Produces:
  - `types.ts`: `TeamPostView`, `TeamSummaryView`, `TeamLiveView`, `TeamDetailView`, `TeamEventView`, `TeamViewState {posts: TeamPostView[]; lastSeq: number}`; `AppState.teams: Record<string, TeamSummaryView>`, `AppState.teamViews: Record<string, TeamViewState>`; the four `ExtensionMessage` cases
  - `teams.ts`: `TERMINAL_TEAM_PHASES`, `isTerminalTeam(phase)`, `mergeLiveTeam(prev: TeamSummaryView | undefined, live: TeamLiveView): TeamSummaryView`, `viewFromTeamDetail(detail) -> TeamViewState`, `applyTeamEvent(view, event) -> TeamViewState`, `summaryWithEvent(team, event) -> TeamSummaryView`, `waitingOn(team, agents, now) -> {label: string; ms: number} | null`, `stanceOf(posts, proposalSeq, label) -> "agree" | "object" | null`
  - `TeamsContext` / `useTeamsUi()`: `{teams, views, openTeam(teamId: string): void}`
  - `<TeamCard teamId agentIds />` — rendered by `MessageRow` for `team_created`
  - `AgentRosterCard.tsx` exports `AgentRosterRow`

The card (spec §9): name, `phase · round n of m`, `waiting on <label> (Ns)` while a member runs (seconds from its `activationStartedAt`, ticked by `useNow` — never a server-ticked number), one roster row per member, the budget, the paused reason when there is one, and **Open board**. Phase 4 does not count requests (Task 7), so the card shows the budget alone (`budget 160 requests`) rather than a `0 / 160` that would read as real usage. The adopted proposal line and usage are Phase 5.

- [ ] **Step 1: Write the failing tests**

`src/test/teams.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  applyTeamEvent, isTerminalTeam, mergeLiveTeam, stanceOf, summaryWithEvent, viewFromTeamDetail,
  waitingOn,
} from "../teams";
import type { AgentSummaryView, TeamDetailView, TeamPostView, TeamSummaryView } from "../types";

const post = (seq: number, over: Partial<TeamPostView> = {}): TeamPostView => ({
  teamId: "team-1", seq, author: "alice", kind: "post", recipient: null, text: `p${seq}`,
  mentions: [], refId: null, round: 1, payload: {}, closed: null,
  createdAt: "2026-10-05T00:00:00Z", ...over,
});

const SUMMARY: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  maxRounds: 3, pausedReason: null,
  members: [{ label: "alice", agentId: "agent-a", status: "running" },
            { label: "bob", agentId: "agent-b", status: "awaiting_peer" }],
  openProposals: [], usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z",
};

describe("team state", () => {
  it("merges live fields over a summary, and builds one when none is loaded", () => {
    const merged = mergeLiveTeam(SUMMARY, { teamId: "team-1", name: "auth", phase: "DELIBERATING",
      round: 2, maxRounds: 3, pausedReason: null,
      members: [{ label: "alice", agentId: "agent-a", status: "completed" }] });
    expect(merged).toMatchObject({ round: 2, goal: "Add login", usage: { budget: 160 } });
    expect(merged.members[0].status).toBe("completed");
    const fresh = mergeLiveTeam(undefined, { teamId: "team-2", name: "x", phase: "DELIBERATING",
      round: 1, maxRounds: 4, pausedReason: null, members: [] });
    expect(fresh).toMatchObject({ teamId: "team-2", goal: "", openProposals: [] });
  });

  it("appends posts in seq order and drops a repeat", () => {
    const detail: TeamDetailView = { ...SUMMARY, posts: [post(1), post(2)], lastSeq: 2 };
    let view = viewFromTeamDetail(detail);
    view = applyTeamEvent(view, { type: "team_post", post: post(2) });
    view = applyTeamEvent(view, { type: "team_post", post: post(3) });
    expect(view.posts.map((p) => p.seq)).toEqual([1, 2, 3]);
    expect(view.lastSeq).toBe(3);
  });

  it("a phase event updates the summary", () => {
    const next = summaryWithEvent(SUMMARY, { type: "team_phase", phase: "DISBANDED",
      round: 1, pausedReason: null });
    expect(next.phase).toBe("DISBANDED");
    expect(isTerminalTeam(next.phase)).toBe(true);
    expect(isTerminalTeam("DELIBERATING")).toBe(false);
  });

  it("waiting on names the running member and how long it has run", () => {
    const agents: Record<string, AgentSummaryView> = {
      "agent-a": { agentId: "agent-a", parentAgentId: null, depth: 1, name: "general-purpose",
        label: "alice", status: "running", now: "", toolCount: 0, filesChangedCount: 0,
        startedAt: "2026-10-05T00:00:00Z", endedAt: null, reportPreview: "",
        activationStartedAt: "2026-10-05T00:00:10Z", activationEndedAt: null },
    };
    expect(waitingOn(SUMMARY, agents, Date.parse("2026-10-05T00:00:40Z")))
      .toEqual({ label: "alice", ms: 30_000 });
    expect(waitingOn({ ...SUMMARY, members: [SUMMARY.members[1]] }, agents, 0)).toBeNull();
  });

  it("a member's latest stance on a proposal wins", () => {
    const posts = [post(1, { kind: "proposal", author: "main" }),
      post(2, { kind: "object", refId: "P1" }), post(3, { kind: "agree", refId: "P1" }),
      post(4, { kind: "agree", refId: "P1", author: "bob" })];
    expect(stanceOf(posts, 1, "alice")).toBe("agree");
    expect(stanceOf(posts, 1, "carol")).toBeNull();
  });
});
```

`src/test/teamCard.test.tsx`:

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
  teamId: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  maxRounds: 3, pausedReason: null,
  members: [{ label: "alice", agentId: "agent-a", status: "running" },
            { label: "bob", agentId: "agent-b", status: "awaiting_peer" }],
  openProposals: [], usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z",
};

function agent(id: string, label: string, status: string): AgentSummaryView {
  return { agentId: id, parentAgentId: null, depth: 1, name: "general-purpose", label, status,
    now: "read_file api/a.py", toolCount: 2, filesChangedCount: 0,
    startedAt: "2026-10-05T00:00:00Z", endedAt: null, reportPreview: "",
    activationStartedAt: "2026-10-05T00:00:00Z", activationEndedAt: null };
}

function wrap(node: ReactNode, teams: Partial<TeamsUi> = {}) {
  const agentsUi: AgentsUi = {
    agents: { "agent-a": agent("agent-a", "alice", "running"),
              "agent-b": agent("agent-b", "bob", "awaiting_peer") },
    views: {}, expanded: new Set(), toggleExpanded: vi.fn(), openWindow: vi.fn(),
  };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM }, views: {}, openTeam: vi.fn(), ...teams };
  return { teamsUi, ...render(
    <AgentsContext.Provider value={agentsUi}>
      <TeamsContext.Provider value={teamsUi}>{node}</TeamsContext.Provider>
    </AgentsContext.Provider>) };
}

describe("TeamCard", () => {
  it("shows name, phase and round, the waiting line, member rows and the budget", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-a", "agent-b"]} />);
    expect(screen.getByText("auth")).toBeInTheDocument();
    expect(screen.getByText("deliberating · round 1 of 3")).toBeInTheDocument();
    expect(screen.getByText(/^waiting on alice/)).toBeInTheDocument();
    expect(screen.getByText("⏳ waiting on a teammate")).toBeInTheDocument();
    expect(screen.getByText("budget 160 requests")).toBeInTheDocument();
  });

  it("Open board opens the team window", () => {
    const { teamsUi } = wrap(<TeamCard teamId="team-1" agentIds={["agent-a", "agent-b"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Open board" }));
    expect(teamsUi.openTeam).toHaveBeenCalledWith("team-1");
  });

  it("shows a paused team's reason, and an ended team without the waiting line", () => {
    wrap(<TeamCard teamId="team-1" agentIds={["agent-a"]} />, {
      teams: { "team-1": { ...TEAM, phase: "DISBANDED", pausedReason: "budget reached" } } });
    expect(screen.getByText("disbanded · round 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("budget reached")).toBeInTheDocument();
    expect(screen.queryByText(/^waiting on/)).toBeNull();
  });

  it("MessageRow renders the card for a team_created message, before summaries load", () => {
    wrap(<MessageRow msg={{ role: "agent", content: "", type: "team_created",
      timestamp: "2026-10-05T00:00:00Z",
      metadata: { team_id: "team-9", name: "cache", agent_ids: ["agent-a"] } }} />, { teams: {} });
    expect(screen.getByText("cache")).toBeInTheDocument();   // from the message's metadata
  });
});
```

`MessageRow` needs only `msg`; its other props are optional.

Add to `src/test/useAppState.test.ts` (next to the sub-agents test):

```ts
  it("tracks team summaries, live merges, boards and clears them with the thread", () => {
    const { result } = renderHook(() => useAppState());
    const team = { teamId: "team-1", name: "auth", goal: "g", phase: "DELIBERATING", round: 1,
      maxRounds: 3, pausedReason: null, members: [], openProposals: [],
      usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z" };
    const post = { teamId: "team-1", seq: 1, author: "main", kind: "proposal", recipient: null,
      text: "plan", mentions: [], refId: null, round: 0, payload: {}, closed: null,
      createdAt: "2026-10-05T00:00:00Z" };
    act(() => { fireMessage({ type: "renderTeams", teams: [team] }); });
    act(() => { fireMessage({ type: "renderLiveTeams", teams: [{ teamId: "team-1",
      name: "auth", phase: "DELIBERATING", round: 2, maxRounds: 3, pausedReason: null,
      members: [] }] }); });
    expect(result.current.state.teams["team-1"]).toMatchObject({ round: 2, goal: "g" });
    act(() => { fireMessage({ type: "teamDetail", teamId: "team-1",
      detail: { ...team, posts: [post], lastSeq: 1 } }); });
    act(() => { fireMessage({ type: "teamEvent", teamId: "team-1",
      event: { type: "team_post", post: { ...post, seq: 2, kind: "post", text: "hi" } } }); });
    act(() => { fireMessage({ type: "teamEvent", teamId: "team-1",
      event: { type: "team_phase", phase: "DISBANDED", round: 2, pausedReason: null } }); });
    expect(result.current.state.teamViews["team-1"].posts.map((p) => p.seq)).toEqual([1, 2]);
    expect(result.current.state.teams["team-1"].phase).toBe("DISBANDED");
    act(() => { fireMessage({ type: "clearThread" }); });
    expect(result.current.state.teams).toEqual({});
    expect(result.current.state.teamViews).toEqual({});
  });
```

- [ ] **Step 2: Run to verify failure**

Run (from `apps/vscode-extension/webview-ui`): `npx vitest run src/test/teams.test.ts src/test/teamCard.test.tsx src/test/useAppState.test.ts > /tmp/p4t10.txt 2>&1; echo exit=$?; tail -15 /tmp/p4t10.txt`
Expected: exit≠0 (`../teams` does not exist). The webview has its own `node_modules`: run `npm install` in `webview-ui` once if vitest is missing.

- [ ] **Step 3: Types** (`types.ts`, after the sub-agent types)

```ts
// ── Agent teams (mirrors editor-client TeamPost/TeamSummary/TeamLive — camelCase) ─────
export interface TeamPostView {
  teamId: string;
  seq: number;
  author: string;
  kind: string;
  recipient: string | null;
  text: string;
  mentions: string[];
  refId: string | null;
  round: number | null;
  payload: Record<string, unknown>;
  closed: string | null;
  createdAt: string;
}

export interface TeamMemberView { label: string; agentId: string; status: string }

export interface TeamSummaryView {
  teamId: string;
  name: string;
  goal: string;
  phase: string;
  round: number;
  maxRounds: number;
  pausedReason: string | null;
  members: TeamMemberView[];
  openProposals: { id: string; author: string; text: string; stances: Record<string, string> }[];
  usage: { requests: number; budget: number };
  createdAt: string;
}

export interface TeamLiveView {
  teamId: string;
  name: string;
  phase: string;
  round: number;
  maxRounds: number;
  pausedReason: string | null;
  members: TeamMemberView[];
}

export interface TeamDetailView extends TeamSummaryView {
  posts: TeamPostView[];
  lastSeq: number;
}

/** A team-channel event as the host forwards it (already camelCase, Task 9). */
export type TeamEventView =
  | { type: "team_post"; post: TeamPostView }
  | { type: "team_phase"; phase: string; round: number; pausedReason: string | null };

/** An open team's board. */
export interface TeamViewState {
  posts: TeamPostView[];
  lastSeq: number;
}
```

`ExtensionMessage`, after `agentEvent`:

```ts
  | { type: "renderTeams"; teams: TeamSummaryView[] }
  | { type: "renderLiveTeams"; teams: TeamLiveView[] }
  | { type: "teamDetail"; teamId: string; detail: TeamDetailView }
  | { type: "teamEvent"; teamId: string; event: TeamEventView }
```

`WebviewMessage` gains `| { type: "setOpenTeams"; teamIds: string[] } | { type: "disbandTeam"; teamId: string }` (next to `setOpenAgents`).

`AppState`, after `agentViews`:

```ts
  // Team summaries (teamId → summary), from the routes and merged with /live.
  teams: Record<string, TeamSummaryView>;
  // Open teams' boards (backfill + live posts).
  teamViews: Record<string, TeamViewState>;
```

- [ ] **Step 4: `src/teams.ts`**

```ts
import type {
  AgentSummaryView, TeamDetailView, TeamEventView, TeamLiveView, TeamPostView, TeamSummaryView,
  TeamViewState,
} from "./types";

// Phases a team never leaves (spec v2 §8.2).
export const TERMINAL_TEAM_PHASES: ReadonlySet<string> = new Set(["DONE", "DISBANDED", "FAILED"]);

export function isTerminalTeam(phase: string): boolean {
  return TERMINAL_TEAM_PHASES.has(phase);
}

/** /live carries slow fields only: keep the loaded summary's goal, proposals and usage. */
export function mergeLiveTeam(prev: TeamSummaryView | undefined, live: TeamLiveView): TeamSummaryView {
  return {
    goal: "", openProposals: [], usage: { requests: 0, budget: 0 }, createdAt: "",
    ...prev,
    ...live,
  };
}

export function viewFromTeamDetail(detail: TeamDetailView): TeamViewState {
  return { posts: detail.posts, lastSeq: detail.lastSeq };
}

/** A post the backfill already holds (a live broadcast racing the backfill) is dropped. */
export function applyTeamEvent(view: TeamViewState, event: TeamEventView): TeamViewState {
  if (event.type !== "team_post" || event.post.seq <= view.lastSeq) return view;
  return { posts: [...view.posts, event.post], lastSeq: event.post.seq };
}

export function summaryWithEvent(team: TeamSummaryView, event: TeamEventView): TeamSummaryView {
  if (event.type !== "team_phase") return team;
  return { ...team, phase: event.phase, round: event.round, pausedReason: event.pausedReason };
}

/** The running member the team waits on, and for how long (spec §9) — the seconds come
 * from the member's activation start, ticked by the caller's useNow. */
export function waitingOn(
  team: TeamSummaryView, agents: Record<string, AgentSummaryView>, now: number,
): { label: string; ms: number } | null {
  for (const member of team.members) {
    const row = agents[member.agentId];
    const status = row?.status ?? member.status;
    if (status !== "running" && status !== "queued") continue;
    const started = row?.activationStartedAt ?? row?.startedAt;
    return { label: member.label, ms: started ? Math.max(0, now - Date.parse(started)) : 0 };
  }
  return null;
}

/** A member's current stance on proposal P<seq>: its latest agree/object wins. */
export function stanceOf(posts: TeamPostView[], proposalSeq: number, label: string): "agree" | "object" | null {
  let stance: "agree" | "object" | null = null;
  for (const p of posts) {
    if (p.author !== label || p.refId !== `P${proposalSeq}`) continue;
    if (p.kind === "agree" || p.kind === "object") stance = p.kind;
  }
  return stance;
}
```

- [ ] **Step 5: Reducer** (`useAppState.ts`)

Import `applyTeamEvent, mergeLiveTeam, summaryWithEvent, viewFromTeamDetail` from `../teams`. `INITIAL` and the `clearThread` case gain `teams: {}, teamViews: {},` next to `agents`/`agentViews`. Cases, after `agentEvent`:

```ts
    case "renderTeams": {
      const teams = { ...state.teams };
      for (const team of msg.teams) teams[team.teamId] = team;
      return { ...state, teams };
    }

    case "renderLiveTeams": {
      const teams = { ...state.teams };
      for (const live of msg.teams) teams[live.teamId] = mergeLiveTeam(teams[live.teamId], live);
      return { ...state, teams };
    }

    case "teamDetail": {
      const { posts: _posts, lastSeq: _lastSeq, ...summary } = msg.detail;
      return {
        ...state,
        teams: { ...state.teams, [msg.teamId]: summary },
        teamViews: { ...state.teamViews, [msg.teamId]: viewFromTeamDetail(msg.detail) },
      };
    }

    case "teamEvent": {
      const view = state.teamViews[msg.teamId];
      const team = state.teams[msg.teamId];
      return {
        ...state,
        teams: team ? { ...state.teams, [msg.teamId]: summaryWithEvent(team, msg.event) } : state.teams,
        teamViews: view
          ? { ...state.teamViews, [msg.teamId]: applyTeamEvent(view, msg.event) }
          : state.teamViews,
      };
    }
```

If the lint rejects the unused `_posts`/`_lastSeq` bindings, build the summary explicitly instead (`const summary: TeamSummaryView = { teamId: d.teamId, … }`).

- [ ] **Step 6: Context, card, roster-row export, MessageRow**

`components/teams/TeamsContext.tsx`:

```tsx
import { createContext, useContext } from "react";
import type { TeamSummaryView, TeamViewState } from "../../types";

/** What team UI needs from the thread (spec v2 §9). Provided by ThreadView. */
export interface TeamsUi {
  teams: Record<string, TeamSummaryView>;
  views: Record<string, TeamViewState>;
  openTeam(teamId: string): void;
}

export const TeamsContext = createContext<TeamsUi>({ teams: {}, views: {}, openTeam: () => {} });

export function useTeamsUi(): TeamsUi {
  return useContext(TeamsContext);
}
```

`AgentRosterCard.tsx`: change `function AgentRosterRow(` to `export function AgentRosterRow(`.

`components/teams/TeamCard.tsx`:

```tsx
import { formatElapsed, isTerminalAgent } from "../../agents";
import { isTerminalTeam, waitingOn } from "../../teams";
import type { TeamSummaryView } from "../../types";
import { Icon } from "../Icon";
import { AgentRosterRow, rosterRow } from "../agents/AgentRosterCard";
import { useAgentsUi } from "../agents/AgentsContext";
import { useNow } from "../agents/useNow";
import { useTeamsUi } from "./TeamsContext";

function placeholder(teamId: string, name: string): TeamSummaryView {
  return { teamId, name, goal: "", phase: "DELIBERATING", round: 1, maxRounds: 0,
    pausedReason: null, members: [], openProposals: [], usage: { requests: 0, budget: 0 },
    createdAt: "" };
}

/** A team in the transcript (spec v2 §9), anchored by its team_created message. */
export function TeamCard({ teamId, agentIds, name = "team" }: {
  teamId: string; agentIds: string[]; name?: string;
}) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const team = teamsUi.teams[teamId] ?? placeholder(teamId, name);
  const rows = agentIds.map((id) => rosterRow(agentsUi.agents, id));
  const ended = isTerminalTeam(team.phase);
  const now = useNow(!ended && rows.some((a) => !isTerminalAgent(a.status)));
  const waiting = ended ? null : waitingOn(team, agentsUi.agents, now);
  return (
    <div className="surface-card overflow-hidden" data-testid="team-card">
      <div className="accent-wash px-3 py-2" style={{ borderBottom: "1px solid var(--color-border)" }}>
        <div className="flex items-center gap-2">
          <span className="flex h-5 w-5 items-center justify-center rounded-md"
            style={{ background: "var(--accent-bg)", border: "1px solid var(--accent-brd)",
                     color: "var(--color-accent-ink)" }}>
            <Icon name="orbit" size={11} />
          </span>
          <span className="truncate text-xs font-semibold text-text">{team.name}</span>
          <span className="whitespace-nowrap text-[10.5px] text-text-3">
            {team.phase.toLowerCase()} · round {team.round} of {team.maxRounds}
          </span>
          <button type="button" onClick={() => teamsUi.openTeam(teamId)}
            className="ml-auto cursor-pointer whitespace-nowrap rounded-md px-2 py-0.5 text-[10.5px]"
            style={{ border: "1px solid var(--accent-brd)", color: "var(--color-accent-ink)",
                     background: "var(--accent-bg)" }}>
            Open board
          </button>
        </div>
        {waiting && (
          <div className="mt-1 text-[11px] text-text-3">
            waiting on {waiting.label} ({formatElapsed(waiting.ms)})
          </div>
        )}
        {team.pausedReason && (
          <div className="mt-1 text-[11px] text-amber">{team.pausedReason}</div>
        )}
      </div>
      {rows.map((agent) => (
        <AgentRosterRow key={agent.agentId} agent={agent} now={now} siblings={agentIds} />
      ))}
      {team.usage.budget > 0 && (
        // Phase 4 counts no requests (spec v2 §8 owns the budget), so only the budget shows.
        <div className="px-3 py-1.5 text-[10.5px] text-text-3"
          style={{ borderTop: "1px solid var(--color-border)" }}>
          budget {team.usage.budget} requests
        </div>
      )}
    </div>
  );
}
```

`formatElapsed(30000)` returns `30s`, so the waiting line reads `waiting on alice (30s)`; the test matches its prefix only.

`MessageRow.tsx` — replace the Phase-3 placeholder case:

```tsx
    // A team's card (spec v2 §9): live data comes from TeamsContext; the message's
    // metadata names the team until its summary loads.
    case "team_created": {
      const teamId = msg.metadata?.team_id;
      const ids = msg.metadata?.agent_ids;
      return typeof teamId === "string" && Array.isArray(ids)
        ? <TeamCard teamId={teamId} agentIds={ids as string[]}
            name={typeof msg.metadata?.name === "string" ? msg.metadata.name : undefined} />
        : null;
    }
```

(import `TeamCard` from `./teams/TeamCard`).

- [ ] **Step 7: Run and commit**

Run (from `apps/vscode-extension/webview-ui`): `npx vitest run > /tmp/p4t10.txt 2>&1; echo exit=$?; tail -6 /tmp/p4t10.txt` and `npm run typecheck > /tmp/p4t10b.txt 2>&1; echo exit=$?`.
Expected: two `exit=0`.

```bash
git add apps/vscode-extension/webview-ui/src/types.ts apps/vscode-extension/webview-ui/src/teams.ts apps/vscode-extension/webview-ui/src/hooks/useAppState.ts apps/vscode-extension/webview-ui/src/components/teams apps/vscode-extension/webview-ui/src/components/MessageRow.tsx apps/vscode-extension/webview-ui/src/components/agents/AgentRosterCard.tsx apps/vscode-extension/webview-ui/src/test/teams.test.ts apps/vscode-extension/webview-ui/src/test/teamCard.test.tsx apps/vscode-extension/webview-ui/src/test/useAppState.test.ts
git commit -m "feat(webview): team state, reducer and the team card"
```

### Task 11: Webview — the team window and the board

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/components/teams/TeamWindow.tsx`, `apps/vscode-extension/webview-ui/src/components/teams/TeamBoard.tsx`, `apps/vscode-extension/webview-ui/src/test/teamWindow.test.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/components/ThreadView.tsx`

**Interfaces:**
- Consumes: Task 10 (`TeamsContext`, `teams.ts`, `TeamViewState`, `AgentTranscript`), Task 9's `setOpenTeams`/`disbandTeam` host messages.
- Produces:
  - `<TeamWindow teamId tab onTab(tab: string) onClose />` — `tab` is `"board"` or a member's `agentId`
  - `<TeamBoard teamId />` — the posts in `seq` order
  - `ThreadView`: state `teamWindow: {teamId: string; tab: string} | null`; provides `TeamsContext`; posts `setOpenTeams {teamIds}`; an open member tab joins the `setOpenAgents` set

The window (spec §9) generalizes v1's `AgentWindow` layout: header with the name, phase and round, and the budget; **Board** as the first tab, one tab per member (each an `AgentTranscript`); **Disband** asks for a confirm inline (a second click — VS Code webviews block `window.confirm`) and posts `disbandTeam`. The board shows authors and kinds from the post's columns; `@mentions` highlighted; proposals as cards (text, assignments, shared files, a stance row per member, closed proposals dimmed with their reason); agrees and objects as compact lines with their note or reason, an objection's evidence expandable; `system` posts as compact lines; a **Messages** toggle (off by default) interleaves direct messages as `alice → bob`. The adoption evaluation and the `team_plan` gate are Phase 5.

- [ ] **Step 1: Write the failing test** (`src/test/teamWindow.test.tsx`)

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { AgentsContext, type AgentsUi } from "../components/agents/AgentsContext";
import { ThreadView } from "../components/ThreadView";
import { TeamWindow } from "../components/teams/TeamWindow";
import { TeamsContext, type TeamsUi } from "../components/teams/TeamsContext";
import type { AppState, TeamPostView, TeamSummaryView } from "../types";

let postMessage: ReturnType<typeof vi.fn>;
beforeEach(async () => {
  postMessage = (await import("../vscodeApi")).vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

const TEAM: TeamSummaryView = {
  teamId: "team-1", name: "auth", goal: "Add login", phase: "DELIBERATING", round: 1,
  maxRounds: 3, pausedReason: null,
  members: [{ label: "alice", agentId: "agent-a", status: "running" },
            { label: "bob", agentId: "agent-b", status: "awaiting_peer" }],
  openProposals: [], usage: { requests: 0, budget: 160 }, createdAt: "2026-10-05T00:00:00Z",
};

const post = (seq: number, over: Partial<TeamPostView>): TeamPostView => ({
  teamId: "team-1", seq, author: "alice", kind: "post", recipient: null, text: "", mentions: [],
  refId: null, round: 1, payload: {}, closed: null, createdAt: "2026-10-05T00:00:00Z", ...over,
});

const POSTS: TeamPostView[] = [
  post(1, { author: "main", kind: "proposal", round: 0, text: "api adds the limiter",
    payload: { assignments: [{ member: "alice", part: "limiter", files: ["api/limiter.py"] }],
               shared_files: ["api/routes.py"], supersedes: [] } }),
  post(2, { kind: "object", refId: "P1", text: "misses a caller",
    payload: { evidence: { files: ["api/admin.py"], line: 17 } } }),
  post(3, { author: "bob", kind: "agree", refId: "P1", text: "fine by me" }),
  post(4, { text: "@bob can you check admin.py", mentions: ["bob"] }),
  post(5, { recipient: "bob", text: "private note" }),
  post(6, { author: "system", kind: "system", text: "alice finished (completed)" }),
  // Body text imitating a system line still renders under its real author.
  post(7, { author: "bob", text: "Adopted P1 — implementing" }),
];

function renderWindow(onTab = vi.fn(), onClose = vi.fn(), tab = "board") {
  const agentsUi: AgentsUi = { agents: {}, views: {}, expanded: new Set(),
    toggleExpanded: vi.fn(), openWindow: vi.fn() };
  const teamsUi: TeamsUi = { teams: { "team-1": TEAM },
    views: { "team-1": { posts: POSTS, lastSeq: 7 } }, openTeam: vi.fn() };
  render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={teamsUi}>
    <TeamWindow teamId="team-1" tab={tab} onTab={onTab} onClose={onClose} />
  </TeamsContext.Provider></AgentsContext.Provider>);
  return { onTab, onClose };
}

describe("TeamWindow", () => {
  it("renders the board: proposal card with stances, objection evidence, mentions, system lines", () => {
    renderWindow();
    expect(screen.getByRole("dialog", { name: "Team auth" })).toBeInTheDocument();
    expect(screen.getByText("deliberating · round 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("P1")).toBeInTheDocument();
    expect(screen.getByText("api adds the limiter")).toBeInTheDocument();
    expect(screen.getByText("alice: limiter — api/limiter.py")).toBeInTheDocument();
    expect(screen.getByText("shared: api/routes.py")).toBeInTheDocument();
    expect(screen.getByLabelText("alice objects")).toBeInTheDocument();
    expect(screen.getByLabelText("bob agrees")).toBeInTheDocument();
    expect(screen.getByText("misses a caller")).toBeInTheDocument();
    fireEvent.click(screen.getByText("evidence"));
    expect(screen.getByText("api/admin.py:17")).toBeInTheDocument();
    expect(screen.getByText("@bob")).toHaveAttribute("data-mention", "bob");
    expect(screen.getByText("alice finished (completed)")).toBeInTheDocument();
    const imitation = screen.getByText("Adopted P1 — implementing");
    expect(imitation.closest("[data-author]")).toHaveAttribute("data-author", "bob");
  });

  it("direct messages show only with the Messages toggle", () => {
    renderWindow();
    expect(screen.queryByText("private note")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Messages" }));
    expect(screen.getByText("private note")).toBeInTheDocument();
    expect(screen.getByText("alice → bob")).toBeInTheDocument();
  });

  it("member tabs switch, Disband needs a confirm, Esc closes", () => {
    const { onTab, onClose } = renderWindow();
    fireEvent.click(screen.getByRole("tab", { name: /bob/ }));
    expect(onTab).toHaveBeenCalledWith("agent-b");
    fireEvent.click(screen.getByRole("button", { name: "Disband" }));
    expect(postMessage).not.toHaveBeenCalledWith({ type: "disbandTeam", teamId: "team-1" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm disband" }));
    expect(postMessage).toHaveBeenCalledWith({ type: "disbandTeam", teamId: "team-1" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("an ended team has no Disband button", () => {
    const agentsUi: AgentsUi = { agents: {}, views: {}, expanded: new Set(),
      toggleExpanded: vi.fn(), openWindow: vi.fn() };
    render(<AgentsContext.Provider value={agentsUi}><TeamsContext.Provider value={{
      teams: { "team-1": { ...TEAM, phase: "DISBANDED" } }, views: {}, openTeam: vi.fn() }}>
      <TeamWindow teamId="team-1" tab="board" onTab={vi.fn()} onClose={vi.fn()} />
    </TeamsContext.Provider></AgentsContext.Provider>);
    expect(screen.queryByRole("button", { name: "Disband" })).toBeNull();
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
                   metadata: { team_id: "team-1", name: "auth", agent_ids: ["agent-a", "agent-b"] } }],
    } as AppState;
    render(<ThreadView state={state} onBack={() => {}} dismissedErrorTaskId={null} onDismissError={() => {}} />);
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenTeams", teamIds: [] });
    act(() => { screen.getByRole("button", { name: "Open board" }).click(); });
    expect(screen.getByRole("dialog", { name: "Team auth" })).toBeInTheDocument();
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenTeams", teamIds: ["team-1"] });
    act(() => { screen.getByRole("tab", { name: /alice/ }).click(); });
    expect(postMessage).toHaveBeenCalledWith({ type: "setOpenAgents", agentIds: ["agent-a"] });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (from `apps/vscode-extension/webview-ui`): `npx vitest run src/test/teamWindow.test.tsx > /tmp/p4t11.txt 2>&1; echo exit=$?; tail -15 /tmp/p4t11.txt`
Expected: exit≠0 (`TeamWindow` does not exist).

- [ ] **Step 3: `components/teams/TeamBoard.tsx`**

```tsx
import { useState, type ReactNode } from "react";
import { stanceOf } from "../../teams";
import type { TeamPostView } from "../../types";
import { useTeamsUi } from "./TeamsContext";

const MENTION_SPLIT = /(@[a-z0-9-]+)/;
const IS_MENTION = /^@[a-z0-9-]+$/;

/** Body text with @mentions highlighted. The text is the agent's; nothing in it is
 * interpreted — only rendered (spec §3.10: authors and kinds come from columns). */
function withMentions(text: string): ReactNode[] {
  return text.split(MENTION_SPLIT).map((part, i) =>
    IS_MENTION.test(part)
      ? <span key={i} data-mention={part.slice(1)} className="font-semibold"
          style={{ color: "var(--color-accent-ink)" }}>{part}</span>
      : <span key={i}>{part}</span>);
}

interface Assignment { member?: unknown; part?: unknown; files?: unknown }

function ProposalCard({ post, posts, members }: {
  post: TeamPostView; posts: TeamPostView[]; members: string[];
}) {
  const assignments = Array.isArray(post.payload.assignments)
    ? post.payload.assignments as Assignment[] : [];
  const shared = Array.isArray(post.payload.shared_files)
    ? post.payload.shared_files as string[] : [];
  return (
    <div data-author={post.author} className="surface-card px-3 py-2"
      style={post.closed ? { opacity: 0.55 } : undefined}>
      <div className="flex items-center gap-2 text-[11px]">
        <span className="rounded px-1.5 font-mono font-semibold"
          style={{ background: "var(--accent-bg)", color: "var(--color-accent-ink)" }}>
          P{post.seq}
        </span>
        <span className="font-semibold text-text">{post.author}</span>
        <span className="text-text-3">proposal{post.round !== null ? ` · round ${post.round}` : ""}</span>
        {post.closed && <span className="ml-auto text-text-3">{post.closed}</span>}
      </div>
      <div className="mt-1 whitespace-pre-wrap text-xs text-text">{withMentions(post.text)}</div>
      {assignments.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-[11px] text-text-2">
          {assignments.map((a, i) => (
            <li key={i}>{`${String(a.member)}: ${String(a.part)} — ${
              Array.isArray(a.files) ? (a.files as string[]).join(", ") : ""}`}</li>
          ))}
        </ul>
      )}
      {shared.length > 0 && (
        <div className="mt-1 text-[11px] text-text-3">{`shared: ${shared.join(", ")}`}</div>
      )}
      <div className="mt-1.5 flex flex-wrap gap-1.5 text-[10.5px]">
        {members.map((label) => {
          const stance = stanceOf(posts, post.seq, label);
          const word = stance === "agree" ? "agrees" : stance === "object" ? "objects" : "no stance";
          const color = stance === "agree" ? "var(--color-green)"
            : stance === "object" ? "var(--color-red)" : "var(--color-text-3)";
          return (
            <span key={label} aria-label={`${label} ${word}`} className="rounded-full px-1.5"
              style={{ border: "1px solid var(--color-border)", color }}>
              {label} {stance === "agree" ? "✓" : stance === "object" ? "✗" : "–"}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function evidenceLines(evidence: Record<string, unknown>): string[] {
  const lines: string[] = [];
  const files = Array.isArray(evidence.files) ? evidence.files as string[] : [];
  if (files.length > 0) {
    lines.push(typeof evidence.line === "number" ? `${files[0]}:${evidence.line}` : files[0],
               ...files.slice(1));
  }
  if (typeof evidence.command === "string") lines.push(`$ ${evidence.command}`);
  if (typeof evidence.output === "string") lines.push(evidence.output);
  if (typeof evidence.quote_seq === "number") lines.push(`quotes post #${evidence.quote_seq}`);
  return lines;
}

function PostLine({ post }: { post: TeamPostView }) {
  if (post.kind === "system") {
    return <div data-author="system" className="text-[11px] italic text-text-3">{post.text}</div>;
  }
  const isStance = post.kind === "agree" || post.kind === "object";
  const evidence = post.payload.evidence as Record<string, unknown> | undefined;
  return (
    <div data-author={post.author} className="text-xs">
      <div className="flex items-center gap-1.5 text-[11px]">
        <span className="font-semibold text-text">
          {post.recipient ? `${post.author} → ${post.recipient}` : post.author}
        </span>
        {isStance && (
          <span style={{ color: post.kind === "agree" ? "var(--color-green)" : "var(--color-red)" }}>
            {post.kind === "agree" ? "agrees with" : "objects to"} {post.refId}
          </span>
        )}
        {post.kind === "withdraw" && <span className="text-text-3">withdraws {post.refId}</span>}
      </div>
      {post.text && (
        <div className="whitespace-pre-wrap text-text-2">{withMentions(post.text)}</div>
      )}
      {evidence && (
        <details className="mt-0.5 text-[11px] text-text-3">
          <summary className="cursor-pointer">evidence</summary>
          {evidenceLines(evidence).map((line, i) => (
            <div key={i} className="whitespace-pre-wrap font-mono">{line}</div>
          ))}
        </details>
      )}
    </div>
  );
}

/** The board (spec v2 §9): posts in seq order. */
export function TeamBoard({ teamId }: { teamId: string }) {
  const ui = useTeamsUi();
  const [showMessages, setShowMessages] = useState(false);
  const team = ui.teams[teamId];
  const posts = ui.views[teamId]?.posts ?? [];
  const members = team?.members.map((m) => m.label) ?? [];
  const shown = posts.filter((p) => showMessages || p.recipient === null);
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <button type="button" aria-pressed={showMessages} onClick={() => setShowMessages((v) => !v)}
          className="cursor-pointer rounded-md px-2 py-0.5 text-[10.5px]"
          style={{ border: "1px solid var(--color-border)",
                   color: showMessages ? "var(--color-accent-ink)" : "var(--color-text-3)",
                   background: showMessages ? "var(--accent-bg)" : "transparent" }}>
          Messages
        </button>
      </div>
      {shown.length === 0 && <div className="text-[11px] text-text-3">No posts yet.</div>}
      {shown.map((p) => p.kind === "proposal"
        ? <ProposalCard key={p.seq} post={p} posts={posts} members={members} />
        : <PostLine key={p.seq} post={p} />)}
    </div>
  );
}
```

`split` with a capturing group keeps each mention as its own part; both patterns are non-global, so `.test` carries no `lastIndex` state between calls.

- [ ] **Step 4: `components/teams/TeamWindow.tsx`**

```tsx
import { useEffect, useState } from "react";
import { isTerminalTeam } from "../../teams";
import { vscode } from "../../vscodeApi";
import { Icon } from "../Icon";
import { AgentTranscript, viewToken } from "../agents/AgentTranscript";
import { TONE_COLOR, toneOf } from "../agents/AgentRosterCard";
import { useAgentsUi } from "../agents/AgentsContext";
import { useFollowBottom } from "../agents/useFollowBottom";
import { TeamBoard } from "./TeamBoard";
import { useTeamsUi } from "./TeamsContext";

interface Props {
  teamId: string;
  tab: string;            // "board" or a member's agentId
  onTab(tab: string): void;
  onClose(): void;
}

/** A team full height over the thread (spec v2 §9): Board first, one tab per member. */
export function TeamWindow({ teamId, tab, onTab, onClose }: Props) {
  const teamsUi = useTeamsUi();
  const agentsUi = useAgentsUi();
  const team = teamsUi.teams[teamId];
  const [confirming, setConfirming] = useState(false);
  const boardToken = String(teamsUi.views[teamId]?.lastSeq ?? 0);
  const { ref, onScroll } = useFollowBottom(
    `${teamId}|${tab}|${tab === "board" ? boardToken : viewToken(agentsUi.views[tab])}`);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!team) return null;
  const live = !isTerminalTeam(team.phase);
  return (
    <div role="presentation" className="scrim absolute inset-0 z-40"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}>
      <div role="dialog" aria-modal="true" aria-label={`Team ${team.name}`}
        className="surface-card anim-pop absolute inset-x-3 bottom-3 top-10 flex flex-col overflow-hidden">
        <div className="accent-wash px-3 pb-2 pt-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
          <div className="flex items-center gap-2">
            <Icon name="orbit" size={12} />
            <span className="truncate text-[13px] font-semibold text-text">{team.name}</span>
            <span className="whitespace-nowrap text-[10.5px] text-text-3">
              {team.phase.toLowerCase()} · round {team.round} of {team.maxRounds}
            </span>
            <span className="ml-auto flex items-center gap-1">
              {live && !confirming && (
                <button type="button" onClick={() => setConfirming(true)}
                  className="cursor-pointer rounded-md px-2 py-0.5 text-[10.5px]"
                  style={{ border: "1px solid var(--red-brd)", color: "var(--color-red)", background: "var(--red-bg)" }}>
                  Disband
                </button>
              )}
              {live && confirming && (
                <button type="button"
                  onClick={() => {
                    setConfirming(false);
                    vscode.postMessage({ type: "disbandTeam", teamId });
                  }}
                  className="cursor-pointer rounded-md px-2 py-0.5 text-[10.5px] font-semibold"
                  style={{ border: "1px solid var(--red-brd)", color: "var(--color-red)", background: "var(--red-bg)" }}>
                  Confirm disband
                </button>
              )}
              <button type="button" onClick={onClose} aria-label="Close team window" title="Close"
                className="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md text-text-3 transition-colors duration-150 hover:bg-surface-2 hover:text-text">
                <Icon name="x" size={12} />
              </button>
            </span>
          </div>
          <div className="mt-1 flex gap-2.5 text-[10.5px] text-text-3">
            <span className="truncate">{team.goal}</span>
            {team.usage.budget > 0 && <span className="whitespace-nowrap">budget {team.usage.budget}</span>}
          </div>
        </div>
        <div role="tablist" className="flex gap-0.5 overflow-x-auto px-2.5" style={{ borderBottom: "1px solid var(--color-border)" }}>
          {[{ id: "board", label: "Board", status: null as string | null },
            // The member's own status until the roster has loaded its row.
            ...team.members.map((m) => ({ id: m.agentId, label: m.label,
              status: agentsUi.agents[m.agentId]?.status ?? m.status }))].map((t) => {
            const on = t.id === tab;
            return (
              <button key={t.id} type="button" role="tab" aria-selected={on} onClick={() => onTab(t.id)}
                className="flex cursor-pointer items-center gap-1.5 whitespace-nowrap border-b-2 px-2 py-1.5 text-[11px]"
                style={{ color: on ? "var(--color-accent-ink)" : "var(--color-text-3)",
                         borderColor: on ? "var(--color-accent)" : "transparent" }}>
                {t.status !== null && (
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: TONE_COLOR[toneOf(t.status)] }} />
                )}
                {t.label}
              </button>
            );
          })}
        </div>
        <div ref={ref} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3">
          {tab === "board" ? <TeamBoard teamId={teamId} /> : <AgentTranscript agentId={tab} />}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: ThreadView**

Imports: `TeamsContext, type TeamsUi` from `./teams/TeamsContext`; `TeamWindow` from `./teams/TeamWindow`.

State, after `agentWindow`:

```tsx
  // The one open team window (spec v2 §9); its tab is "board" or a member's agentId.
  const [teamWindow, setTeamWindow] = useState<{ teamId: string; tab: string } | null>(null);
```

The thread-change effect also calls `setTeamWindow(null)`. The open-agents set includes the team window's member tab:

```tsx
  const teamAgent = teamWindow && teamWindow.tab !== "board" ? [teamWindow.tab] : [];
  const openAgentsKey = [...new Set([...expanded, ...(agentWindow ? [agentWindow.agentId] : []),
                                     ...teamAgent])]
    .sort().join(",");
```

and the open team is reported like the agents:

```tsx
  const openTeamId = teamWindow?.teamId ?? "";
  useEffect(() => {
    vscode.postMessage({ type: "setOpenTeams", teamIds: openTeamId ? [openTeamId] : [] });
  }, [openTeamId]);

  const teamsUi = useMemo<TeamsUi>(() => ({
    teams: state.teams,
    views: state.teamViews,
    openTeam: (teamId) => setTeamWindow({ teamId, tab: "board" }),
  }), [state.teams, state.teamViews]);
```

Wrap the existing `<AgentsContext.Provider value={agentsUi}>…</AgentsContext.Provider>` content in `<TeamsContext.Provider value={teamsUi}>`, and after the `AgentWindow` block:

```tsx
      {teamWindow !== null && (
        <TeamWindow
          teamId={teamWindow.teamId}
          tab={teamWindow.tab}
          onTab={(tab) => setTeamWindow({ ...teamWindow, tab })}
          onClose={() => setTeamWindow(null)}
        />
      )}
```

The existing ThreadView fixture in `agentWindow.test.tsx` predates teams: add `teams: {}, teamViews: {}` to it (ThreadView now reads both).

- [ ] **Step 6: Run and commit**

Run (from `apps/vscode-extension/webview-ui`): `npx vitest run > /tmp/p4t11.txt 2>&1; echo exit=$?; tail -6 /tmp/p4t11.txt` and `npm run typecheck > /tmp/p4t11b.txt 2>&1; echo exit=$?`; then from `apps/vscode-extension`: `npm run build > /tmp/p4t11c.txt 2>&1; echo exit=$?` (the webview bundle the dev host loads).
Expected: three `exit=0`.

```bash
git add apps/vscode-extension/webview-ui/src/components/teams apps/vscode-extension/webview-ui/src/components/ThreadView.tsx apps/vscode-extension/webview-ui/src/test/teamWindow.test.tsx apps/vscode-extension/webview-ui/src/test/agentWindow.test.tsx
git commit -m "feat(webview): team window with the board and member tabs"
```

---

# Part E — Docs, suites, smoke

### Task 12: Docs, full suites, live smoke

- [ ] **Step 1: CLAUDE.md** — under "Sub-agents (P5)", add an **Agent teams — foundations (v2 Phase 4)** bullet:
  - `CRUCIBLE_TEAMS_ENABLED` (default off; needs sub-agents; `/v1/config` `teams_enabled`; the test conftest forces it off — opt in with `monkeypatch.setenv("CRUCIBLE_TEAMS_ENABLED", "1")`). Limits: `CRUCIBLE_TEAM_MAX_MEMBERS` (6), `CRUCIBLE_TEAM_MAX_LIVE_PER_THREAD` (2), `CRUCIBLE_TEAM_REQUEST_BUDGET_PER_MEMBER` (80), `CRUCIBLE_TEAM_MAX_BUDGET` (1000, stored only — enforced in Phase 5), `CRUCIBLE_TEAM_MAX_WAKES` (15).
  - `agentd/teams/`: `store.py` (`teams`/`team_members`/`team_posts` on the chat DB connection, per-team post `seq`, `delivered_seq` never lowers), `validation.py` (labels, mentions, 8 000-char text, evidence), `service.py` (`TeamService`: board operations, `render_delta`, `status_text`, `brief`, `summary`), `tools.py` (members' `team_*`, the main agent's `create_team`/`post_board`/`team_status`/`adopt_proposal`/`resume_team`/`disband_team`).
  - The **interim activation policy** lives in one method, `ChatController._team_posted`, and Phase 5's coordinator replaces it: a proposal kickoff wakes every member, a post kickoff its mentions; a post wakes its mentions (`@team` everyone), a DM its recipient, never the author; a running member gets a marker that its next drain renders as one `team` inbox item; wakes cap per phase with a system post. A member's report becomes `"<label> finished (<status>)"` and wakes nobody.
  - Members keep `awaiting_peer` (no `partial` mapping); their team tools survive any definition `tools:` filter (`MEMBER_TOOL_NAMES` exemption); goal + roster ride the system prompt via `RenderContext.team_brief` and the `<<team>>` tag; phase/stances/unread ride the payload tail as `team_status` every iteration.
  - Routes `GET /chat/threads/{id}/teams`, `GET …/teams/{team_id}`, `POST …/teams/{team_id}/disband`; `/live` `teams` (slow fields only — in `lastLiveSignature`); team channel `chat:{thread}:team:{team}` (`team_post` with the post's `seq`, `team_phase`). Host `TeamViewManager` (`src/team-views.ts`); webview `TeamCard` + `TeamWindow` (Board tab + member tabs, Messages toggle, inline-confirm Disband).
  - GOTCHA: `/live` lists live teams only, so the controller reloads summaries when a team **leaves** `/live` — without that, an ended team's card keeps its last live phase.

  Commit with `docs(claude): agent teams foundations`.

- [ ] **Step 2: Full suites**

```bash
cd services/agentd-py && .venv/bin/pytest --color=no --timeout=120 > /tmp/py.txt 2>&1; echo exit=$?; tail -3 /tmp/py.txt
cd ../.. && npm run build > /tmp/build.txt 2>&1; echo exit=$?
npm run test > /tmp/ts.txt 2>&1; echo exit=$?; tail -8 /tmp/ts.txt
npm run typecheck > /tmp/tc.txt 2>&1; echo exit=$?
cd apps/vscode-extension/webview-ui && npx vitest run > /tmp/web.txt 2>&1; echo exit=$?; tail -4 /tmp/web.txt
```

Known pre-existing flakes (not this phase): `test_command_only_step_runs_command_and_verifies`, `test_reap_kills_live_recorded_process` — re-run alone before attributing. `cargo` is untouched by this phase.

- [ ] **Step 3: Live smoke on the dev host** (second VS Code instance, CDP 9335; `npm run build`, then "Developer: Reload Window")

The managed spawn does not pass `CRUCIBLE_TEAMS_ENABLED` (it is default-off until Phase 6), so run the backend with `start-backend.sh` and point the workspace's `crucible.backendBaseUrl` at it:

```bash
export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
CRUCIBLE_TEAMS_ENABLED=1 bash scripts/stress/start-backend.sh --backend openai_compatible \
  --workspace "$PWD/workspaces/subagent-smoke" --validation-profile none
curl -s http://localhost:8000/v1/config -H "Authorization: Bearer $(cat ~/.crucible/run/agentd-8000.token)" | python3 -m json.tool | grep teams_enabled
```

Expected: `"teams_enabled": true`.

1. Ask: "Create a team with two members — `api` (general-purpose) and `review` (explore) — to add a `/health` route to `app.py`. Kick off with your own proposal." The main agent calls `create_team` and answers at once. A team card appears with both members, `deliberating · round 1 of N`, and `waiting on <label> (Ns)` ticking.
2. Open board: the proposal card P1 shows assignments; within a minute each member's stance appears in the stance row (agree ✓ / object ✗), and `system` lines `<label> finished (…)` arrive live without a reload.
3. Toggle Messages; any direct message shows as `a → b`. Switch to a member tab — its transcript streams (Task 9's member-tab `setOpenAgents`).
4. Ask the main agent: "Tell the team that the route must return JSON." It calls `post_board`; the post appears on the board, and the mentioned (or `@team`) members are woken (their rows go running).
5. Reload the chat webview (close and reopen the chat tab): the card and board come back from the routes, with no duplicate posts (Review Focus 5).
6. Disband → Confirm disband: members stop, the board gets "The team was disbanded.", the card reads `disbanded`, and the main agent's `team_status` reports `DISBANDED`.
7. Restart the backend with a team live (create a new one, then stop `start-backend.sh` mid-deliberation and start it again): the team reads `failed` in the card after the reload; no member stays "running".

Inspect what a member actually received: `workspaces/subagent-smoke/.crucible/state/artifacts/chat/<thread>/<turn>/agents/<agent>/controller-turn-00.json` — the system prompt must contain the TEAM block (framing, brief, examples) and the payload tail `team_status`. Record any failure as a finding, fix it with a regression test, re-run the step.

