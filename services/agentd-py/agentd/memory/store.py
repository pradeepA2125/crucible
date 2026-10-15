from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import sqlite_vec  # type: ignore[import-untyped]

from agentd.memory.models import AnchoredSummary, CompactionSegment, Memory

logger = logging.getLogger(__name__)

_FTS_TOKEN = re.compile(r"\w+")


def _fts_match_query(text: str) -> str:
    """Turn an arbitrary query into a safe FTS5 MATCH expression: bare word tokens, each quoted
    as a literal (neutralizing AND/OR/NOT/paths/punctuation), OR-joined. Empty when no tokens."""
    return " OR ".join(f'"{t}"' for t in _FTS_TOKEN.findall(text))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS compaction_segments (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_run ON compaction_segments(run_id, seq);
CREATE TABLE IF NOT EXISTS history_sequences (
    run_id TEXT PRIMARY KEY, next_mseq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS curation_progress (
    run_id TEXT PRIMARY KEY, last_mseq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS curation_status (
    run_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending', failures INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT, error TEXT, updated_at TEXT, saved INTEGER NOT NULL DEFAULT 0,
    dropped INTEGER NOT NULL DEFAULT 0, chunks INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS anchored_summaries (
    run_id TEXT PRIMARY KEY,
    summary_md TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    entities TEXT NOT NULL,
    importance INTEGER NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    superseded_by TEXT,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_seq_lo INTEGER,
    source_seq_hi INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope_kind, scope_id, valid_to);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    memory_id UNINDEXED, content, entities
);
"""


class MemoryStore:
    """The only DB-aware unit. SQLite-backed compaction segments + anchored summaries +
    long-term memories (vectors in a co-located sqlite-vec table when available)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._vec_enabled = self._try_load_vec()
        self._conn.executescript(_SCHEMA)  # base tables + FTS5 (always)
        columns = {r[1] for r in self._conn.execute("PRAGMA table_info(compaction_segments)")}
        for name, sql_type in (("mseq", "INTEGER"), ("role", "TEXT"), ("tool", "TEXT"),
                               ("metadata", "TEXT")):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE compaction_segments ADD COLUMN {name} {sql_type}")
        if self._vec_enabled:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories "
                "USING vec0(memory_id TEXT PRIMARY KEY, embedding float[384])"
            )
        self._conn.commit()

    def _try_load_vec(self) -> bool:
        # FIX #1: a missing extension must NOT crash the store — Phase 1 compaction uses it too.
        try:
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            return True
        except Exception:  # noqa: BLE001 — degrade to FTS5-only
            logger.warning("[memory] sqlite-vec unavailable; semantic search disabled (FTS5-only)")
            return False

    def add_segments(self, segments: list[CompactionSegment]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO compaction_segments "
            "(id, run_id, seq, content, created_at,mseq,role,tool,metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(s.id, s.run_id, s.seq, s.content, s.created_at.isoformat(), s.mseq, s.role,
              s.tool, json.dumps(s.metadata, default=str)) for s in segments],
        )
        self._conn.commit()

    def get_segments(self, run_id: str) -> list[CompactionSegment]:
        rows = self._conn.execute(
            "SELECT * FROM compaction_segments WHERE run_id=? ORDER BY seq", (run_id,)
        ).fetchall()
        return [
            CompactionSegment(
                id=r["id"],
                run_id=r["run_id"],
                seq=r["seq"],
                content=r["content"],
                created_at=datetime.fromisoformat(r["created_at"]),
                mseq=r["mseq"], role=r["role"], tool=r["tool"],
                metadata=json.loads(r["metadata"] or "{}"),
            )
            for r in rows
        ]

    def next_seq(self, run_id: str) -> int:
        """Run-monotonic seq so ordering is stable across many compaction rounds."""
        r = self._conn.execute(
            "SELECT MAX(seq) AS m FROM compaction_segments WHERE run_id=?", (run_id,)
        ).fetchone()
        return 0 if r["m"] is None else r["m"] + 1

    def reserve_mseq(self, run_id: str, count: int, floor: int = 1) -> int:
        """Reserve source IDs durably, including gaps when a turn crashes or is rewound."""
        if count < 0 or floor < 1:
            raise ValueError("Invalid message sequence reservation")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO history_sequences(run_id,next_mseq) VALUES(?,?)",
                (run_id, floor),
            )
            row = self._conn.execute(
                "UPDATE history_sequences SET next_mseq=max(next_mseq,?)+? "
                "WHERE run_id=? RETURNING next_mseq-?", (floor, count, run_id, count),
            ).fetchone()
            assert row is not None
            self._conn.commit()
            return int(row[0])
        except BaseException:
            self._conn.rollback()
            raise

    def curation_watermark(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT last_mseq FROM curation_progress WHERE run_id=?", (run_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    def advance_curation(self, run_id: str, last_mseq: int) -> None:
        if last_mseq < 0:
            raise ValueError("Invalid curation watermark")
        self._conn.execute(
            "INSERT INTO curation_progress(run_id,last_mseq) VALUES(?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET last_mseq=max(last_mseq,excluded.last_mseq)",
            (run_id, last_mseq),
        )
        self._conn.commit()

    def curation_state(self, run_id: str) -> dict[str, object]:
        row = self._conn.execute(
            "SELECT * FROM curation_status WHERE run_id=?", (run_id,),
        ).fetchone()
        state = dict(row) if row else {"generation": 0, "status": "pending", "failures": 0}
        state["last_mseq"] = self.curation_watermark(run_id)
        return state

    def set_curation_state(self, run_id: str, **values: object) -> None:
        allowed = {"status", "failures", "retry_at", "error", "saved", "dropped", "chunks"}
        if set(values) - allowed:
            raise ValueError("Invalid curation state field")
        values["updated_at"] = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO curation_status(run_id) VALUES(?)", (run_id,))
            self._conn.execute(
                "UPDATE curation_status SET " + ",".join(f"{key}=?" for key in values)
                + " WHERE run_id=?", (*values.values(), run_id),
            )

    def rewind_evidence(self, run_id: str, cutoff_iso: str, retained_mseq: int) -> None:
        if retained_mseq < 0:
            raise ValueError("Invalid retained message sequence")
        cutoff_iso = datetime.fromisoformat(cutoff_iso).astimezone(UTC).isoformat()
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO curation_status(run_id) VALUES(?)", (run_id,))
            self._conn.execute(
                "UPDATE curation_status SET generation=generation+1,status='pending',"
                "failures=0,retry_at=NULL,error=NULL WHERE run_id=?", (run_id,),
            )
            self._conn.execute(
                "DELETE FROM compaction_segments WHERE run_id=? AND created_at>=?",
                (run_id, cutoff_iso),
            )
            self._conn.execute(
                "UPDATE curation_progress SET last_mseq=min(last_mseq,?) WHERE run_id=?",
                (retained_mseq, run_id),
            )

    def upsert_anchor(self, run_id: str, summary_md: str) -> AnchoredSummary:
        now = datetime.now(UTC)
        existing = self._conn.execute(
            "SELECT version FROM anchored_summaries WHERE run_id=?", (run_id,)
        ).fetchone()
        version = (existing["version"] + 1) if existing else 1
        self._conn.execute(
            "INSERT OR REPLACE INTO anchored_summaries "
            "(run_id, summary_md, version, updated_at) VALUES (?,?,?,?)",
            (run_id, summary_md, version, now.isoformat()),
        )
        self._conn.commit()
        return AnchoredSummary(
            run_id=run_id, summary_md=summary_md, version=version, updated_at=now
        )

    def get_anchor(self, run_id: str) -> AnchoredSummary | None:
        r = self._conn.execute(
            "SELECT * FROM anchored_summaries WHERE run_id=?", (run_id,)
        ).fetchone()
        if r is None:
            return None
        return AnchoredSummary(
            run_id=r["run_id"],
            summary_md=r["summary_md"],
            version=r["version"],
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )

    def clear_anchor(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM anchored_summaries WHERE run_id=?", (run_id,))
        self._conn.commit()

    def delete_segments(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM compaction_segments WHERE run_id=?", (run_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Phase 2: long-term memories
    # ------------------------------------------------------------------
    def _insert_rows(self, m: Memory, embedding: list[float]) -> None:
        # FIX #2: raw writes WITHOUT a `with` block, so supersede can wrap the UPDATE + these
        # inserts in ONE atomic transaction. Caller owns the transaction.
        self._conn.execute(
            "INSERT INTO memories (id, scope_kind, scope_id, kind, content, entities, "
            "importance, valid_from, valid_to, superseded_by, source_kind, source_ref, "
            "source_seq_lo, source_seq_hi, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m.id, m.scope_kind, m.scope_id, m.kind, m.content, json.dumps(m.entities),
             m.importance, m.valid_from.isoformat(),
             m.valid_to.isoformat() if m.valid_to else None, m.superseded_by, m.source_kind,
             m.source_ref, m.source_seq_lo, m.source_seq_hi, m.created_at.isoformat()),
        )
        self._conn.execute(
            "INSERT INTO memories_fts (memory_id, content, entities) VALUES (?,?,?)",
            (m.id, m.content, " ".join(m.entities)),
        )
        if embedding and self._vec_enabled:  # FIX #1: skip vec write when degraded
            self._conn.execute(
                "INSERT INTO vec_memories (memory_id, embedding) VALUES (?, ?)",
                (m.id, sqlite_vec.serialize_float32(embedding)),
            )

    def insert_memory(self, memory: Memory, embedding: list[float]) -> None:
        with self._conn:  # one transaction across the 3 tables
            self._insert_rows(memory, embedding)

    def get_memory(self, memory_id: str) -> Memory | None:
        r = self._conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return self._row_to_memory(r) if r else None

    @staticmethod
    def _row_to_memory(r: sqlite3.Row) -> Memory:
        return Memory(
            id=r["id"], scope_kind=r["scope_kind"], scope_id=r["scope_id"], kind=r["kind"],
            content=r["content"], entities=json.loads(r["entities"]), importance=r["importance"],
            valid_from=datetime.fromisoformat(r["valid_from"]),
            valid_to=datetime.fromisoformat(r["valid_to"]) if r["valid_to"] else None,
            superseded_by=r["superseded_by"], source_kind=r["source_kind"],
            source_ref=r["source_ref"], source_seq_lo=r["source_seq_lo"],
            source_seq_hi=r["source_seq_hi"], created_at=datetime.fromisoformat(r["created_at"]),
        )

    def get_live_memories(self, scope_kind: str, scope_id: str) -> list[Memory]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE valid_to IS NULL AND scope_kind=? AND scope_id=?",
            (scope_kind, scope_id),
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def search_semantic(
        self, query_embedding: list[float], k: int, scope_kind: str, scope_id: str
    ) -> list[tuple[str, float]]:
        # FIX #1: degrade when vec disabled. FIX #6: over-fetch (k*4) BEFORE the live+scope JOIN
        # filter, else the KNN cap drops in-scope hits.
        if not query_embedding or not self._vec_enabled:
            return []
        rows = self._conn.execute(
            "SELECT v.memory_id AS mid, v.distance AS dist "
            "FROM vec_memories v JOIN memories m ON m.id = v.memory_id "
            "WHERE v.embedding MATCH ? AND k = ? AND m.valid_to IS NULL "
            "AND m.scope_kind=? AND m.scope_id=? ORDER BY v.distance LIMIT ?",
            (sqlite_vec.serialize_float32(query_embedding), k * 4, scope_kind, scope_id, k),
        ).fetchall()
        return [(r["mid"], r["dist"]) for r in rows]

    def search_lexical(
        self, query: str, k: int, scope_kind: str, scope_id: str
    ) -> list[tuple[str, float]]:
        # A raw user query (paths/dots/colons/AND/OR) is NOT a valid FTS5 MATCH expression and
        # raises a syntax error. Tokenize to bare words, quote each as a literal, OR-join.
        match = _fts_match_query(query)
        if not match:
            return []
        rows = self._conn.execute(
            "SELECT f.memory_id AS mid, bm25(memories_fts) AS rank "
            "FROM memories_fts f JOIN memories m ON m.id = f.memory_id "
            "WHERE memories_fts MATCH ? AND m.valid_to IS NULL "
            "AND m.scope_kind=? AND m.scope_id=? ORDER BY rank LIMIT ?",
            (match, scope_kind, scope_id, k),
        ).fetchall()
        return [(r["mid"], r["rank"]) for r in rows]

    def similar_memories(
        self, embedding: list[float], kind: str, scope_kind: str, scope_id: str, k: int
    ) -> list[tuple[Memory, float]]:
        if not embedding or not self._vec_enabled:  # FIX #1
            return []
        rows = self._conn.execute(
            "SELECT m.*, v.distance AS dist "
            "FROM vec_memories v JOIN memories m ON m.id = v.memory_id "
            "WHERE v.embedding MATCH ? AND k = ? AND m.valid_to IS NULL AND m.kind=? "
            "AND m.scope_kind=? AND m.scope_id=? ORDER BY v.distance LIMIT ?",
            (sqlite_vec.serialize_float32(embedding), k * 4, kind, scope_kind, scope_id, k),
        ).fetchall()
        return [(self._row_to_memory(r), r["dist"]) for r in rows]

    def supersede(self, old_id: str, new_memory: Memory, new_embedding: list[float]) -> None:
        # FIX #2: ONE transaction — retire old + insert new atomically. If the insert raises,
        # the UPDATE rolls back too (no orphaned retire). Never nest insert_memory here.
        with self._conn:
            self._conn.execute(
                "UPDATE memories SET valid_to=?, superseded_by=? WHERE id=?",
                (new_memory.valid_from.isoformat(), new_memory.id, old_id),
            )
            self._insert_rows(new_memory, new_embedding)

    # ------------------------------------------------------------------
    # Phase 3: read-only browse (the inspector panel)
    # ------------------------------------------------------------------
    def list_memories(
        self, scope_kind: str, scope_id: str, kind: str | None = None,
        include_retired: bool = False,
    ) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE scope_kind=? AND scope_id=?"
        args: list[object] = [scope_kind, scope_id]
        if not include_retired:
            sql += " AND valid_to IS NULL"
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY importance DESC, valid_from DESC"
        return [self._row_to_memory(r) for r in self._conn.execute(sql, args).fetchall()]

    def retire_since(self, source_ref: str, cutoff_iso: str) -> int:
        """Retire live memories written by one run at or after a cutoff.

        `source_ref` is the consolidator's run_id, which for the chat controller IS the
        thread_id — so this one predicate catches both thread- and workspace-scoped
        memories a rewound span produced, with no schema change. RecallEngine already
        filters `valid_to IS NULL` before scoring, so retirement takes effect with no
        recall-path change and no vec/FTS index surgery.
        """
        cur = self._conn.execute(
            "UPDATE memories SET valid_to = ? "
            "WHERE source_ref = ? AND valid_to IS NULL AND created_at >= ?",
            (datetime.now(UTC).isoformat(), source_ref, cutoff_iso),
        )
        self._conn.commit()
        return cur.rowcount or 0

    def get_supersede_chain(self, memory_id: str) -> list[Memory]:
        # Walk back to the oldest (the row nobody supersedes), then forward via superseded_by.
        cur = self.get_memory(memory_id)
        while cur is not None:
            prev = self._conn.execute(
                "SELECT * FROM memories WHERE superseded_by=?", (cur.id,)
            ).fetchone()
            if prev is None:
                break
            cur = self._row_to_memory(prev)
        chain: list[Memory] = []
        seen: set[str] = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            chain.append(cur)
            cur = self.get_memory(cur.superseded_by) if cur.superseded_by else None
        return chain
