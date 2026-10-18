from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from agentd.child_env import child_env
from agentd.domain.models import (
    Diagnostic,
    PlanEvidenceFile,
    PlanEvidencePack,
    PlanEvidenceSymbol,
)
from agentd.local_models import semantic_ready
from agentd.retrieval.chunker import ScoredChunk
from agentd.retrieval.discovery_cache import DiscoverySnapshotCache, snapshot_version
from agentd.runtime.adapters import EvidenceAdapter, GenericEvidenceAdapter

# Whole words only: a substring match let "latest", "inspect" and "specific" pull
# test files back into discovery results that never asked for them.
_TEST_REQUEST = re.compile(
    r"\b(?:tests?|testing|tested|specs?|pytest|unittest|vitest|jest|fixtures?)\b")


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


@dataclass(frozen=True)
class RetrievalContext:
    repository_structure: list[str] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    related_symbols: list[str] = field(default_factory=list)
    graph_neighbors: list[str] = field(default_factory=list)
    # Workspace-relative file paths reached from the matched seed by one Calls/Imports/References/Inherits
    # hop, dedupe by file, seed-files removed, cap 20. The Rust indexer's edges encode actual structural
    # connections (e.g. `engine.py:_run_task → state_machine.py:transition` via Calls + Imports), so this
    # surfaces neighboring files the semantic top-K may not have matched on. Read but never wrote before.
    graph_neighbor_files: list[str] = field(default_factory=list)
    # Bounded file-level structural edges for the chat discovery seed. The
    # planner's existing serialization contract remains unchanged.
    graph_relationships: list[dict[str, str]] = field(default_factory=list)
    discovery_candidates: list[dict[str, object]] = field(default_factory=list)
    file_outlines: dict[str, list[str]] = field(default_factory=dict)
    diagnostics_excerpt: list[str] = field(default_factory=list)
    snapshot_age_sec: float | None = None
    snapshot_stats: dict[str, int] = field(
        default_factory=lambda: {"node_count": 0, "edge_count": 0, "diagnostic_count": 0}
    )
    file_contents: dict[str, str] = field(default_factory=dict)
    planner_evidence: PlanEvidencePack = field(default_factory=PlanEvidencePack)
    # Semantic chunks from the vector index — used by engine for chunk-scoped patch context.
    # Not serialised into the LLM payload; evidence_files in planner_evidence carries them.
    semantic_chunks: list[ScoredChunk] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "RetrievalContext":
        return cls(
            repository_structure=[],
            related_files=[],
            related_symbols=[],
            graph_neighbors=[],
            graph_neighbor_files=[],
            file_outlines={},
            file_contents={},
            planner_evidence=PlanEvidencePack(),
            diagnostics_excerpt=[],
            snapshot_age_sec=None,
            snapshot_stats={"node_count": 0, "edge_count": 0, "diagnostic_count": 0},
            semantic_chunks=[],
        )

    def as_prompt_payload(self) -> dict[str, object]:
        pe = self.planner_evidence
        # Serialize only the fields the LLM can act on; strip internal bookkeeping.
        if hasattr(pe, "evidence_files"):
            planner_evidence: dict[str, object] = {
                "evidence_files": [
                    f.model_dump(mode="json") if hasattr(f, "model_dump") else f
                    for f in pe.evidence_files
                ],
                "evidence_symbols": [
                    s.model_dump(mode="json") if hasattr(s, "model_dump") else s
                    for s in pe.evidence_symbols
                ],
                "confidence_notes": pe.confidence_notes,
            }
        else:
            # Already a plain dict (e.g. from test stubs)
            planner_evidence = dict(pe)  # type: ignore[arg-type]
        # Only surface diagnostics that are actionable errors — skip import-resolution
        # noise and other warnings that don't inform planning.
        error_diagnostics = [
            d for d in self.diagnostics_excerpt
            if ": error" in d.lower() or d.strip().startswith("error")
        ]
        return {
            "repository_structure": self.repository_structure,
            "file_outlines": self.file_outlines,
            "file_contents": self.file_contents,
            "planner_evidence": planner_evidence,
            "diagnostics_excerpt": error_diagnostics,
            "snapshot_stats": self.snapshot_stats,
            # Cross-file neighbours of the seed symbols/files reached via one
            # Calls/Imports/References/Inherits hop in the symbol graph. Use
            # to extend exploration beyond the semantic top-K — files here are
            # structurally connected to the goal's matched symbols even when
            # their text doesn't surface in semantic search.
            "graph_neighbor_files": self.graph_neighbor_files,
        }


@dataclass
class _DiscoveryGraph:
    nodes: list[dict[str, object]]
    edges: list[dict[str, object]]
    by_id: dict[str, dict[str, object]]
    by_path: dict[str, list[str]]
    incident: dict[str, list[int]]

    def edges_for(self, ids: set[str]) -> list[dict[str, object]]:
        # Preserve snapshot order, including parallel edges and first-edge choices.
        indexes = {i for node_id in ids for i in self.incident.get(node_id, [])}
        return [self.edges[i] for i in sorted(indexes)]


@dataclass
class _SnapshotPathCache:
    """Goal-independent results for one snapshot; discovery retains a bounded graph view."""

    key: tuple[object, ...]
    normalized_by_raw: dict[str, str | None] = field(default_factory=dict)
    repository_structure: list[str] | None = None
    discovery: _DiscoveryGraph | None = None


def _git_visible_files(workspace_root: Path) -> set[str] | None:
    """Workspace-relative paths git does not ignore (tracked + untracked), or None when the
    workspace is not a git repo or git fails — callers then fall back to the fixed list."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace_root), "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, timeout=10, check=False, env=child_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return {p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p}


def _ancestor_dirs(files: set[str]) -> set[str]:
    dirs: set[str] = set()
    for rel in files:
        parent = Path(rel).parent
        while parent.as_posix() != "." and parent.as_posix() not in dirs:
            dirs.add(parent.as_posix())
            parent = parent.parent
    return dirs


class RetrievalArtifactClient:
    _IGNORED_CONTEXT_DIRS = {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "target",
        "dist",
        ".crucible/state",
        ".crucible",
        ".tmp",
    }

    def __init__(
        self,
        *,
        snapshot_path_template: str | None = None,
        max_age_sec: int = 900,
        index_command_template: str | None = None,
        index_timeout_sec: int = 120,
        evidence_adapter: EvidenceAdapter | None = None,
        semantic_index: object = None,  # SemanticIndex | None — typed as object to avoid hard dep
    ) -> None:
        self._snapshot_path_template = snapshot_path_template
        self._max_age_sec = max_age_sec
        self._index_command_template = index_command_template
        self._index_timeout_sec = index_timeout_sec
        self._evidence_adapter = evidence_adapter or GenericEvidenceAdapter()
        self._semantic_index = semantic_index  # None when semantic retrieval is disabled
        self._last_indexed_snapshot_ms: int = 0  # snapshot generation ms at last index build
        self._building: bool = False
        # Serializes re-embeds: the watch loop can fire several /v1/index/build calls in quick
        # succession (multiple FS events per edit). A request that arrives during a build sets
        # _rebuild_requested and the running build goes once more, so the snapshot written
        # mid-build is still indexed (it used to be dropped until the next edit).
        self._build_lock = threading.Lock()
        self._rebuild_requested = False
        self._path_cache: _SnapshotPathCache | None = None
        self._discovery_path_cache: _SnapshotPathCache | None = None
        self._discovery_prepare_lock = threading.RLock()
        self._discovery_warm_lock = threading.Lock()
        self._discovery_warm_status: dict[str, object] = {"state": "cold", "warmup_count": 0}
        self._discovery_warm_version: object = None
        self._discovery_workspace: str | None = None
        self._discovery_snapshot = DiscoverySnapshotCache()

    @classmethod
    def from_env(
        cls,
        *,
        evidence_adapter: EvidenceAdapter | None = None,
        semantic_index: object = None,
    ) -> "RetrievalArtifactClient":
        return cls(
            snapshot_path_template=os.getenv("CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH"),
            max_age_sec=int(os.getenv("CRUCIBLE_RETRIEVAL_MAX_AGE_SEC", "900")),
            index_command_template=os.getenv("CRUCIBLE_INDEXER_INDEX_CMD"),
            index_timeout_sec=int(os.getenv("CRUCIBLE_INDEXER_INDEX_TIMEOUT_SEC", "120")),
            evidence_adapter=evidence_adapter,
            semantic_index=semantic_index,
        )

    def semantic_enabled(self) -> bool:
        return semantic_ready(self._semantic_index)

    def snapshot_version(self, workspace_path: str) -> object:
        try:
            return snapshot_version(self._resolve_snapshot_path(workspace_path))
        except OSError:
            return None

    def trigger_index_build(self, workspace_path: str) -> object | None:
        """Warm discovery metadata and build/update the optional semantic index.

        Returns the IndexStats from build_or_update, or None if semantic retrieval
        is disabled, the snapshot doesn't exist, or another build is running (that
        build then runs once more for this request).
        """
        self.warm_discovery(workspace_path)
        if not self.semantic_enabled():
            return None
        stats: object | None = None
        while True:
            self._rebuild_requested = True
            if not self._build_lock.acquire(blocking=False):
                return None  # the running build sees the request after it finishes
            try:
                self._rebuild_requested = False
                self._building = True
                stats = self._build_semantic_index_once(workspace_path)
            finally:
                self._building = False
                self._build_lock.release()
            if not self._rebuild_requested:
                return stats

    def _build_semantic_index_once(self, workspace_path: str) -> object | None:
        snapshot_path = self._resolve_snapshot_path(workspace_path)
        try:
            if not snapshot_path.exists():
                # The graph panel's Build-index CTA fires with no snapshot on disk —
                # run the auto-index command first so there is something to embed.
                self._attempt_build_snapshot(workspace_path, snapshot_path)
                if not snapshot_path.exists():
                    return None
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            stats = self._semantic_index.build_or_update(workspace_path, payload)  # type: ignore[union-attr]
            snapshot_ms = int(payload.get("generated_at_ms", 0) or 0)
            if snapshot_ms > self._last_indexed_snapshot_ms:
                self._last_indexed_snapshot_ms = snapshot_ms
            self._fill_path_cache(payload, workspace_path)
            self.warm_discovery(workspace_path)
            return stats
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("trigger_index_build failed: %s", exc)
            return None

    def _fill_path_cache(self, payload: dict[str, object], workspace_path: str) -> None:
        """Run the goal-independent work for this snapshot now, on the build's background
        thread, so the first chat after an edit finds it cached."""
        try:
            self._build_context(payload, "", None, workspace_path)
        except Exception:  # noqa: BLE001 — best-effort: the chat path computes it otherwise
            import logging
            logging.getLogger(__name__).debug("retrieval path cache fill failed", exc_info=True)

    def _request_background_build(self, workspace_path: str) -> None:
        """Keep index builds off the chat path: run one on a daemon thread, or, when one is
        already running, ask it to go once more."""
        if self._build_lock.locked():
            self._rebuild_requested = True
            return
        threading.Thread(target=self.trigger_index_build, args=(workspace_path,),
                         name="semantic-index-build", daemon=True).start()

    def index_status(self) -> dict[str, object]:
        return {
            "semantic_enabled": self.semantic_enabled(),
            "building": self._building or self._discovery_warm_status.get("state") == "warming",
            "last_indexed_snapshot_ms": self._last_indexed_snapshot_ms,
            "discovery": self.discovery_status(),
        }

    def discovery_status(self) -> dict[str, object]:
        status = dict(self._discovery_warm_status)
        if status.get("state") == "ready" and self._discovery_workspace:
            try:
                current = snapshot_version(self._resolve_snapshot_path(self._discovery_workspace))
            except OSError:
                current = None
            if current != self._discovery_warm_version:
                status["state"] = "pending"
        return status

    def warm_discovery(self, workspace_path: str) -> dict[str, object]:
        """Prepare only versioned metadata/lookups; no ranking, source reads or model calls."""
        with self._discovery_warm_lock:
            started = time.perf_counter()
            previous = self._discovery_warm_status
            warmup_count = _coerce_int(previous.get("warmup_count"), 0)
            self._discovery_workspace = workspace_path
            self._discovery_warm_status = {**previous, "state": "warming"}
            path = self._resolve_snapshot_path(workspace_path)
            try:
                payload = self._discovery_snapshot.load(path)
                version = payload.get("_discovery_cache_key")
                if version is None:
                    with self._discovery_prepare_lock:
                        self._discard_outdated_discovery(path)
                    self._discovery_warm_version = None
                    self._discovery_warm_status = {"state": "uncacheable",
                                                   "warmup_count": warmup_count}
                    return self.discovery_status()
                workspace_root = Path(workspace_path).resolve()
                raw_root = payload.get("workspace_root")
                snapshot_root = (Path(raw_root).expanduser().resolve()
                                 if isinstance(raw_root, str) and raw_root.strip() else workspace_root)
                cache = self._path_cache_for(payload, workspace_root, snapshot_root)
                graph = self._prepare_discovery_graph(payload, workspace_root, snapshot_root, cache)
                if snapshot_version(path) != version:
                    raise OSError("Snapshot changed during discovery warmup")
                changed = self._discovery_warm_version != version
                self._discovery_warm_version = version
                self._discovery_warm_status = {
                    "state": "ready", "warmup_count": warmup_count + int(changed),
                    "last_warmup_ms": (round((time.perf_counter() - started) * 1000, 2)
                                       if changed else previous.get("last_warmup_ms", 0)),
                    "node_count": len(graph.nodes), "edge_count": len(graph.edges),
                    "generated_at_ms": payload.get("generated_at_ms"),
                }
            except (OSError, ValueError) as exc:
                with self._discovery_prepare_lock:
                    self._discard_outdated_discovery(path)
                self._discovery_warm_version = None
                self._discovery_warm_status = {
                    "state": "missing" if isinstance(exc, FileNotFoundError) else "unavailable",
                    "warmup_count": warmup_count, "error_type": type(exc).__name__,
                }
            return self.discovery_status()

    def _discard_outdated_discovery(self, path: Path) -> None:
        # A query may already have prepared a newer revision while this warmup failed.
        try:
            current = snapshot_version(path)
        except OSError:
            current = None
        cache = self._discovery_path_cache
        if cache is not None and cache.key[-1] != current:
            self._discovery_path_cache = None

    def _prepare_discovery_graph(
        self, payload: dict[str, object], workspace_root: Path, snapshot_root: Path,
        cache: _SnapshotPathCache,
    ) -> _DiscoveryGraph:
        with self._discovery_prepare_lock:
            if cache.discovery is not None:
                return cache.discovery
            graph = payload.get("graph", {})
            if not isinstance(graph, dict):
                raise ValueError("Discovery graph must be an object")
            raw_nodes = graph.get("nodes", [])
            edges = [e for e in graph.get("edges", []) if isinstance(e, dict)]
            nodes: list[dict[str, object]] = []
            for node in raw_nodes:
                if not isinstance(node, dict):
                    continue
                raw = node.get("path")
                if isinstance(raw, str) and raw in cache.normalized_by_raw:
                    path = cache.normalized_by_raw[raw]
                else:
                    path = self._normalize_snapshot_path(
                        raw_path=raw, workspace_root=workspace_root,
                        snapshot_workspace_root=snapshot_root)
                    if isinstance(raw, str):
                        cache.normalized_by_raw[raw] = path
                if path is not None:
                    nodes.append({**node, "path": path})
            by_id = {str(n["id"]): n for n in nodes if isinstance(n.get("id"), str)}
            by_path: dict[str, list[str]] = {}
            for node_id, node in by_id.items():
                by_path.setdefault(str(node["path"]), []).append(node_id)
            incident: dict[str, list[int]] = {}
            for i, edge in enumerate(edges):
                source, target = edge.get("from"), edge.get("to")
                if isinstance(source, str):
                    incident.setdefault(source, []).append(i)
                if isinstance(target, str) and target != source:
                    incident.setdefault(target, []).append(i)
            cache.discovery = _DiscoveryGraph(nodes, edges, by_id, by_path, incident)
            return cache.discovery

    def load_context(
        self,
        workspace_path: str,
        goal: str,
        *,
        discovery_only: bool = False,
        current_query: str | None = None,
        allow_build: bool = True,
    ) -> tuple[RetrievalContext, list[Diagnostic]]:
        diagnostics: list[Diagnostic] = []
        snapshot_path = self._resolve_snapshot_path(workspace_path)

        if allow_build and not snapshot_path.exists():
            diagnostics.extend(self._attempt_build_snapshot(workspace_path, snapshot_path))

        if not snapshot_path.exists():
            diagnostics.append(
                Diagnostic(
                    source="retrieval",
                    message=(
                        "Retrieval snapshot is unavailable; continuing without retrieval context "
                        f"({snapshot_path})"
                    ),
                    level="warning",
                )
            )
            return RetrievalContext.empty(), diagnostics

        try:
            payload = (self._discovery_snapshot.load(snapshot_path) if discovery_only
                       else json.loads(snapshot_path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            diagnostics.append(
                Diagnostic(
                    source="retrieval",
                    message=(
                        "Retrieval snapshot could not be parsed; continuing without retrieval context "
                        f"({exc})"
                    ),
                    level="warning",
                )
            )
            return RetrievalContext.empty(), diagnostics

        age_sec = self._compute_age_sec(payload)
        if age_sec is not None and age_sec > self._max_age_sec:
            diagnostics.append(
                Diagnostic(
                    source="retrieval",
                    message=(
                        "Retrieval snapshot is stale "
                        f"({age_sec:.1f}s old > {self._max_age_sec}s); continuing with stale context"
                    ),
                    level="warning",
                )
            )

        # Never build the semantic index here: this is the chat/task path, and a build reads
        # and chunks the workspace (seconds on a large repo). Query whatever index exists and
        # request a background build when the snapshot is newer than what was indexed.
        if self.semantic_enabled():
            index = self._semantic_index
            if not index.is_ready():  # type: ignore[attr-defined]
                index.open_existing(workspace_path)  # type: ignore[attr-defined]
            snapshot_ms = int(payload.get("generated_at_ms", 0) or 0)
            if snapshot_ms > self._last_indexed_snapshot_ms or not index.is_ready():  # type: ignore[attr-defined]
                self._request_background_build(workspace_path)

        context = self._build_context(
            payload, goal, age_sec, workspace_path,
            discovery_only=discovery_only, current_query=current_query,
        )

        return context, diagnostics

    def _resolve_snapshot_path(self, workspace_path: str) -> Path:
        workspace = Path(workspace_path).resolve()
        if self._snapshot_path_template:
            rendered = self._snapshot_path_template.format(
                workspace=str(workspace),
                snapshot_path=str(workspace / ".crucible/index-snapshot.json"),
            )
            return Path(rendered).expanduser().resolve()
        return (workspace / ".crucible/index-snapshot.json").resolve()

    def _attempt_build_snapshot(self, workspace_path: str, snapshot_path: Path) -> list[Diagnostic]:
        command = self._render_index_command(workspace_path, snapshot_path)
        if not command:
            return [
                Diagnostic(
                    source="retrieval",
                    message=(
                        "Retrieval snapshot missing and no index command is configured or auto-detected; "
                        "skipping auto-index"
                    ),
                    level="warning",
                )
            ]

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._index_timeout_sec,
                check=False,
                env=child_env(),
            )
        except subprocess.TimeoutExpired:
            return [
                Diagnostic(
                    source="retrieval",
                    message=(
                        "Auto-index command timed out after "
                        f"{self._index_timeout_sec}s: {command}"
                    ),
                    level="warning",
                )
            ]
        except OSError as exc:
            return [
                Diagnostic(
                    source="retrieval",
                    message=f"Auto-index command could not be launched: {exc}",
                    level="warning",
                )
            ]

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            return [
                Diagnostic(
                    source="retrieval",
                    message=(
                        f"Auto-index command failed with exit code {result.returncode}: {command}"
                        + (f" | stderr: {stderr}" if stderr else "")
                    ),
                    level="warning",
                )
            ]

        return []

    def _render_index_command(self, workspace_path: str, snapshot_path: Path) -> str | None:
        workspace = str(Path(workspace_path).resolve())
        if self._index_command_template:
            return self._index_command_template.format(
                workspace=workspace,
                snapshot_path=str(snapshot_path),
            )

        auto_indexer = shutil.which("crucible-indexer")
        if not auto_indexer:
            return None

        # This is the LAST-RESORT fallback: agentd shells out synchronously
        # (subprocess.run with a timeout) when a task needs context but no
        # snapshot exists and the watcher hasn't produced one yet. Force LSP
        # OFF here — an LSP warmup (rust-analyzer can take 40-120s) would blow
        # the auto-index timeout and stall the task. The result is a fast,
        # tree-sitter-only snapshot (unresolved Calls/Implements/Inherits);
        # the self-updating watcher, which DOES run LSP, re-resolves it within
        # ~a minute and overwrites this snapshot. So the unresolved graph is a
        # brief bootstrap state, not the steady state.
        return (
            "CRUCIBLE_LSP_ENABLED=0 "
            f"{shlex.quote(auto_indexer)} index "
            f"--workspace {shlex.quote(workspace)} "
            f"--snapshot-path {shlex.quote(str(snapshot_path))} "
            "--watch 0"
        )

    def _compute_age_sec(self, payload: dict[str, object]) -> float | None:
        generated_raw = payload.get("generated_at_ms")
        generated_ms = None
        if isinstance(generated_raw, int):
            generated_ms = generated_raw
        elif isinstance(generated_raw, float):
            generated_ms = int(generated_raw)
        elif isinstance(generated_raw, str) and generated_raw.isdigit():
            generated_ms = int(generated_raw)

        if generated_ms is None:
            return None
        now_ms = int(time.time() * 1000)
        if generated_ms > now_ms:
            return 0.0
        return (now_ms - generated_ms) / 1000.0

    def _build_context(
        self,
        payload: dict[str, object],
        goal: str,
        age_sec: float | None,
        workspace_path: str,
        *,
        discovery_only: bool = False,
        current_query: str | None = None,
    ) -> RetrievalContext:
        workspace_root = Path(workspace_path).resolve()
        snapshot_workspace_root = workspace_root
        workspace_root_raw = payload.get("workspace_root")
        if isinstance(workspace_root_raw, str) and workspace_root_raw.strip():
            snapshot_workspace_root = Path(workspace_root_raw).expanduser().resolve()

        path_cache = self._path_cache_for(payload, workspace_root, snapshot_workspace_root)

        graph = payload.get("graph", {})
        nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
        edges = graph.get("edges", []) if isinstance(graph, dict) else []
        diagnostics: list[object] = payload.get("diagnostics") or []  # type: ignore[assignment]
        stats = payload.get("stats", {})

        raw_node_items = [node for node in nodes if isinstance(node, dict)]
        node_items: list[dict[str, object]] = []
        # Nodes and diagnostics share files (~20 symbols per file), and
        # normalization does filesystem resolves, so it runs once per distinct
        # path, not once per node or diagnostic.
        normalized_by_raw = path_cache.normalized_by_raw

        def normalize_path(raw_path: object) -> str | None:
            if isinstance(raw_path, str) and raw_path in normalized_by_raw:
                return normalized_by_raw[raw_path]
            normalized = self._normalize_snapshot_path(
                raw_path=raw_path,
                workspace_root=workspace_root,
                snapshot_workspace_root=snapshot_workspace_root,
            )
            if isinstance(raw_path, str):
                normalized_by_raw[raw_path] = normalized
            return normalized

        prepared = (self._prepare_discovery_graph(
            payload, workspace_root, snapshot_workspace_root, path_cache)
            if discovery_only and payload.get("_discovery_cache_key") is not None else None)
        if prepared is not None:
            node_items, edge_items = prepared.nodes, prepared.edges
        else:
            for node in raw_node_items:
                normalized_path = normalize_path(node.get("path"))
                if normalized_path is None:
                    continue
                normalized_node = dict(node)
                normalized_node["path"] = normalized_path
                node_items.append(normalized_node)
            edge_items = [edge for edge in edges if isinstance(edge, dict)]
        diagnostic_items = [item for item in diagnostics if isinstance(item, dict)]

        terms = {
            token.lower()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", current_query or goal)
            if len(token) >= 3
        }
        context_terms = ({
            token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", goal)
        } - terms - {"thread", "recap", "conversation", "summary", "previous",
                     "substantive", "answer"}) if discovery_only and current_query else set()

        goal_lower = (current_query or goal).lower()

        eligibility: dict[str, bool] = {}
        wants_tests = bool(_TEST_REQUEST.search(goal_lower))

        def eligible_discovery_path(path: str) -> bool:
            if not discovery_only or wants_tests:
                return True
            if path in eligibility:
                return eligibility[path]
            parts = Path(path).parts
            name = Path(path).name.lower()
            allowed = not (
                any(part in {"test", "tests", "__tests__"} for part in parts)
                or name.startswith("test_") or "_test." in name
                or ".test." in name or ".spec." in name
            )
            eligibility[path] = allowed
            return allowed

        # ── Semantic retrieval (if index is ready) ────────────────────────────
        semantic_results: list[ScoredChunk] = []
        semantic_file_scores: dict[str, float] = {}
        if self.semantic_enabled():
            try:
                sem_index = self._semantic_index
                if sem_index.is_ready():  # type: ignore[union-attr]
                    semantic_results = sem_index.query(  # type: ignore[union-attr]
                        goal,
                        top_k=40,
                        exclude_tests="test" not in goal_lower,
                    )
                    for sr in semantic_results:
                        p = sr.chunk.path
                        if p not in semantic_file_scores or sr.score > semantic_file_scores[p]:
                            semantic_file_scores[p] = sr.score
            except Exception:
                pass  # Graceful fallback to graph-only scoring

        # ── Hybrid scoring: graph token overlap + semantic similarity ─────────
        # Graph score is integer hit count; semantic score is float [0, 1].
        # We normalize graph score to [0, 1] before combining.
        # Weight: 0.35 graph + 0.65 semantic (semantic captures intent better for code goals).
        _GRAPH_WEIGHT = 0.35
        _SEM_WEIGHT = 0.65
        _MAX_GRAPH_SCORE = 10.0  # normalisation ceiling

        scored_nodes: list[tuple[float, dict[str, object]]] = []
        # The path part of the graph score depends only on the path and the goal,
        # so it is computed once per distinct path (terms × segments per node was
        # millions of comparisons on a large snapshot).
        path_score_by_path: dict[str, float] = {}
        for node in node_items:
            if not eligible_discovery_path(str(node.get("path", ""))):
                continue
            node_name = str(node.get("name", "")).lower()
            node_path = str(node.get("path", "")).lower()
            canonical_path = str(node.get("path", ""))
            graph_raw: float = sum(1.0 for term in terms if term in node_name)
            path_score = path_score_by_path.get(node_path)
            if path_score is None:
                path_score = self._path_graph_score(node_path, terms, current_query or goal)
                path_score_by_path[node_path] = path_score
            graph_raw += path_score
            if context_terms:
                # Supplemental recall signals help anaphoric follow-ups, while
                # direct request matches remain stronger than background hits.
                graph_raw += min(0.5, 0.15 * sum(
                    term in node_name or term in node_path for term in context_terms
                ))
            sem_score = semantic_file_scores.get(canonical_path, 0.0)

            # Skip nodes with no signal from either source
            if graph_raw == 0 and sem_score == 0.0:
                continue

            graph_norm = min(graph_raw / _MAX_GRAPH_SCORE, 1.0)
            combined = _GRAPH_WEIGHT * graph_norm + _SEM_WEIGHT * sem_score

            if "test_" in node_name and "test" not in goal_lower:
                combined -= 0.5

            scored_nodes.append((combined, node))

        # Also surface nodes from files the semantic index found but token matching missed
        graph_paths = {str(n.get("path", "")) for _, n in scored_nodes}
        for sem_path, sem_score in semantic_file_scores.items():
            if not eligible_discovery_path(sem_path):
                continue
            if sem_path in graph_paths:
                continue
            for node in node_items:
                if str(node.get("path", "")) == sem_path:
                    scored_nodes.append((_SEM_WEIGHT * sem_score, node))

        scored_nodes.sort(key=lambda item: (-item[0], str(item[1].get("path", ""))))
        matched_nodes = [node for _, node in scored_nodes[:500]]

        if not matched_nodes and not discovery_only:
            matched_nodes = node_items[:8]

        matched_ids = {
            str(node.get("id"))
            for node in matched_nodes
            if isinstance(node.get("id"), str)
        }

        file_scores: dict[str, float] = {}
        for score, node in scored_nodes:
            path = str(node.get("path", ""))
            if path and path not in file_scores:
                file_scores[path] = score

        related_files: list[str] = []
        seen_files: set[str] = set()
        for node in matched_nodes:
            node_path = node.get("path")
            if not isinstance(node_path, str):
                continue
            if node_path in seen_files:
                continue
            related_files.append(node_path)
            seen_files.add(node_path)
            if len(related_files) >= 20:
                break

        related_symbols: list[str] = []
        seen_symbols: set[str] = set()
        for node in matched_nodes:
            node_name = node.get("name")
            if not isinstance(node_name, str):
                continue
            if str(node.get("kind")) == "File":
                continue
            if node_name in seen_symbols:
                continue
            related_symbols.append(node_name)
            seen_symbols.add(node_name)
            if len(related_symbols) >= 40:
                break

        graph_neighbors: set[str] = set()
        for edge in (prepared.edges_for(matched_ids) if prepared else edge_items):
            source = edge.get("from")
            target = edge.get("to")
            if isinstance(source, str) and source in matched_ids and isinstance(target, str):
                graph_neighbors.add(target)
            if isinstance(target, str) and target in matched_ids and isinstance(source, str):
                graph_neighbors.add(source)

        # Filter out neighbors whose IDs reference ignored directories
        filtered_neighbors = [
            n for n in sorted(graph_neighbors)
            if not any(f"/{ignored}/" in n for ignored in self._IGNORED_CONTEXT_DIRS)
        ]
        neighbors = filtered_neighbors[:50]

        # Flatten neighbor node-ids to workspace-relative FILE paths the planner
        # can actually read. We re-walk edge_items rather than reusing `neighbors`
        # because that list mixes intra-file method nodes (engine.py's 79 symbols
        # generate many intra-file edges) and cross-file structural edges in a
        # single alphabetically-sorted 50-cap — intra-file ones squeeze out the
        # cross-file targets we actually need. Here we drop neighbors whose host
        # file is already a seed file, and rank by distinct edge count so the
        # most-connected new files come first.
        nodes_by_id = prepared.by_id if prepared else {
            str(n.get("id")): n for n in node_items
            if isinstance(n.get("id"), str)
        }
        # Seed files = files the planner is actually likely to see as evidence.
        # Union three sources: matched_nodes (keyword + score), the semantic top-K
        # chunk paths (planner_evidence.evidence_files draws from these — they
        # are the files the planner sees most directly), and the resolved paths
        # of any matched file-level node. Without the semantic union, files like
        # engine.py — surfaced only by the embedding model — never become seeds,
        # and their structural neighbours (state_machine.py via Imports, etc.)
        # are missed.
        seed_files: set[str] = {
            str(n.get("path")) for n in matched_nodes
            if isinstance(n.get("path"), str)
        }
        for sr in semantic_results:
            sem_path = sr.chunk.path
            if not sem_path:
                continue
            normalized_sem = normalize_path(sem_path)
            if normalized_sem:
                seed_files.add(normalized_sem)
        # One representative per neighbouring file, selected after hit ranking;
        # an arbitrary first-N edge cutoff can hide all useful relationships.
        neighbor_relationships: dict[str, dict[str, str]] = {}
        internal_relationships: dict[tuple[str, str], dict[str, str]] = {}
        visible_seed_files = set(related_files) | set(semantic_file_scores)
        neighbor_file_hits: dict[str, int] = {}
        seed_ids = (matched_ids | {node_id for path in seed_files
                                  for node_id in prepared.by_path.get(path, [])}
                    if prepared else set())
        for edge in (prepared.edges_for(seed_ids) if prepared else edge_items):
            src = edge.get("from")
            tgt = edge.get("to")
            src_node = nodes_by_id.get(src) if isinstance(src, str) else None
            tgt_node = nodes_by_id.get(tgt) if isinstance(tgt, str) else None
            src_path = src_node.get("path") if isinstance(src_node, dict) else None
            tgt_path = tgt_node.get("path") if isinstance(tgt_node, dict) else None
            # A node is a "seed" if its id is in matched_ids (symbol-level match)
            # OR its host file is a seed file (file-level reach). The file-level
            # check is what unlocks Python file→file Imports edges, whose endpoints
            # are file nodes — never themselves keyword/semantic-matched.
            src_is_seed = (
                (isinstance(src, str) and src in matched_ids)
                or (isinstance(src_path, str) and src_path in seed_files)
            )
            tgt_is_seed = (
                (isinstance(tgt, str) and tgt in matched_ids)
                or (isinstance(tgt_path, str) and tgt_path in seed_files)
            )
            # Preserve edge direction and type without inventing reachability.
            # Shared file endpoints summarize a real symbol/file-level edge.
            kind = str(edge.get("kind", "")).lower()
            if ((src_path in visible_seed_files or tgt_path in visible_seed_files)
                    and isinstance(src_path, str) and isinstance(tgt_path, str)
                    and src_path != tgt_path
                    and kind in {"calls", "imports", "references", "inherits", "implements"}):
                relation = {"from": src_path, "to": tgt_path, "kind": kind}
                if src_is_seed != tgt_is_seed:
                    neighbor_relationships.setdefault(
                        tgt_path if src_is_seed else src_path, relation,
                    )
                elif len(internal_relationships) < 16:
                    internal_relationships.setdefault((src_path, tgt_path), relation)
            if src_is_seed == tgt_is_seed:
                # Both seeds or neither seed → no new file to surface.
                continue
            neighbor_node = tgt_node if src_is_seed else src_node
            if not isinstance(neighbor_node, dict):
                continue
            path_str = neighbor_node.get("path")
            if not isinstance(path_str, str) or path_str in seed_files:
                continue
            if any(f"/{ig}/" in path_str for ig in self._IGNORED_CONTEXT_DIRS):
                continue
            # nodes_by_id already contains normalized workspace-relative paths.
            neighbor_file_hits[path_str] = neighbor_file_hits.get(path_str, 0) + 1

        # Sort by hit count (descending), then path (ascending) for deterministic
        # ties; cap at 20.
        graph_neighbor_files: list[str] = [
            path for path, _ in sorted(
                neighbor_file_hits.items(),
                key=lambda item: (-item[1], item[0]),
            )[:20]
        ]

        if discovery_only:
            # No repository walk, source reads, outline expansion or excerpt
            # extraction. Prefer distinct files before adding extra symbols.
            discovery_neighbors = [path for path in graph_neighbor_files
                                   if eligible_discovery_path(path)]
            selected_paths = related_files[:8]
            # Reserve a little graph reach when the direct ranking is broad.
            if len(selected_paths) > 6 and discovery_neighbors:
                selected_paths = selected_paths[:6]
            selected_paths += [path for path in discovery_neighbors
                               if path not in selected_paths][:8 - len(selected_paths)]
            ranked_by_path: dict[str, list[dict[str, object]]] = {
                path: [] for path in selected_paths
            }
            for result in semantic_results:
                path, name = result.chunk.path, getattr(result.chunk, "name", None)
                if path not in ranked_by_path or not name:
                    continue
                symbol = {"name": name, "kind": getattr(result.chunk, "kind", None),
                          "line": result.chunk.line_start}
                symbols = ranked_by_path[path]
                if symbol not in symbols and len(symbols) < 3:
                    symbols.append(symbol)
            for _, node in scored_nodes:
                path = str(node.get("path", ""))
                if path not in ranked_by_path or node.get("kind") == "File":
                    continue
                symbols = ranked_by_path[path]
                symbol = {key: node.get(key) for key in ("name", "kind", "line")}
                if symbol not in symbols and len(symbols) < 3:
                    symbols.append(symbol)
            discovery = []
            for path, symbols in ranked_by_path.items():
                ranges = []
                for result in semantic_results:
                    if result.chunk.path == path:
                        bounds = [result.chunk.line_start, result.chunk.line_end]
                        if bounds not in ranges and len(ranges) < 3:
                            ranges.append(bounds)
                discovery.append({"path": path, "symbols": symbols, "ranges": ranges})
            return RetrievalContext(
                related_files=selected_paths, graph_neighbor_files=graph_neighbor_files,
                graph_relationships=[*neighbor_relationships.values(),
                                     *internal_relationships.values()],
                discovery_candidates=discovery, snapshot_age_sec=age_sec,
            )

        # Extract structural outlines for top files.
        # When semantic search is active, only include files above threshold to suppress noise.
        # Without semantic, graph scores are much smaller (integer hits / 10), so no threshold.
        _OUTLINE_SCORE_THRESHOLD = 0.45
        file_outlines: dict[str, list[str]] = {}
        candidates = related_files[:8]
        if semantic_results:
            candidates = [f for f in candidates if file_scores.get(f, 0.0) >= _OUTLINE_SCORE_THRESHOLD]
            if not candidates:
                candidates = related_files[:8]  # fallback if all filtered
        top_files = candidates

        _SYMBOL_KINDS = {"Class", "Function", "Method", "Interface", "Protocol"}
        _GAP_THRESHOLD = 30      # lines between symbols before inserting a gap marker
        _CHUNK_BUFFER = 10       # lines around each chunk when limiting large files
        _FULL_LISTING_MAX = 100  # files with ≤ this many symbols get a full listing

        # Per-file lookup of matched chunk line ranges (used for large-file fallback).
        file_chunk_ranges: dict[str, list[tuple[int, int]]] = {}
        for sr in semantic_results:
            p = sr.chunk.path
            if p not in file_chunk_ranges:
                file_chunk_ranges[p] = []
            file_chunk_ranges[p].append((sr.chunk.line_start, sr.chunk.line_end))

        def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
            merged: list[list[int]] = []
            for s, e in sorted(ranges):
                s = max(1, s - _CHUNK_BUFFER)
                e = e + _CHUNK_BUFFER
                if merged and s <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            return [(s, e) for s, e in merged]

        def _build_outline(target_file: str) -> list[str]:
            file_nodes = sorted(
                [n for n in node_items
                 if n.get("path") == target_file and str(n.get("kind", "")) in _SYMBOL_KINDS],
                key=lambda x: _coerce_int(x.get("line"), 0),
            )
            if not file_nodes:
                return []

            def _fmt(n: dict[str, object]) -> str:
                line = n.get("line")
                return f"{n.get('kind')}: {n.get('name')}" + (f" (line {line})" if line else "")

            if len(file_nodes) <= _FULL_LISTING_MAX:
                # Small enough: show every symbol in line order; gap markers for large code gaps.
                result: list[str] = []
                prev_line = 0
                for n in file_nodes:
                    line = _coerce_int(n.get("line"), 0)
                    if prev_line and line - prev_line > _GAP_THRESHOLD:
                        result.append(f"... (lines {prev_line + 1}–{line - 1} omitted)")
                    result.append(_fmt(n))
                    prev_line = line
                return result

            # Large file: show only symbols near semantic chunk hits.
            chunk_ranges = file_chunk_ranges.get(target_file)
            if not chunk_ranges:
                result = [_fmt(n) for n in file_nodes[:20]]
                if len(file_nodes) > 20:
                    result.append(f"... ({len(file_nodes) - 20} more symbols omitted)")
                return result

            merged = _merge_ranges(chunk_ranges)
            result = []
            prev_end = 0
            for rs, re in merged:
                if prev_end > 0 and rs > prev_end + 1:
                    result.append(f"... (lines {prev_end + 1}–{rs - 1} omitted)")
                for n in file_nodes:
                    line = _coerce_int(n.get("line"), 0)
                    if rs <= line <= re:
                        result.append(_fmt(n))
                prev_end = re
            last_line = _coerce_int(file_nodes[-1].get("line"), 0)
            trailing = [n for n in file_nodes if _coerce_int(n.get("line"), 0) > prev_end]
            if trailing:
                result.append(f"... (lines {prev_end + 1}–{last_line} omitted, {len(trailing)} symbols)")
            return result

        for target_file in top_files:
            outlines = _build_outline(target_file)
            if outlines:
                file_outlines[target_file] = outlines

        # Same-directory sibling expansion: for each top file, add outlines for
        # sibling files in the same directory that have semantic signal.
        # Sorted by semantic score so the most relevant siblings are added first.
        # This surfaces co-located files (e.g. errors.py next to validators.py)
        # that the graph doesn't link via cross-file import edges for Python.
        _MAX_OUTLINE_FILES = 12
        indexed_paths = {str(n.get("path", "")) for n in node_items if n.get("kind") == "File"}
        seen_outline_files = set(file_outlines.keys())
        top_dirs = {str(Path(f).parent) for f in top_files}

        sibling_candidates: list[tuple[float, str]] = []
        for sibling in indexed_paths:
            if sibling in seen_outline_files:
                continue
            if str(Path(sibling).parent) not in top_dirs:
                continue
            # Include siblings with semantic signal; fall back to any sibling if no semantic index
            sem_score = semantic_file_scores.get(sibling, 0.0)
            if semantic_results and sem_score == 0.0:
                continue
            sibling_candidates.append((sem_score, sibling))

        sibling_candidates.sort(key=lambda x: -x[0])
        for _, sibling in sibling_candidates:
            if len(file_outlines) >= _MAX_OUTLINE_FILES:
                break
            outlines = _build_outline(sibling)
            if outlines:
                file_outlines[sibling] = outlines
                seen_outline_files.add(sibling)

        relevant_files = set(top_files)
        diagnostics_excerpt: list[str] = []
        for item in diagnostic_items:
            diagnostic_file = normalize_path(item.get("file"))
            if diagnostic_file is None:
                continue
            excerpt = f"{diagnostic_file}:{item.get('line', '?')}: {item.get('message', '')}"
            normalized_file = excerpt.split(":", 1)[0]
            if relevant_files and normalized_file not in relevant_files:
                continue
            diagnostics_excerpt.append(excerpt)
            if len(diagnostics_excerpt) >= 12:
                break

        node_count = _coerce_int(
            stats.get("node_count") if isinstance(stats, dict) else None,
            len(node_items),
        )
        edge_count = _coerce_int(
            stats.get("edge_count") if isinstance(stats, dict) else None,
            len(edge_items),
        )
        diagnostic_count = _coerce_int(
            stats.get("diagnostic_count") if isinstance(stats, dict) else None,
            len(diagnostic_items),
        )

        if path_cache.repository_structure is None:
            repository_structure: list[str] = []
            visible = _git_visible_files(workspace_root)
            visible_dirs = _ancestor_dirs(visible) if visible is not None else None

            def kept_dir(rel: Path) -> bool:
                if self._is_ignored_relative_path(rel):
                    return False
                return visible_dirs is None or rel.as_posix() in visible_dirs

            for root, dirs, files in os.walk(workspace_root):
                rel_root = Path(root).relative_to(workspace_root)
                if self._is_ignored_relative_path(rel_root):
                    dirs.clear()
                    continue

                level = len(rel_root.parts)
                if level > 5:
                    dirs.clear()
                    continue

                indent = "  " * level
                display_name = "." if str(rel_root) == "." else rel_root.name

                valid_dirs = [d for d in dirs if kept_dir(rel_root / d)]
                dirs[:] = sorted(valid_dirs)
                valid_files = sorted(
                    f for f in files if self._is_supported_source_path(Path(f))
                    and (visible is None or (rel_root / f).as_posix() in visible))
                if valid_dirs or valid_files:
                    summary = f"{indent}{display_name}/ ({len(valid_dirs)} dirs, {len(valid_files)} source files)"
                    repository_structure.append(summary)
            path_cache.repository_structure = repository_structure
        repository_structure = list(path_cache.repository_structure)

        planner_evidence = self._build_planner_evidence(
            workspace_root=workspace_root,
            goal_terms=terms,
            matched_nodes=matched_nodes,
            top_files=top_files,
            snapshot_age_sec=age_sec,
            semantic_results=semantic_results,
        )

        return RetrievalContext(
            repository_structure=repository_structure,
            related_files=related_files,
            related_symbols=related_symbols,
            graph_neighbors=neighbors,
            graph_neighbor_files=graph_neighbor_files,
            graph_relationships=[neighbor_relationships[path] for path in graph_neighbor_files
                                 if path in neighbor_relationships]
            + list(internal_relationships.values()),
            file_outlines=file_outlines,
            diagnostics_excerpt=diagnostics_excerpt,
            snapshot_age_sec=age_sec,
            snapshot_stats={
                "node_count": node_count,
                "edge_count": edge_count,
                "diagnostic_count": diagnostic_count,
            },
            planner_evidence=planner_evidence,
            semantic_chunks=semantic_results,
        )

    def _path_cache_for(
        self, payload: dict[str, object], workspace_root: Path, snapshot_workspace_root: Path,
    ) -> _SnapshotPathCache:
        """The cache for this snapshot version, or a throwaway one when the snapshot has no
        version (nothing to key on)."""
        version = _coerce_int(payload.get("generated_at_ms"), 0)
        key = (version, str(workspace_root), str(snapshot_workspace_root),
               payload.get("_discovery_cache_key"))
        if version <= 0 and payload.get("_discovery_cache_key") is None:
            return _SnapshotPathCache(key=key)
        if payload.get("_discovery_cache_key") is not None:
            with self._discovery_prepare_lock:
                cache = self._discovery_path_cache
                if cache is None or cache.key != key:
                    cache = _SnapshotPathCache(key=key)
                    stamp = payload.get("_discovery_cache_key")
                    try:
                        current = (snapshot_version(Path(stamp[0]))
                                   if isinstance(stamp, tuple) and stamp
                                   and isinstance(stamp[0], str) else None)
                    except OSError:
                        current = None
                    # An in-flight query for an older revision gets a private view;
                    # it must not evict a newer graph prepared in the background.
                    if current == stamp:
                        self._discovery_path_cache = cache
                return cache
        cache = self._path_cache
        if cache is None or cache.key != key:
            cache = _SnapshotPathCache(key=key)
            self._path_cache = cache
        return cache

    def _path_graph_score(self, node_path: str, terms: set[str], goal: str) -> float:
        """Graph score contributed by a node's (lowercased) path.

        Directory segments use weighted segment scoring:
          - Exact match or underscore/dot-separated prefix → 1.0 pt
          - Hyphen-prefixed extension (e.g. "pydantic" in "pydantic-core") → 0.5 pt
        This means "pydantic" scores "pydantic/" at 1.0 and "pydantic-core/" at 0.5,
        giving the exact-name directory an edge over hyphenated siblings — without
        zeroing out legitimate hyphenated dirs like "agentd-py/" or "react-dom/".

        The filename segment uses stem-word prefix matching: the file extension
        is stripped, the stem is split on word characters, and each word is matched
        if it equals the term or starts with the term (handles plurals like
        "validators" matching term "validator", but NOT "validation" since
        "validation" does not start with "validator").
        """
        path_segs = node_path.split("/")
        dir_segs = path_segs[:-1]
        file_seg = path_segs[-1] if path_segs else ""
        file_stem = re.sub(r'\.[^.]+$', '', file_seg).lstrip("_").lstrip("-")
        file_words = re.findall(r'[a-z][a-z0-9]*', file_stem) if file_stem else []

        def _dir_seg_score(seg: str, term: str) -> float:
            s = seg.lstrip("_")
            if s == term or s.startswith(term + "_") or s.startswith(term + "."):
                return 1.0
            if s.startswith(term + "-"):
                return 0.5  # partial credit for hyphenated extensions (agentd-py, react-dom, etc.)
            return 0.0

        score: float = sum(
            max((_dir_seg_score(seg, term) for seg in dir_segs), default=0.0)
            for term in terms
        )
        score += sum(
            1.0 for term in terms
            if any(w == term or w.startswith(term) for w in file_words)
        )
        score += self._evidence_adapter.path_relevance_score(
            goal=goal,
            normalized_path=node_path,
        )
        return score

    def _normalize_snapshot_path(
        self,
        *,
        raw_path: object,
        workspace_root: Path,
        snapshot_workspace_root: Path,
    ) -> str | None:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None

        path_str = raw_path.strip()
        candidate = Path(path_str).expanduser()
        
        # If the path is absolute and doesn't match workspace_root,
        # it might be from a different workspace/shadow environment.
        # We try to extract the project-relative part by finding the common suffix.
        if candidate.is_absolute() and not self._is_within(candidate, workspace_root):
            # STRATEGY: Find the project-relative path by looking for the last 
            # occurrence of a project sub-directory that exists in the current workspace.
            # This is more robust than hardcoding markers.
            parts = candidate.parts
            for i in range(len(parts)):
                # Take the suffix from index i to end
                suffix = Path(*parts[i:])
                if (workspace_root / suffix).exists():
                    return suffix.as_posix()

        # Standard resolution for relative paths or paths already within workspace_root
        resolved = candidate.resolve() if candidate.is_absolute() else (snapshot_workspace_root / candidate).resolve()
        if not self._is_within(resolved, workspace_root):
            # Final fallback: if it's a relative path that doesn't resolve within snapshot_workspace_root,
            # try resolving it relative to the current workspace_root.
            if not candidate.is_absolute():
                fallback = (workspace_root / candidate).resolve()
                if self._is_within(fallback, workspace_root):
                    return candidate.as_posix()
            return None

        relative = resolved.relative_to(workspace_root)
        if self._is_ignored_relative_path(relative):
            return None
        return relative.as_posix()

    def _is_within(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _is_ignored_relative_path(self, relative_path: Path) -> bool:
        return any(part in self._IGNORED_CONTEXT_DIRS for part in relative_path.parts)

    def _is_supported_source_path(self, path: Path) -> bool:
        # Same extensions as indexer-rs's is_supported_source_path (service.rs)
        ext = path.suffix.lower()
        return ext in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs", ".go", ".java"}

    def _build_workspace_files_index(self, workspace_root: Path) -> list[str]:
        indexed: list[str] = []
        for root, dirs, files in os.walk(workspace_root):
            rel_root = Path(root).relative_to(workspace_root)
            if self._is_ignored_relative_path(rel_root):
                dirs.clear()
                continue
            dirs[:] = sorted(d for d in dirs if not self._is_ignored_relative_path(rel_root / d))
            for file_name in sorted(files):
                rel_path = (Path(root) / file_name).relative_to(workspace_root)
                if self._is_ignored_relative_path(rel_path):
                    continue
                indexed.append(rel_path.as_posix())
                if len(indexed) >= 15000:
                    return indexed
        return indexed

    def _build_planner_evidence(
        self,
        *,
        workspace_root: Path,
        goal_terms: set[str],
        matched_nodes: list[dict[str, object]],
        top_files: list[str],
        snapshot_age_sec: float | None,
        semantic_results: list[ScoredChunk] | None = None,
    ) -> PlanEvidencePack:
        evidence_files: list[PlanEvidenceFile] = []
        evidence_symbols: list[PlanEvidenceSymbol] = []

        # Prefer semantic chunks as evidence files — they are semantically grounded to the
        # goal rather than using a keyword-anchor heuristic within the file.
        if semantic_results:
            seen_paths: set[str] = set()
            for sr in semantic_results[:12]:
                chunk = sr.chunk
                if chunk.path in seen_paths:
                    continue
                seen_paths.add(chunk.path)
                abs_path = workspace_root / chunk.path
                if not abs_path.exists():
                    continue
                evidence_files.append(
                    PlanEvidenceFile(
                        path=chunk.path,
                        excerpt=chunk.text_with_lines,
                        rationale=(
                            f"semantic match (score={sr.score:.2f}): "
                            f"{chunk.kind} {chunk.name}"
                            + (f" in {chunk.parent_name}" if chunk.parent_name else "")
                        ),
                        line_start=chunk.line_start,
                        line_end=chunk.line_end,
                    )
                )
                if len(evidence_files) >= 8:
                    break
        else:
            # Fallback: keyword-anchored excerpts (original behaviour)
            for target_file in top_files[:8]:
                excerpt_info = self._extract_file_evidence(
                    workspace_root=workspace_root,
                    file_path=target_file,
                    matched_nodes=matched_nodes,
                    goal_terms=goal_terms,
                )
                if excerpt_info is None:
                    continue
                evidence_files.append(
                    PlanEvidenceFile(
                        path=target_file,
                        excerpt=excerpt_info["excerpt"],
                        rationale=excerpt_info["rationale"],
                        line_start=excerpt_info["line_start"],
                        line_end=excerpt_info["line_end"],
                    )
                )

        seen_symbol_keys: set[tuple[str, str]] = set()
        for node in matched_nodes:
            node_name = str(node.get("name", "")).strip()
            node_kind = str(node.get("kind", "")).strip()
            node_file = node.get("path")
            if not node_name or not isinstance(node_file, str) or node_kind == "File":
                continue
            symbol_key = (node_file, node_name)
            if symbol_key in seen_symbol_keys:
                continue
            seen_symbol_keys.add(symbol_key)
            snippet = self._extract_symbol_snippet(
                workspace_root=workspace_root,
                file_path=node_file,
                line=_coerce_int(node.get("line"), 0) or None,
                symbol=node_name,
            )
            evidence_symbols.append(
                PlanEvidenceSymbol(
                    name=node_name,
                    kind=node_kind,
                    file=node_file,
                    line=_coerce_int(node.get("line"), 0) or None,
                    snippet=snippet,
                )
            )
            if len(evidence_symbols) >= 16:
                break

        confidence_notes: list[str] = []
        if snapshot_age_sec is not None and snapshot_age_sec > self._max_age_sec:
            confidence_notes.append(
                f"Snapshot is stale ({snapshot_age_sec:.1f}s old); prefer evidence excerpts over graph freshness."
            )
        if not evidence_files:
            confidence_notes.append("No grounded file excerpts were extracted from the current workspace.")
        if not evidence_symbols:
            confidence_notes.append("No grounded symbol evidence was extracted for this goal.")

        return PlanEvidencePack(
            evidence_files=evidence_files,
            evidence_symbols=evidence_symbols,
            confidence_notes=confidence_notes,
        )

    def _extract_file_evidence(
        self,
        *,
        workspace_root: Path,
        file_path: str,
        matched_nodes: list[dict[str, object]],
        goal_terms: set[str],
    ) -> dict[str, object] | None:
        absolute_path = workspace_root / file_path
        if not absolute_path.exists() or not absolute_path.is_file():
            return None
        try:
            lines = absolute_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None

        file_nodes = [node for node in matched_nodes if node.get("path") == file_path]
        anchor_line: int | None = None
        rationale = "keyword-grounded excerpt"
        if file_nodes:
            line_values = [
                _coerce_int(node.get("line"), 0)
                for node in file_nodes
                if _coerce_int(node.get("line"), 0) > 0
            ]
            if line_values:
                anchor_line = min(line_values)
                rationale = "symbol-grounded excerpt"

        if anchor_line is None and goal_terms:
            for index, line in enumerate(lines, start=1):
                lowered = line.lower()
                if any(term in lowered for term in goal_terms):
                    anchor_line = index
                    break

        if anchor_line is None:
            anchor_line = 1

        start = max(1, anchor_line - 3)
        end = min(len(lines), start + 11)
        excerpt = "\n".join(lines[start - 1 : end])
        return {
            "excerpt": excerpt,
            "rationale": rationale,
            "line_start": start,
            "line_end": end,
        }

    def _extract_symbol_snippet(
        self,
        *,
        workspace_root: Path,
        file_path: str,
        line: int | None,
        symbol: str,
    ) -> str | None:
        absolute_path = workspace_root / file_path
        if not absolute_path.exists() or not absolute_path.is_file():
            return None
        try:
            lines = absolute_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None

        if line is None or line <= 0:
            for index, content in enumerate(lines, start=1):
                if symbol in content:
                    line = index
                    break
        if line is None or line <= 0:
            return None

        start = max(1, line - 2)
        end = min(len(lines), line + 3)
        return "\n".join(lines[start - 1 : end])
