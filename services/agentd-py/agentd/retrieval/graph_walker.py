"""In-process BFS walker over the indexer snapshot's symbol graph.

Backs the `query_graph` planning/execution tool. The graph itself is already
loaded by the indexer pipeline into `index-snapshot.json` — this module just
provides a thin, LLM-friendly query surface over it:

    walker.query("services/agentd-py/agentd/orchestrator/engine.py:_run_task",
                 depth=1, limit=20, edge_kinds=["Calls", "Implements"])

Returns a `QueryResult` with each neighbour decoded into a human-readable
`(file, symbol, kind, line, edge_kind, direction, distance)` tuple — strips
the absolute paths + node-id syntax the raw snapshot uses, so the model sees
a workspace-relative path + symbol name pair it can directly hand to
`read_file`.

Loading the snapshot is cheap relative to the planner's other costs (~3 MB
JSON parse) and the walker caches it per workspace_root so repeated tool
calls from the same loop don't re-parse.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Public dataclasses ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GraphNode:
    """Snapshot node, decoded to workspace-relative path + symbol info."""

    file: str           # workspace-relative path, e.g. "services/agentd-py/agentd/foo.py"
    symbol: str         # the symbol name, e.g. "_run_task" or "AgentOrchestrator"
    kind: str           # node kind: "File", "Function", "Method", "Class", "Module", "Interface", "Variable"
    line: int           # 1-indexed line of the symbol declaration
    raw_id: str         # the raw snapshot node id — included so the model can re-query if needed


@dataclass(frozen=True)
class GraphNeighbor:
    """One step away from the seed root in the BFS. Used for SYMBOL-seeded
    queries, where the caller wants symbol-level detail (what this function
    calls, who calls it)."""

    node: GraphNode
    edge_kind: str      # "Calls" | "Imports" | "References" | "Inherits" | "Implements"
    direction: str      # "out" — seed → neighbour, "in" — neighbour → seed
    distance: int       # 1 = direct neighbour, 2 = neighbour-of-neighbour, etc.


@dataclass(frozen=True)
class FileNeighbor:
    """A distinct neighbour FILE, aggregated across all symbol edges that
    connect it to the seed. Used for FILE-seeded queries (no `:Symbol`), where
    the caller's question is "which files does this file connect to" — a
    file-level question that a symbol-edge dump answers poorly (it truncates
    and buries the signal). `direction` separates the seed's dependencies
    (out: the seed imports/calls into this file) from its dependents
    (in: this file imports/calls into the seed)."""

    file: str                       # workspace-relative path
    direction: str                  # "out" — seed depends on it; "in" — it depends on seed
    edge_kinds: tuple[str, ...]     # sorted distinct kinds collapsed into this file
    edge_count: int                 # how many symbol-level edges aggregated here


@dataclass(frozen=True)
class QueryResult:
    matched_roots: list[GraphNode]
    # Symbol-seeded queries populate `neighbors` (symbol-level). File-seeded
    # queries populate `file_neighbors` (file-level aggregation) and leave
    # `neighbors` empty. Exactly one is non-empty for a successful query.
    neighbors: list[GraphNeighbor]
    truncated: bool
    stats: dict[str, int]
    file_neighbors: list[FileNeighbor] = field(default_factory=list)


def _root_priority(node: dict[str, object]) -> tuple[int, str]:
    """Order roots so the File-kind node is processed first. File nodes carry
    cross-file `Imports` edges (workspace → external symbol → workspace) that
    no per-symbol node has, and surface the densest cross-file information
    per BFS step. Within the same kind, fall back to alphabetical for
    determinism."""
    kind = str(node.get("kind", ""))
    rank = 0 if kind == "File" else 1
    return rank, str(node.get("id", ""))


def _coerce_int(value: object, default: int) -> int:
    """Best-effort int coercion. Returns `default` on `None`, on non-numeric
    strings, or on any other type that can't round-trip through `int(...)`.
    Used so a tool call with `depth="medium"` or `limit=null` doesn't crash
    the planning loop — the walker clamps the result anyway."""
    if value is None:
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class GraphWalkerSnapshotError(RuntimeError):
    """Raised when the symbol-graph snapshot can't be loaded (missing,
    truncated, malformed). Callers translate this into a tool-output error
    rather than letting it propagate through the planning loop."""


# ── Walker ────────────────────────────────────────────────────────────────────

_ALLOWED_EDGE_KINDS = {"Calls", "Imports", "References", "Inherits", "Implements"}
_DEFAULT_DEPTH = 1
_DEFAULT_LIMIT = 20
_MAX_DEPTH = 3
_MAX_LIMIT = 60
# Safety bound on raw symbol edges walked during a file-seeded aggregation.
# A file with hundreds of symbols each with ~10 edges stays well under this;
# the cap only fires on pathological graphs and sets `truncated`.
_INTERNAL_RAW_CAP = 5000


class GraphWalker:
    """BFS walker over a single snapshot. Lazy-loaded; thread-safe."""

    def __init__(self, snapshot_path: Path, workspace_root: Path) -> None:
        self._snapshot_path = snapshot_path
        self._workspace_root = workspace_root.resolve()
        self._lock = threading.Lock()
        self._loaded_at_mtime_ns: int | None = None
        self._nodes_by_id: dict[str, dict[str, Any]] = {}
        # Edge indexes: outbound[from_id] → list[(to_id, kind)], inbound[to_id] → list[(from_id, kind)].
        self._outbound: dict[str, list[tuple[str, str]]] = {}
        self._inbound: dict[str, list[tuple[str, str]]] = {}
        # File-path → list[node_id] index (built from nodes_by_id paths). Cached
        # to keep `resolve_root` fast even on repeated calls.
        self._nodes_by_file: dict[str, list[str]] = {}

    # ── Public API ────────────────────────────────────────────────────────

    def query(
        self,
        node: str,
        depth: int = _DEFAULT_DEPTH,
        limit: int = _DEFAULT_LIMIT,
        edge_kinds: list[str] | None = None,
    ) -> QueryResult:
        """BFS expand from `node` and return decoded neighbours.

        `node` accepts two forms:
          - "path/to/file.py"          — root every node in that file.
                                          Intra-file neighbours are SKIPPED:
                                          the caller already has the file, so
                                          the cap is reserved for cross-file
                                          edges they actually need to follow.
          - "path/to/file.py:Symbol"   — root only the matching symbol(s).
                                          Intra-file neighbours INCLUDED so
                                          methods/fields of a class are
                                          visible.
        """
        # Coerce defensively. The LLM occasionally sends `depth=null` or a
        # string like "medium"; we don't want a malformed tool arg to crash
        # the planning loop.
        depth = max(1, min(_coerce_int(depth, _DEFAULT_DEPTH), _MAX_DEPTH))
        limit = max(1, min(_coerce_int(limit, _DEFAULT_LIMIT), _MAX_LIMIT))
        kinds_filter = self._normalize_edge_kinds(edge_kinds)

        # Hold the lock across the entire BFS so a concurrent reload doesn't
        # swap the index maps out from under us. The lock is contended only
        # on the rare reload path; query-after-load takes it for the duration
        # of the walk but never blocks on I/O.
        with self._lock:
            self._ensure_loaded_locked()
            return self._query_locked(node, depth, limit, kinds_filter)

    def _query_locked(
        self,
        node: str,
        depth: int,
        limit: int,
        kinds_filter: set[str],
    ) -> QueryResult:
        symbol_seed = ":" in node
        roots = self._resolve_roots(node)
        if not roots:
            return QueryResult(matched_roots=[], neighbors=[], truncated=False, stats={
                "root_count": 0, "neighbor_count": 0, "depth": depth, "limit": limit,
            })

        matched_roots: list[GraphNode] = [
            decoded for decoded in (self._decode_node(n) for n in roots) if decoded is not None
        ]

        if symbol_seed:
            return self._query_symbol_seed(roots, matched_roots, depth, limit, kinds_filter)
        return self._query_file_seed(roots, matched_roots, depth, limit, kinds_filter)

    def _query_symbol_seed(
        self,
        roots: list[dict[str, Any]],
        matched_roots: list[GraphNode],
        depth: int,
        limit: int,
        kinds_filter: set[str],
    ) -> QueryResult:
        """Symbol-level walk: the caller asked for a specific symbol and wants
        to know exactly what it calls / who calls it. Intra-file neighbours are
        kept (a class's methods, sibling functions) since those are part of
        understanding the symbol."""
        seen_node_ids: set[str] = set(n["id"] for n in roots)
        ordered: list[GraphNeighbor] = []
        truncated = False
        queue: deque[tuple[str, int]] = deque((n["id"], 0) for n in roots)

        while queue:
            current_id, distance = queue.popleft()
            if distance >= depth:
                continue
            next_distance = distance + 1
            for neighbor_id, kind in self._outbound.get(current_id, ()):
                if kind not in kinds_filter:
                    continue
                if not self._emit_neighbor(
                    neighbor_id, kind, "out", next_distance, seen_node_ids, ordered, limit
                ):
                    truncated = True
                    queue.clear()
                    break
                if next_distance < depth:
                    queue.append((neighbor_id, next_distance))
            else:
                for neighbor_id, kind in self._inbound.get(current_id, ()):
                    if kind not in kinds_filter:
                        continue
                    if not self._emit_neighbor(
                        neighbor_id, kind, "in", next_distance, seen_node_ids, ordered, limit
                    ):
                        truncated = True
                        queue.clear()
                        break
                    if next_distance < depth:
                        queue.append((neighbor_id, next_distance))

        ordered.sort(key=lambda n: (n.edge_kind, n.node.file, n.node.symbol))
        return QueryResult(
            matched_roots=matched_roots,
            neighbors=ordered,
            truncated=truncated,
            stats={
                "root_count": len(matched_roots),
                "neighbor_count": len(ordered),
                "depth": depth,
                "limit": limit,
            },
        )

    def _query_file_seed(
        self,
        roots: list[dict[str, Any]],
        matched_roots: list[GraphNode],
        depth: int,
        limit: int,
        kinds_filter: set[str],
    ) -> QueryResult:
        """File-level walk: the caller seeded a whole file, so the question is
        "which files does this file connect to". A symbol-edge dump answers
        that poorly (it truncates at the limit and buries the file signal in
        noise). Instead we aggregate every symbol edge to its host file,
        keyed by direction — `out` = the seed depends on the file (imports /
        calls into it), `in` = the file depends on the seed.

        File-first root ordering is preserved so the dense File-node `Imports`
        edges are walked before per-symbol noise if we hit the internal raw
        cap on a pathologically connected file."""
        roots = sorted(roots, key=_root_priority)
        seed_files: set[str] = {
            str(r.get("path")) for r in roots if isinstance(r.get("path"), str)
        }

        # (rel_file, direction) → {kinds, count}
        agg: dict[tuple[str, str], dict[str, Any]] = {}
        seen_node_ids: set[str] = set(n["id"] for n in roots)
        raw_walked = 0
        hit_internal_cap = False
        queue: deque[tuple[str, int]] = deque((n["id"], 0) for n in roots)

        while queue and not hit_internal_cap:
            current_id, distance = queue.popleft()
            if distance >= depth:
                continue
            next_distance = distance + 1
            for adjacency, direction in (
                (self._outbound, "out"),
                (self._inbound, "in"),
            ):
                for neighbor_id, kind in adjacency.get(current_id, ()):
                    if kind not in kinds_filter:
                        continue
                    if neighbor_id.startswith("external:"):
                        continue
                    nbr = self._nodes_by_id.get(neighbor_id)
                    if nbr is None:
                        continue
                    path = nbr.get("path")
                    if not isinstance(path, str) or path in seed_files:
                        # Skip the seed file's own nodes — a file's internal
                        # structure isn't what "which files connect to this
                        # file" asks for; read_file covers that.
                        continue
                    rel = self._workspace_relative(path)
                    if rel is None:
                        continue
                    slot = agg.setdefault((rel, direction), {"kinds": set(), "count": 0})
                    slot["kinds"].add(kind)
                    slot["count"] += 1
                    raw_walked += 1
                    # Continue the walk through this neighbour for depth>1.
                    if next_distance < depth and neighbor_id not in seen_node_ids:
                        seen_node_ids.add(neighbor_id)
                        queue.append((neighbor_id, next_distance))
                    if raw_walked >= _INTERNAL_RAW_CAP:
                        hit_internal_cap = True
                        queue.clear()
                        break
                if hit_internal_cap:
                    break

        rows = [
            FileNeighbor(
                file=rel,
                direction=direction,
                edge_kinds=tuple(sorted(slot["kinds"])),
                edge_count=int(slot["count"]),
            )
            for (rel, direction), slot in agg.items()
        ]
        # `out` (dependencies) before `in` (dependents); then alphabetical.
        rows.sort(key=lambda fn: (0 if fn.direction == "out" else 1, fn.file))
        truncated = hit_internal_cap or len(rows) > limit
        rows = rows[:limit]

        return QueryResult(
            matched_roots=matched_roots,
            neighbors=[],
            file_neighbors=rows,
            truncated=truncated,
            stats={
                "root_count": len(matched_roots),
                "neighbor_count": len(rows),
                "depth": depth,
                "limit": limit,
            },
        )

    # ── Internals ─────────────────────────────────────────────────────────

    def _ensure_loaded_locked(self) -> None:
        """Load the snapshot if it has changed since last load. Cheap NOOP
        when nothing's moved. Caller MUST hold `self._lock`."""
        try:
            mtime_ns = self._snapshot_path.stat().st_mtime_ns
        except (FileNotFoundError, NotADirectoryError):
            if self._loaded_at_mtime_ns is None:
                raise
            # Snapshot disappeared after first load — keep what we have.
            return
        if self._loaded_at_mtime_ns == mtime_ns and self._nodes_by_id:
            return

        try:
            with self._snapshot_path.open(encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            # Snapshot is mid-rewrite or otherwise garbled. Don't blow up the
            # planning loop — keep any existing state and raise a typed
            # error the tool layer translates to a friendly message. First-
            # load failures still propagate (caller has no cached state to
            # serve), but with a clearer exception than raw JSONDecodeError.
            if self._loaded_at_mtime_ns is None:
                raise GraphWalkerSnapshotError(
                    f"snapshot JSON is malformed: {exc.msg}"
                ) from exc
            return

        graph = payload.get("graph", {}) if isinstance(payload, dict) else {}
        nodes = graph.get("nodes", []) or []
        edges = graph.get("edges", []) or []

        self._nodes_by_id = {
            node["id"]: node for node in nodes if isinstance(node, dict) and "id" in node
        }

        outbound: dict[str, list[tuple[str, str]]] = {}
        inbound: dict[str, list[tuple[str, str]]] = {}
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            src = edge.get("from")
            dst = edge.get("to")
            kind = edge.get("kind")
            if not (isinstance(src, str) and isinstance(dst, str) and isinstance(kind, str)):
                continue
            outbound.setdefault(src, []).append((dst, kind))
            inbound.setdefault(dst, []).append((src, kind))
        self._outbound = outbound
        self._inbound = inbound

        nodes_by_file: dict[str, list[str]] = {}
        for node_id, node in self._nodes_by_id.items():
            rel = self._workspace_relative(node.get("path"))
            if rel is None:
                continue
            nodes_by_file.setdefault(rel, []).append(node_id)
        self._nodes_by_file = nodes_by_file

        self._loaded_at_mtime_ns = mtime_ns

    def _resolve_roots(self, node: str) -> list[dict[str, Any]]:
        """Parse the `node` argument and return matching snapshot node dicts."""
        if ":" in node:
            file_part, symbol_part = node.rsplit(":", 1)
        else:
            file_part, symbol_part = node, None
        file_part = file_part.strip().lstrip("./")
        symbol_part = symbol_part.strip() if symbol_part else None

        candidate_ids = self._nodes_by_file.get(file_part, [])
        if not candidate_ids:
            return []

        if symbol_part is None:
            # Every node in the file.
            return [self._nodes_by_id[nid] for nid in candidate_ids]

        # Filter by symbol name (case-sensitive — matches the snapshot's
        # `name` field directly).
        matches: list[dict[str, Any]] = []
        for nid in candidate_ids:
            n = self._nodes_by_id[nid]
            if n.get("name") == symbol_part:
                matches.append(n)
        return matches

    def _emit_neighbor(
        self,
        neighbor_id: str,
        kind: str,
        direction: str,
        distance: int,
        seen_ids: set[str],
        out: list[GraphNeighbor],
        limit: int,
    ) -> bool:
        """Append a symbol-level neighbour if we haven't seen it. Returns False
        when the limit is hit (signals the BFS to stop). Used only by the
        symbol-seeded path — file-seeded queries aggregate to files instead and
        never call this."""
        if neighbor_id in seen_ids:
            return True
        node = self._nodes_by_id.get(neighbor_id)
        if node is None:
            return True
        # Skip external markers (`external:call:foo`, `external:module:bar`).
        # These don't have a `path` we can route the model to.
        if neighbor_id.startswith("external:") or not isinstance(node.get("path"), str):
            seen_ids.add(neighbor_id)
            return True
        decoded = self._decode_node(node)
        if decoded is None:
            seen_ids.add(neighbor_id)
            return True
        out.append(GraphNeighbor(node=decoded, edge_kind=kind, direction=direction, distance=distance))
        seen_ids.add(neighbor_id)
        return len(out) < limit

    def _decode_node(self, node: dict[str, Any]) -> GraphNode | None:
        path = self._workspace_relative(node.get("path"))
        if path is None:
            return None
        return GraphNode(
            file=path,
            symbol=str(node.get("name", "")),
            kind=str(node.get("kind", "")),
            line=int(node.get("line", 1)) if isinstance(node.get("line"), int) else 1,
            raw_id=str(node.get("id", "")),
        )

    def _workspace_relative(self, raw: object) -> str | None:
        if not isinstance(raw, str) or not raw:
            return None
        try:
            return str(Path(raw).resolve().relative_to(self._workspace_root))
        except (ValueError, OSError):
            return None

    @staticmethod
    def _normalize_edge_kinds(kinds: list[str] | None) -> set[str]:
        if not kinds:
            return set(_ALLOWED_EDGE_KINDS)
        normalized: set[str] = set()
        for raw in kinds:
            if not isinstance(raw, str):
                continue
            cap = raw.strip().capitalize()
            if cap in _ALLOWED_EDGE_KINDS:
                normalized.add(cap)
        return normalized or set(_ALLOWED_EDGE_KINDS)
