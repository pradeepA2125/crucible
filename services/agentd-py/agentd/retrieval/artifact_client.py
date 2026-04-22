from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from agentd.domain.models import (
    Diagnostic,
    PlanEvidenceFile,
    PlanEvidencePack,
    PlanEvidenceSymbol,
)
from agentd.runtime.adapters import EvidenceAdapter, GenericEvidenceAdapter


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
    file_outlines: dict[str, list[str]] = field(default_factory=dict)
    diagnostics_excerpt: list[str] = field(default_factory=list)
    snapshot_age_sec: float | None = None
    snapshot_stats: dict[str, int] = field(
        default_factory=lambda: {"node_count": 0, "edge_count": 0, "diagnostic_count": 0}
    )
    file_contents: dict[str, str] = field(default_factory=dict)
    planner_evidence: PlanEvidencePack = field(default_factory=PlanEvidencePack)

    @classmethod
    def empty(cls) -> "RetrievalContext":
        return cls(
            repository_structure=[],
            related_files=[],
            related_symbols=[],
            graph_neighbors=[],
            file_outlines={},
            file_contents={},
            planner_evidence=PlanEvidencePack(),
            diagnostics_excerpt=[],
            snapshot_age_sec=None,
            snapshot_stats={"node_count": 0, "edge_count": 0, "diagnostic_count": 0},
        )

    def as_prompt_payload(self) -> dict[str, object]:
        planner_evidence = (
            self.planner_evidence.model_dump(mode="json")
            if hasattr(self.planner_evidence, "model_dump")
            else self.planner_evidence
        )
        return {
            "repository_structure": self.repository_structure,
            "related_files": self.related_files,
            "related_symbols": self.related_symbols,
            "graph_neighbors": self.graph_neighbors,
            "file_outlines": self.file_outlines,
            "file_contents": self.file_contents,
            "planner_evidence": planner_evidence,
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
        ".mypy_cache",
        "target",
        "dist",
        ".agentd",
        ".ai-editor",
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
    ) -> None:
        self._snapshot_path_template = snapshot_path_template
        self._max_age_sec = max_age_sec
        self._index_command_template = index_command_template
        self._index_timeout_sec = index_timeout_sec
        self._evidence_adapter = evidence_adapter or GenericEvidenceAdapter()

    @classmethod
    def from_env(
        cls,
        *,
        evidence_adapter: EvidenceAdapter | None = None,
    ) -> "RetrievalArtifactClient":
        return cls(
            snapshot_path_template=os.getenv("AI_EDITOR_RETRIEVAL_SNAPSHOT_PATH"),
            max_age_sec=int(os.getenv("AI_EDITOR_RETRIEVAL_MAX_AGE_SEC", "900")),
            index_command_template=os.getenv("AI_EDITOR_INDEXER_INDEX_CMD"),
            index_timeout_sec=int(os.getenv("AI_EDITOR_INDEXER_INDEX_TIMEOUT_SEC", "120")),
            evidence_adapter=evidence_adapter,
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
            score = hit_count + self._evidence_adapter.path_relevance_score(
                goal=goal,
                normalized_path=node_path,
            )
            
            # Deprioritize test symbols unless goal mentions tests
            if "test_" in node_name and "test" not in goal_lower:
                score -= 10
                
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

        # Filter out neighbors whose IDs reference ignored directories
        filtered_neighbors = [
            n for n in sorted(graph_neighbors)
            if not any(f"/{ignored}/" in n for ignored in self._IGNORED_CONTEXT_DIRS)
        ]
        neighbors = filtered_neighbors[:50]

        # Extract structural outlines for top files
        file_outlines: dict[str, list[str]] = {}
        top_files = related_files[:8]  # Limit to top 8 most relevant files
        for target_file in top_files:
            outlines = []
            # Find all nodes belonging to this file
            file_nodes = [n for n in node_items if n.get("path") == target_file]
            # Group by kind and sort by line if available
            file_nodes.sort(key=lambda x: _coerce_int(x.get("line"), 0))
            
            for fnode in file_nodes:
                kind = str(fnode.get("kind", ""))
                name = str(fnode.get("name", ""))
                if kind in {"Class", "Function", "Method", "Interface", "Protocol"}:
                    line = fnode.get("line")
                    suffix = f" (line {line})" if line else ""
                    outlines.append(f"{kind}: {name}{suffix}")
            
            if outlines:
                file_outlines[target_file] = outlines

        relevant_files = set(top_files)
        diagnostics_excerpt: list[str] = []
        for item in diagnostic_items:
            excerpt = self._format_diagnostic_excerpt(
                item,
                workspace_root=workspace_root,
                snapshot_workspace_root=snapshot_workspace_root,
            )
            if excerpt is None:
                continue
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

        repository_structure: list[str] = []
        workspace_files_index = self._build_workspace_files_index(workspace_root)
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

            valid_dirs = [d for d in dirs if not self._is_ignored_relative_path(rel_root / d)]
            valid_files = [f for f in files if self._is_supported_source_path(Path(f))]
            if valid_dirs or valid_files:
                summary = f"{indent}{display_name}/ ({len(valid_dirs)} dirs, {len(valid_files)} source files)"
                repository_structure.append(summary)

        planner_evidence = self._build_planner_evidence(
            workspace_root=workspace_root,
            workspace_files_index=workspace_files_index,
            goal_terms=terms,
            matched_nodes=matched_nodes,
            node_items=node_items,
            top_files=top_files,
            diagnostics_excerpt=diagnostics_excerpt,
            snapshot_age_sec=age_sec,
        )

        return RetrievalContext(
            repository_structure=repository_structure,
            related_files=related_files,
            related_symbols=related_symbols,
            graph_neighbors=neighbors,
            file_outlines=file_outlines,
            diagnostics_excerpt=diagnostics_excerpt,
            snapshot_age_sec=age_sec,
            snapshot_stats={
                "node_count": node_count,
                "edge_count": edge_count,
                "diagnostic_count": diagnostic_count,
            },
            planner_evidence=planner_evidence,
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
        # Same extensions as indexer-rs
        ext = path.suffix.lower()
        return ext in {".ts", ".tsx", ".py", ".rs"}

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
        workspace_files_index: list[str],
        goal_terms: set[str],
        matched_nodes: list[dict[str, object]],
        node_items: list[dict[str, object]],
        top_files: list[str],
        diagnostics_excerpt: list[str],
        snapshot_age_sec: float | None,
    ) -> PlanEvidencePack:
        evidence_files: list[PlanEvidenceFile] = []
        evidence_symbols: list[PlanEvidenceSymbol] = []
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

        category_facts = self._evidence_adapter.build_category_facts(
            evidence_files=evidence_files,
        )

        confidence_notes: list[str] = []
        if snapshot_age_sec is not None and snapshot_age_sec > self._max_age_sec:
            confidence_notes.append(
                f"Snapshot is stale ({snapshot_age_sec:.1f}s old); prefer evidence excerpts over graph freshness."
            )
        if not evidence_files:
            confidence_notes.append("No grounded file excerpts were extracted from the current workspace.")
        if not evidence_symbols:
            confidence_notes.append("No grounded symbol evidence was extracted for this goal.")
        if not diagnostics_excerpt:
            confidence_notes.append("No goal-relevant diagnostics were found in the retrieval snapshot.")
        if len(workspace_files_index) == 0:
            confidence_notes.append("Workspace file index is empty; file-path validation confidence is low.")

        return PlanEvidencePack(
            workspace_files_index=workspace_files_index,
            evidence_files=evidence_files,
            evidence_symbols=evidence_symbols,
            evidence_routes_models_storage=category_facts,
            diagnostics_excerpt=diagnostics_excerpt,
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
