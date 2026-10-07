"""Team tables on the chat database (spec v2 §7.4). Shares ChatThreadStore's connection;
the backend is one asyncio thread, so each method's statements run without interleaving."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from agentd.providers.usage import Usage
from agentd.teams.models import (
    ACTIVITY_KINDS,
    LIVE_TEAM_PHASES,
    TeamActivity,
    TeamMember,
    TeamPost,
    TeamRecord,
)

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
        # Spec 2026-10-05 §4.1: the lifecycle record. Its own per-team sequence (aseq) so
        # post seq stays the delivery cursor and the proposal id.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS team_activity (
                team_id TEXT NOT NULL, aseq INTEGER NOT NULL, at TEXT NOT NULL,
                label TEXT NOT NULL, kind TEXT NOT NULL, activation INTEGER,
                cause_seq INTEGER, payload_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (team_id, aseq))""")
        # Added after the table shipped: databases created before it gain the column.
        existing = {r[1] for r in self._conn.execute("PRAGMA table_info(teams)")}
        if "cached_tokens" not in existing:
            self._conn.execute(
                "ALTER TABLE teams ADD COLUMN cached_tokens INTEGER NOT NULL DEFAULT 0")
        if "round_cutoff_seq" not in existing:
            self._conn.execute("ALTER TABLE teams ADD COLUMN round_cutoff_seq INTEGER")
        if "lead" not in existing:
            self._conn.execute("ALTER TABLE teams ADD COLUMN lead TEXT")
        if "vote_between" not in existing:
            self._conn.execute(
                "ALTER TABLE teams ADD COLUMN vote_between TEXT NOT NULL DEFAULT ''")
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

    def live_team_names(self, thread_id: str) -> list[str]:
        marks = ", ".join("?" * len(LIVE_TEAM_PHASES))
        rows = self._conn.execute(
            f"SELECT name FROM teams WHERE thread_id = ? AND phase IN ({marks}) "  # noqa: S608
            "ORDER BY created_at", (thread_id, *sorted(LIVE_TEAM_PHASES))).fetchall()
        return [r[0] for r in rows]

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

    def teams_from_checkpoint(self, thread_id: str, seq: int) -> list[str]:
        return [r["team_id"] for r in self._conn.execute(
            "SELECT team_id FROM teams WHERE thread_id = ? AND checkpoint_seq >= ?",
            (thread_id, seq)).fetchall()]

    def teams_for_turns(self, thread_id: str, turn_ids: list[str]) -> list[str]:
        if not turn_ids:
            return []
        marks = ", ".join("?" * len(turn_ids))
        return [r["team_id"] for r in self._conn.execute(
            f"SELECT team_id FROM teams WHERE thread_id = ? AND created_turn_id IN ({marks})",  # noqa: S608 — placeholders only
            (thread_id, *turn_ids)).fetchall()]

    def delete_teams(self, team_ids: list[str]) -> None:
        """A rewind past a team removes it whole (spec v2 §8.10)."""
        if not team_ids:
            return
        marks = ", ".join("?" * len(team_ids))
        for table in ("team_activity", "team_posts", "team_members", "teams"):
            self._conn.execute(
                f"DELETE FROM {table} WHERE team_id IN ({marks})", team_ids)  # noqa: S608 — fixed table names
        self._conn.commit()

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

    def set_in_quorum(self, team_id: str, label: str, in_quorum: bool) -> None:
        self._conn.execute(
            "UPDATE team_members SET in_quorum = ? WHERE team_id = ? AND label = ?",
            (int(in_quorum), team_id, label))
        self._conn.commit()

    def bump_wakes(self, team_id: str, label: str) -> int:
        self._conn.execute(
            "UPDATE team_members SET wakes_this_phase = wakes_this_phase + 1 "
            "WHERE team_id = ? AND label = ?", (team_id, label))
        self._conn.commit()
        member = self.member(team_id, label)
        return member.wakes_this_phase if member else 0

    # ── posts ────────────────────────────────────────────────────────────────

    def set_assignment(self, team_id: str, label: str, assignment: dict[str, object] | None,
                       ) -> None:
        self._conn.execute(
            "UPDATE team_members SET assignment_json = ?, assignment_done = 0 "
            "WHERE team_id = ? AND label = ?",
            (json.dumps(assignment) if assignment is not None else None, team_id, label))
        self._conn.commit()

    def set_assignment_done(self, team_id: str, label: str) -> None:
        self._conn.execute(
            "UPDATE team_members SET assignment_done = 1 WHERE team_id = ? AND label = ?",
            (team_id, label))
        self._conn.commit()

    def reset_wakes(self, team_id: str) -> None:
        """The wake cap counts per phase (spec v2 §8.6)."""
        self._conn.execute("UPDATE team_members SET wakes_this_phase = 0 WHERE team_id = ?",
                           (team_id,))
        self._conn.commit()

    def add_usage(self, team_id: str, usage: Usage) -> None:
        """A team's usage is the sum over its members and their helpers (spec §3.11)."""
        self._conn.execute(
            "UPDATE teams SET requests = requests + ?, prompt_tokens = prompt_tokens + ?, "
            "completion_tokens = completion_tokens + ?, cached_tokens = cached_tokens + ? "
            "WHERE team_id = ?",
            (usage.requests, usage.prompt_tokens, usage.completion_tokens, usage.cached_tokens,
             team_id))
        self._conn.commit()

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
