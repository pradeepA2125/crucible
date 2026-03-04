from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentd.domain.models import Diagnostic


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
    related_files: list[str]
    related_symbols: list[str]
    graph_neighbors: list[str]
    diagnostics_excerpt: list[str]
    snapshot_age_sec: float | None
    snapshot_stats: dict[str, int]

    @classmethod
    def empty(cls) -> "RetrievalContext":
        return cls(
            related_files=[],
            related_symbols=[],
            graph_neighbors=[],
            diagnostics_excerpt=[],
            snapshot_age_sec=None,
            snapshot_stats={"node_count": 0, "edge_count": 0, "diagnostic_count": 0},
        )

    def as_prompt_payload(self) -> dict[str, object]:
        return {
            "related_files": self.related_files,
            "related_symbols": self.related_symbols,
            "graph_neighbors": self.graph_neighbors,
            "diagnostics_excerpt": self.diagnostics_excerpt,
            "snapshot_age_sec": self.snapshot_age_sec,
            "snapshot_stats": self.snapshot_stats,
        }


class RetrievalArtifactClient:
    _IGNORED_CONTEXT_DIRS = {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "target",
        "dist",
        ".agentd",
        ".ai-editor",
    }

    def __init__(
        self,
        *,
        snapshot_path_template: str | None = None,
        max_age_sec: int = 900,
        index_command_template: str | None = None,
        index_timeout_sec: int = 120,
    ) -> None:
        self._snapshot_path_template = snapshot_path_template
        self._max_age_sec = max_age_sec
        self._index_command_template = index_command_template
        self._index_timeout_sec = index_timeout_sec

    @classmethod
    def from_env(cls) -> "RetrievalArtifactClient":
        return cls(
            snapshot_path_template=os.getenv("AI_EDITOR_RETRIEVAL_SNAPSHOT_PATH"),
            max_age_sec=int(os.getenv("AI_EDITOR_RETRIEVAL_MAX_AGE_SEC", "900")),
            index_command_template=os.getenv("AI_EDITOR_INDEXER_INDEX_CMD"),
            index_timeout_sec=int(os.getenv("AI_EDITOR_INDEXER_INDEX_TIMEOUT_SEC", "120")),
        )

    def load_context(
        self,
        workspace_path: str,
        goal: str,
    ) -> tuple[RetrievalContext, list[Diagnostic]]:
        diagnostics: list[Diagnostic] = []
        snapshot_path = self._resolve_snapshot_path(workspace_path)

        if not snapshot_path.exists():
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
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
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

        context = self._build_context(payload, goal, age_sec, workspace_path)
        return context, diagnostics

    def _resolve_snapshot_path(self, workspace_path: str) -> Path:
        workspace = Path(workspace_path).resolve()
        if self._snapshot_path_template:
            rendered = self._snapshot_path_template.format(
                workspace=str(workspace),
                snapshot_path=str(workspace / ".ai-editor/index-snapshot.json"),
            )
            return Path(rendered).expanduser().resolve()
        return (workspace / ".ai-editor/index-snapshot.json").resolve()

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

        auto_indexer = shutil.which("ai-editor-indexer")
        if not auto_indexer:
            return None

        return (
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
    ) -> RetrievalContext:
        workspace_root = Path(workspace_path).resolve()
        snapshot_workspace_root = workspace_root
        workspace_root_raw = payload.get("workspace_root")
        if isinstance(workspace_root_raw, str) and workspace_root_raw.strip():
            snapshot_workspace_root = Path(workspace_root_raw).expanduser().resolve()

        graph = payload.get("graph", {})
        nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
        edges = graph.get("edges", []) if isinstance(graph, dict) else []
        diagnostics = payload.get("diagnostics", [])
        stats = payload.get("stats", {})

        raw_node_items = [node for node in nodes if isinstance(node, dict)]
        node_items: list[dict[str, object]] = []
        for node in raw_node_items:
            normalized_path = self._normalize_snapshot_path(
                raw_path=node.get("path"),
                workspace_root=workspace_root,
                snapshot_workspace_root=snapshot_workspace_root,
            )
            if normalized_path is None:
                continue
            normalized_node = dict(node)
            normalized_node["path"] = normalized_path
            node_items.append(normalized_node)

        edge_items = [edge for edge in edges if isinstance(edge, dict)]
        diagnostic_items = [item for item in diagnostics if isinstance(item, dict)]

        terms = {
            token.lower()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", goal)
            if len(token) >= 3
        }

        goal_lower = goal.lower()
        scored_nodes: list[tuple[int, dict[str, object]]] = []
        for node in node_items:
            node_name = str(node.get("name", "")).lower()
            node_path = str(node.get("path", "")).lower()
            hit_count = sum(1 for term in terms if term in node_name or term in node_path)
            if hit_count == 0:
                continue
            score = hit_count + self._path_bias_score(node_path, goal_lower)
            scored_nodes.append((score, node))

        scored_nodes.sort(key=lambda item: (-item[0], str(item[1].get("path", ""))))
        matched_nodes = [node for _, node in scored_nodes[:500]]

        if not matched_nodes:
            matched_nodes = node_items[:8]

        matched_ids = {
            str(node.get("id"))
            for node in matched_nodes
            if isinstance(node.get("id"), str)
        }

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
        for edge in edge_items:
            source = edge.get("from")
            target = edge.get("to")
            if isinstance(source, str) and source in matched_ids and isinstance(target, str):
                graph_neighbors.add(target)
            if isinstance(target, str) and target in matched_ids and isinstance(source, str):
                graph_neighbors.add(source)
        neighbors = sorted(graph_neighbors)[:50]

        diagnostics_excerpt: list[str] = []
        for item in diagnostic_items:
            excerpt = self._format_diagnostic_excerpt(
                item,
                workspace_root=workspace_root,
                snapshot_workspace_root=snapshot_workspace_root,
            )
            if excerpt is not None:
                diagnostics_excerpt.append(excerpt)
            if len(diagnostics_excerpt) >= 20:
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

        return RetrievalContext(
            related_files=related_files,
            related_symbols=related_symbols,
            graph_neighbors=neighbors,
            diagnostics_excerpt=diagnostics_excerpt,
            snapshot_age_sec=age_sec,
            snapshot_stats={
                "node_count": node_count,
                "edge_count": edge_count,
                "diagnostic_count": diagnostic_count,
            },
        )

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
        resolved = candidate.resolve() if candidate.is_absolute() else (snapshot_workspace_root / candidate).resolve()
        if not self._is_within(resolved, workspace_root):
            # Some snapshots store repo-relative paths while workspace_root changes across machines.
            if not candidate.is_absolute():
                fallback = (workspace_root / candidate).resolve()
                if self._is_within(fallback, workspace_root):
                    resolved = fallback
                else:
                    return None
            else:
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

    def _path_bias_score(self, normalized_path: str, goal_lower: str) -> int:
        score = 0
        if "agentd-py" in goal_lower and normalized_path.startswith("services/agentd-py/"):
            score += 5
        if "indexer" in goal_lower and normalized_path.startswith("services/indexer-rs/"):
            score += 5
        if ("vscode" in goal_lower or "extension" in goal_lower) and normalized_path.startswith(
            "apps/vscode-extension/"
        ):
            score += 4
        if (
            "editor-client" in goal_lower
            or "typescript client" in goal_lower
            or "sdk" in goal_lower
        ) and normalized_path.startswith("apps/editor-client/"):
            score += 4
        if "docs" in goal_lower and normalized_path.startswith("docs/"):
            score += 2
        return score

    def _format_diagnostic_excerpt(
        self,
        item: dict[str, object],
        *,
        workspace_root: Path,
        snapshot_workspace_root: Path,
    ) -> str | None:
        normalized_file = self._normalize_snapshot_path(
            raw_path=item.get("file"),
            workspace_root=workspace_root,
            snapshot_workspace_root=snapshot_workspace_root,
        )
        if normalized_file is None:
            return None

        line = item.get("line", "?")
        message = item.get("message", "")
        return f"{normalized_file}:{line}: {message}"
