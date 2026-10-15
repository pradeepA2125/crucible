"""Scoped v2 memory storage; compaction evidence stays in the workspace store."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agentd.memory.models import DurableMemory, MemoryKind
from agentd.memory.scope import ScopeContext, ScopeKind

logger = logging.getLogger(__name__)
WriteOrigin = Literal["user_requested", "agent_nomination", "curator"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY, scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL,
    scope_key TEXT NOT NULL CHECK(scope_key=scope_kind || ':' || scope_id),
    kind TEXT NOT NULL, content TEXT NOT NULL,
    normalized_content TEXT NOT NULL, topic TEXT NOT NULL, applies_when TEXT NOT NULL,
    detail TEXT NOT NULL, entities TEXT NOT NULL, provenance TEXT NOT NULL,
    source_thread_id TEXT NOT NULL, source_message_id INTEGER NOT NULL,
    revision INTEGER NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
    superseded_by TEXT, created_at TEXT NOT NULL,
    embedding_state TEXT NOT NULL, embedding_model TEXT, embedded_hash TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_durable_claim
    ON memories(scope_key, normalized_content) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_durable_scope ON memories(scope_key, valid_to);
CREATE INDEX IF NOT EXISTS idx_durable_source ON memories(source_thread_id, source_message_id);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    memory_id UNINDEXED, content, topic, applies_when, entities, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS memory_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL,
    scope_key TEXT NOT NULL, action TEXT NOT NULL, old_row TEXT, new_row TEXT,
    reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_vector_config (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), model TEXT,
    dimensions INTEGER NOT NULL, dirty INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO memory_vector_config(singleton, model, dimensions) VALUES(1, NULL, 384);
CREATE TABLE IF NOT EXISTS curation_batches (
    batch_key TEXT PRIMARY KEY, result_json TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class MemoryAudit:
    action: str
    old: DurableMemory | None
    new: DurableMemory | None
    reason: str
    created_at: datetime


def embedding_hash(memory: DurableMemory) -> str:
    return hashlib.sha256(memory.embedding_text.encode()).hexdigest()


def _claim_key(content: str) -> str:
    # Case and interior whitespace may be significant in identifiers or literals.
    return content.strip()


class DurableMemoryStore:
    """One caller-owned connection, atomic mutations, no bare-ID memory reads."""

    def __init__(
        self,
        path: Path | str,
        *,
        embedding_model: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), timeout=5)
        self._conn.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in self._conn.execute("SELECT name FROM sqlite_master")}
            columns = {r[1] for r in self._conn.execute("PRAGMA table_info(memories)")}
            required = set(DurableMemory.model_fields) | {"scope_key", "normalized_content"}
            if "compaction_segments" in tables or (
                "memories" in tables and not required.issubset(columns)
            ):
                raise ValueError("V2 memory needs a fresh store; legacy memory is not migrated")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._vec_available = self._load_vec()
            if self._vec_available:
                try:
                    with self._transaction():
                        config = self._vector_config()
                        if config["dirty"]:
                            self._reset_vectors(int(config["dimensions"]))
                        else:
                            self._create_vectors(int(config["dimensions"]))
                except sqlite3.Error:
                    self._vec_available = False
                    logger.warning("[memory] v2 vector schema unavailable; using keyword search")
            if embedding_model is not None:
                # Preserve a warmed model's learned dimension on another backend's open.
                configured_dimensions = (
                    int(self._vector_config()["dimensions"]) if dimensions is None else dimensions
                )
                try:
                    self.configure_embeddings(embedding_model, configured_dimensions)
                except sqlite3.Error:
                    self._vec_available = False
                    logger.warning("[memory] v2 vector rebuild unavailable; using keyword search")
                    self.configure_embeddings(embedding_model, configured_dimensions)
        except BaseException:
            self._conn.close()
            raise

    def close(self) -> None:
        self._conn.close()

    def _load_vec(self) -> bool:
        try:
            import sqlite_vec  # type: ignore[import-untyped]

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            return True
        except (ImportError, sqlite3.Error, AttributeError):
            logger.warning("[memory] v2 vectors unavailable; using keyword/entity search")
            return False
        finally:
            try:
                self._conn.enable_load_extension(False)
            except (sqlite3.Error, AttributeError):
                pass

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._conn.in_transaction:
            # Curator publication groups audited mutations and its idempotency
            # marker in one transaction; each rejected candidate can roll back.
            self._conn.execute("SAVEPOINT memory_mutation")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK TO memory_mutation")
                self._conn.execute("RELEASE memory_mutation")
                raise
            else:
                self._conn.execute("RELEASE memory_mutation")
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def curated_batch(
        self, batch_key: str,
        proposals: Sequence[tuple[DurableMemory, str | None, int | None, str]],
        context: ScopeContext,
    ) -> dict[str, int]:
        """Atomic, retry-safe publication; no await or foreign/silent rewrites.

        The workspace watermark is a separate database. If that write fails, a
        retry reads this marker instead of repeating revisions or audit rows.
        """
        with self._transaction():
            row = self._conn.execute(
                "SELECT result_json FROM curation_batches WHERE batch_key=?", (batch_key,),
            ).fetchone()
            if row:
                return json.loads(row[0])  # type: ignore[no-any-return]
            saved, dropped = 0, 0
            for memory, target_id, expected_revision, operation in proposals:
                try:
                    if target_id is not None:
                        old = self.get(target_id, context)
                        if (old is None or old.valid_to is not None
                                or old.scope_key != memory.scope_key
                                or old.provenance != "agent_inferred"
                                or old.revision != expected_revision):
                            dropped += 1
                            continue
                        if operation == "revise":
                            changes = {key: getattr(memory, key) for key in (
                                "content", "topic", "applies_when", "detail", "entities",
                                "provenance", "kind", "source_thread_id", "source_message_id",
                            )}
                            result = self.revise(target_id, changes, context, origin="curator",
                                                 reason="idle curation")
                        elif operation == "supersede":
                            result = self.supersede(target_id, memory, context, origin="curator",
                                                    reason="idle curation")
                        else:
                            dropped += 1
                            continue
                    elif operation == "save":
                        result = self.put(memory, context, origin="curator")
                        if result.id != memory.id:
                            dropped += 1
                            continue
                    else:
                        dropped += 1
                        continue
                    saved += result is not None
                except (ValueError, sqlite3.IntegrityError):
                    dropped += 1
            result_counts = {"saved": saved, "dropped": dropped}
            self._conn.execute(
                "INSERT INTO curation_batches(batch_key,result_json) VALUES(?,?)",
                (batch_key, json.dumps(result_counts)),
            )
            return result_counts

    @staticmethod
    def _entry(row: sqlite3.Row) -> DurableMemory:
        data = {name: row[name] for name in DurableMemory.model_fields}
        data["entities"] = json.loads(row["entities"])
        return DurableMemory.model_validate(data)

    @staticmethod
    def _values(memory: DurableMemory) -> dict[str, object]:
        values: dict[str, object] = memory.model_dump(mode="json")
        values["entities"] = json.dumps(memory.entities)
        values["scope_key"] = memory.scope_key
        values["normalized_content"] = _claim_key(memory.content)
        return values

    def _read(self, memory_id: str, context: ScopeContext) -> DurableMemory | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=? AND scope_key IN (?,?,?)",
            (memory_id, *context.keys),
        ).fetchone()
        return self._entry(row) if row is not None else None

    def get(self, memory_id: str, context: ScopeContext) -> DurableMemory | None:
        """Retired entries are readable; foreign entries look exactly like absent ones."""
        memory = self._read(memory_id, context)
        if memory is not None and memory.superseded_by:
            replacement = self._read(memory.superseded_by, context)
            if replacement is None or replacement.scope_key != memory.scope_key:
                return memory.model_copy(update={"superseded_by": None})
        return memory

    def list_memories(
        self, context: ScopeContext, *, include_retired: bool = False
    ) -> list[DurableMemory]:
        live = "" if include_retired else " AND valid_to IS NULL"
        return [
            self._entry(r)
            for r in self._conn.execute(
                "SELECT * FROM memories WHERE scope_key IN (?,?,?)"
                + live
                + " ORDER BY created_at,id",
                context.keys,
            )
        ]

    def search_lexical(
        self,
        query: str,
        context: ScopeContext,
        *,
        k: int = 30,
    ) -> list[tuple[DurableMemory, float]]:
        if k < 1:
            return []
        match = " OR ".join(f'"{t}"' for t in re.findall(r"\w+", query))
        if not match:
            return []
        rows = self._conn.execute(
            "SELECT m.*, bm25(memories_fts) AS rank FROM memories_fts f "
            "JOIN memories m ON m.id=f.memory_id WHERE memories_fts MATCH ? "
            "AND m.scope_key IN (?,?,?) AND m.valid_to IS NULL ORDER BY rank,m.id LIMIT ?",
            (match, *context.keys, k),
        )
        return [(self._entry(r), float(r["rank"])) for r in rows]

    def search_entities(
        self,
        entities: Sequence[str],
        context: ScopeContext,
    ) -> list[DurableMemory]:
        if not entities:
            return []
        rows = self._conn.execute(
            "SELECT m.* FROM memories m WHERE scope_key IN (?,?,?) AND valid_to IS NULL "
            "AND EXISTS (SELECT 1 FROM json_each(m.entities) e "
            "JOIN json_each(?) wanted ON wanted.value=e.value) ORDER BY m.id",
            (*context.keys, json.dumps(list(entities))),
        )
        return [self._entry(r) for r in rows]

    @staticmethod
    def _validate_write(memory: DurableMemory, context: ScopeContext, origin: WriteOrigin) -> None:
        if not context.permits(memory.scope_kind, memory.scope_id):
            raise ValueError("Memory scope is not available to this conversation")
        if memory.source_thread_id != context.thread_id:
            raise ValueError("Memory evidence must belong to the source conversation")
        if origin not in {"user_requested", "agent_nomination", "curator"}:
            raise ValueError("Unknown memory write origin")
        if memory.scope_kind == ScopeKind.USER and (
            origin != "user_requested" or memory.provenance != "user_said"
        ):
            raise ValueError("User-scope saves require a user request")
        if origin == "curator" and memory.kind == MemoryKind.EPISODIC:
            raise ValueError("The curator cannot save episodic status")

    def _audit(
        self,
        action: str,
        old: DurableMemory | None,
        new: DurableMemory | None,
        reason: str,
    ) -> None:
        if not reason.strip():
            raise ValueError("Memory changes need an audit reason")
        memory = new if new is not None else old
        assert memory is not None
        self._conn.execute(
            "INSERT INTO memory_audit"
            "(memory_id,scope_key,action,old_row,new_row,reason,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                memory.id,
                memory.scope_key,
                action,
                old.model_dump_json() if old else None,
                new.model_dump_json() if new else None,
                reason,
                datetime.now(UTC).isoformat(),
            ),
        )

    def _fts(self, memory: DurableMemory) -> None:
        self._conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory.id,))
        if memory.valid_to is None:
            self._conn.execute(
                "INSERT INTO memories_fts(memory_id,content,topic,applies_when,entities) "
                "VALUES(?,?,?,?,?)",
                (
                    memory.id,
                    memory.content,
                    memory.topic,
                    memory.applies_when,
                    " ".join(memory.entities),
                ),
            )

    def _insert(self, memory: DurableMemory, reason: str) -> DurableMemory:
        existing = self._conn.execute(
            "SELECT * FROM memories WHERE scope_key=? AND normalized_content=? "
            "AND valid_to IS NULL",
            (memory.scope_key, _claim_key(memory.content)),
        ).fetchone()
        if existing is not None:
            return self._entry(existing)
        if memory.valid_to is not None or memory.superseded_by or memory.revision != 1:
            raise ValueError("A new memory must be live, unreplaced and at revision 1")
        memory = memory.model_copy(
            update={
                "embedding_state": "pending",
                "embedding_model": None,
                "embedded_hash": None,
            }
        )
        values = self._values(memory)
        self._conn.execute(
            f"INSERT INTO memories({','.join(values)}) VALUES({','.join('?' for _ in values)})",
            tuple(values.values()),
        )
        self._fts(memory)
        self._audit("save", None, memory, reason)
        return memory

    def put(
        self,
        memory: DurableMemory,
        context: ScopeContext,
        *,
        origin: WriteOrigin,
    ) -> DurableMemory:
        memory = DurableMemory.model_validate(memory.model_dump())
        self._validate_write(memory, context, origin)
        with self._transaction():
            existing = self._insert(memory, origin)
            if (
                existing.id != memory.id
                and origin == "user_requested"
                and memory.provenance == "user_said"
                and existing.provenance != "user_said"
            ):
                confirmed = existing.model_copy(update={
                    "provenance": "user_said",
                    "revision": existing.revision + 1,
                    "source_thread_id": memory.source_thread_id,
                    "source_message_id": memory.source_message_id,
                })
                values = self._values(confirmed)
                self._conn.execute(
                    f"UPDATE memories SET {','.join(f'{key}=?' for key in values)} WHERE id=?",
                    (*values.values(), confirmed.id),
                )
                self._audit("confirm", existing, confirmed, origin)
                return confirmed
            return existing

    def revise(
        self,
        memory_id: str,
        changes: Mapping[str, object],
        context: ScopeContext,
        *,
        origin: WriteOrigin,
        reason: str,
    ) -> DurableMemory | None:
        allowed = {
            "content",
            "topic",
            "applies_when",
            "detail",
            "entities",
            "provenance",
            "kind",
            "source_thread_id",
            "source_message_id",
        }
        if set(changes) - allowed:
            raise ValueError("A revision cannot change identity, scope or lifecycle fields")
        with self._transaction():
            old = self._read(memory_id, context)
            if old is None or old.valid_to is not None:
                return None
            if origin == "curator" and old.provenance == "user_said":
                raise ValueError("The curator cannot revise a user-said memory")
            new = DurableMemory.model_validate(
                {
                    **old.model_dump(),
                    **changes,
                    "revision": old.revision + 1,
                }
            )
            self._validate_write(new, context, origin)
            if embedding_hash(old) != embedding_hash(new):
                new = new.model_copy(
                    update={
                        "embedding_state": "pending",
                        "embedding_model": None,
                        "embedded_hash": None,
                    }
                )
                self._remove_vector(old.id)
            values = self._values(new)
            self._conn.execute(
                "UPDATE memories SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?",
                (*values.values(), memory_id),
            )
            self._fts(new)
            self._audit("rewrite", old, new, reason)
            return new

    def _retire(
        self,
        old: DurableMemory,
        reason: str,
        action: str,
        replacement: str | None = None,
    ) -> None:
        new = old.model_copy(update={"valid_to": datetime.now(UTC), "superseded_by": replacement})
        self._conn.execute(
            "UPDATE memories SET valid_to=?,superseded_by=? WHERE id=?",
            (new.valid_to.isoformat() if new.valid_to else None, replacement, old.id),
        )
        self._fts(new)
        if self._vec_available:
            self._conn.execute("UPDATE memory_vectors SET live=0 WHERE memory_id=?", (old.id,))
        else:
            self._mark_vectors_dirty()
        self._audit(action, old, new, reason)

    def forget(
        self,
        memory_id: str,
        context: ScopeContext,
        *,
        origin: WriteOrigin,
        reason: str,
    ) -> bool:
        if origin != "user_requested":
            raise ValueError("Forgetting requires a user request")
        with self._transaction():
            old = self._read(memory_id, context)
            if old is None or old.valid_to is not None:
                return False
            self._retire(old, reason, "forget")
            return True

    def supersede(
        self,
        memory_id: str,
        replacement: DurableMemory,
        context: ScopeContext,
        *,
        origin: WriteOrigin,
        reason: str,
    ) -> DurableMemory | None:
        replacement = DurableMemory.model_validate(replacement.model_dump())
        self._validate_write(replacement, context, origin)
        with self._transaction():
            old = self._read(memory_id, context)
            if old is None or old.valid_to is not None:
                return None
            if old.scope_key != replacement.scope_key:
                raise ValueError("Supersession requires the same scope")
            if origin == "curator" and old.provenance == "user_said":
                raise ValueError("The curator cannot supersede a user-said memory")
            # Retire first so an unchanged claim can be replaced; the transaction rolls
            # back retirement if publishing the replacement fails.
            self._retire(old, reason, "supersede", replacement.id)
            new = self._insert(replacement, reason)
            if new.id != replacement.id:
                self._conn.execute(
                    "UPDATE memories SET superseded_by=? WHERE id=?", (new.id, old.id)
                )
                # Record the actual deduplicated replacement in the retire snapshot too.
                self._conn.execute(
                    "UPDATE memory_audit SET new_row=? WHERE audit_id=last_insert_rowid()",
                    (
                        old.model_copy(
                            update={"valid_to": datetime.now(UTC), "superseded_by": new.id}
                        ).model_dump_json(),
                    ),
                )
            return new

    def retire_since(
        self,
        source_thread_id: str,
        cutoff: int,
        context: ScopeContext,
        *,
        reason: str,
    ) -> int:
        if source_thread_id != context.thread_id:
            return 0
        with self._transaction():
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE scope_key IN (?,?,?) AND valid_to IS NULL "
                "AND source_thread_id=? AND source_message_id>=?",
                (*context.keys, source_thread_id, cutoff),
            ).fetchall()
            for row in rows:
                self._retire(self._entry(row), reason, "rewind")
            return len(rows)

    def replacement_chain(self, memory_id: str, context: ScopeContext) -> list[DurableMemory]:
        current = self.get(memory_id, context)
        if current is None:
            return []
        visited: set[str] = set()
        while current.id not in visited:
            visited.add(current.id)
            row = self._conn.execute(
                "SELECT * FROM memories WHERE superseded_by=? AND scope_key=? ORDER BY id LIMIT 1",
                (current.id, current.scope_key),
            ).fetchone()
            if row is None or row["id"] in visited:
                break
            current = self._entry(row)
        chain: list[DurableMemory] = []
        visited.clear()
        while current.id not in visited:
            visited.add(current.id)
            chain.append(current)
            following = self.get(current.superseded_by, context) if current.superseded_by else None
            if following is None or following.scope_key != current.scope_key:
                break
            current = following
        return chain

    def audit(self, memory_id: str, context: ScopeContext) -> list[MemoryAudit]:
        return [
            MemoryAudit(
                action=r["action"],
                old=DurableMemory.model_validate_json(r["old_row"]) if r["old_row"] else None,
                new=DurableMemory.model_validate_json(r["new_row"]) if r["new_row"] else None,
                reason=r["reason"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in self._conn.execute(
                "SELECT * FROM memory_audit WHERE memory_id=? AND scope_key IN (?,?,?) "
                "ORDER BY audit_id",
                (memory_id, *context.keys),
            )
        ]

    def _vector_config(self) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM memory_vector_config WHERE singleton=1").fetchone()
        assert isinstance(row, sqlite3.Row)
        return row

    def _create_vectors(self, dimensions: int) -> None:
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors USING vec0("
            "memory_id TEXT PRIMARY KEY, scope_key TEXT PARTITION KEY, live INTEGER, "
            f"embedding float[{dimensions}])"
        )

    def _mark_vectors_dirty(self) -> None:
        self._conn.execute("UPDATE memory_vector_config SET dirty=1 WHERE singleton=1")

    def _reset_vectors(self, dimensions: int) -> None:
        if self._vec_available:
            self._conn.execute("DROP TABLE IF EXISTS memory_vectors")
            self._create_vectors(dimensions)
        self._conn.execute(
            "UPDATE memories SET embedding_state='pending',embedding_model=NULL,embedded_hash=NULL"
        )
        self._conn.execute(
            "UPDATE memory_vector_config SET dirty=? WHERE singleton=1", (not self._vec_available,)
        )

    @property
    def vectors_available(self) -> bool:
        return self._vec_available and not bool(self._vector_config()["dirty"])

    def embedding_configuration(self) -> tuple[str | None, int]:
        row = self._vector_config()
        model = row["model"]
        return model if isinstance(model, str) else None, int(row["dimensions"])

    def configure_embeddings(
        self,
        model: str,
        dimensions: int,
        *,
        expected_model: str | None = None,
    ) -> bool:
        if not model.strip() or not 1 <= dimensions <= 4096:
            raise ValueError("Embedding configuration needs a model and 1–4096 dimensions")
        with self._transaction():
            old = self._vector_config()
            if expected_model is not None and old["model"] != expected_model:
                return False
            if (
                old["model"] != model
                or old["dimensions"] != dimensions
                or (old["dirty"] and self._vec_available)
            ):
                self._reset_vectors(dimensions)
                self._conn.execute(
                    "UPDATE memory_vector_config SET model=?,dimensions=? WHERE singleton=1",
                    (model, dimensions),
                )
            return True

    def pending_embeddings(self, context: ScopeContext, *, limit: int = 100) -> list[DurableMemory]:
        return [
            self._entry(r)
            for r in self._conn.execute(
                "SELECT * FROM memories WHERE scope_key IN (?,?,?) AND valid_to IS NULL "
                "AND embedding_state='pending' ORDER BY id LIMIT ?",
                (*context.keys, max(0, limit)),
            )
        ]

    def _remove_vector(self, memory_id: str) -> None:
        if self._vec_available:
            self._conn.execute("DELETE FROM memory_vectors WHERE memory_id=?", (memory_id,))
        else:
            self._mark_vectors_dirty()

    @staticmethod
    def _vector_bytes(embedding: Sequence[float]) -> bytes:
        if not all(math.isfinite(value) for value in embedding):
            raise ValueError("Embedding values must be finite")
        import sqlite_vec

        return bytes(sqlite_vec.serialize_float32(list(embedding)))

    def publish_embedding(
        self,
        memory_id: str,
        context: ScopeContext,
        embedding: Sequence[float],
        *,
        model: str,
        expected_hash: str,
    ) -> bool:
        """Reject results made stale by edits, retirement or another backend's model change."""
        if not self._vec_available:
            return False
        with self._transaction():
            config = self._vector_config()
            memory = self._read(memory_id, context)
            if (
                memory is None
                or memory.valid_to is not None
                or config["model"] != model
                or config["dirty"]
                or config["dimensions"] != len(embedding)
                or embedding_hash(memory) != expected_hash
            ):
                return False
            self._remove_vector(memory_id)
            self._conn.execute(
                "INSERT INTO memory_vectors(memory_id,scope_key,live,embedding) VALUES(?,?,1,?)",
                (memory_id, memory.scope_key, self._vector_bytes(embedding)),
            )
            self._conn.execute(
                "UPDATE memories SET embedding_state='done',embedding_model=?,embedded_hash=? "
                "WHERE id=?",
                (model, expected_hash, memory_id),
            )
            return True

    def search_semantic(
        self,
        embedding: Sequence[float],
        context: ScopeContext,
        *,
        model: str,
        k: int = 30,
    ) -> list[tuple[DurableMemory, float]]:
        if not self._vec_available or k < 1 or k > 4096:
            return []
        # A read transaction keeps model metadata, vector dimensions and liveness in
        # one snapshot when another backend reconfigures the shared store.
        self._conn.execute("BEGIN")
        try:
            config = self._vector_config()
            if (
                config["model"] != model
                or config["dirty"]
                or config["dimensions"] != len(embedding)
            ):
                return []
            vector = self._vector_bytes(embedding)
            matches: list[tuple[DurableMemory, float]] = []
            for scope in context.keys:
                rows = self._conn.execute(
                    "SELECT m.*,v.distance FROM memory_vectors v JOIN memories m "
                    "ON m.id=v.memory_id WHERE v.embedding MATCH ? AND k=? "
                    "AND v.scope_key=? AND v.live=1 AND m.valid_to IS NULL "
                    "AND m.scope_key=? AND m.embedding_state='done' AND m.embedding_model=?",
                    (vector, k, scope, scope, model),
                )
                matches.extend((self._entry(r), float(r["distance"])) for r in rows)
            return sorted(matches, key=lambda pair: (pair[1], pair[0].id))
        finally:
            self._conn.rollback()
