"""AST-aware code chunker.

Uses the snapshot's symbol nodes as AST-quality chunk boundaries — no re-parsing needed.
Reads actual workspace files to get code content. Produces rich CodeChunk objects ready
for embedding and vector storage.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class CodeChunk:
    # Identity
    chunk_id: str           # "src/auth.py::L42" — vector DB primary key

    # Location
    path: str               # workspace-relative: "src/auth.py"
    language: str           # "python" | "typescript" | "rust"
    line_start: int         # 1-indexed, in original file
    line_end: int           # 1-indexed, in original file
    line_count: int         # line_end - line_start + 1

    # Symbol identity
    name: str               # "validate_token"
    kind: str               # "Function" | "Class" | "Method" | "Interface" | "Module"
    signature: str          # first line of symbol declaration

    # Hierarchy — parent class/module context
    parent_name: str | None     # enclosing class for methods: "AuthService"
    parent_kind: str | None     # "Class" | None for top-level
    module_path: str            # dot-path: "agentd.retrieval.artifact_client"
    is_top_level: bool          # True if not nested inside another symbol

    # Relationships from snapshot edges
    imports: list[str]      # modules this FILE imports: ["os", "json", "agentd.domain"]
    calls: list[str]        # symbol names this chunk invokes
    called_by: list[str]    # symbol names that call this chunk

    # Content
    docstring: str | None       # extracted docstring / JSDoc / Rust doc comment
    text: str                   # raw code — what gets embedded
    text_with_lines: str        # "  42: def validate...\n  43:..." — for LLM context
    context_before: str         # 3 lines immediately before chunk
    context_after: str          # 3 lines immediately after chunk

    # Flags
    is_test: bool           # path contains "test" or "spec"
    has_docstring: bool     # docstring is non-empty

    # Delta tracking
    file_mtime: float       # file mtime at index time
    indexed_at_ms: int      # epoch ms when chunk was built


@dataclass
class ScoredChunk:
    """A CodeChunk with an associated relevance score from ANN search."""
    chunk: CodeChunk
    score: float            # cosine similarity [0, 1]


# ── Chunker ───────────────────────────────────────────────────────────────────

class CodeChunker:
    """Builds CodeChunk list from a snapshot payload + workspace file reads."""

    _SYMBOL_KINDS = {"Function", "Class", "Method", "Interface", "Protocol"}
    _MAX_CHUNK_LINES = 120
    _CONTEXT_LINES = 3
    _LANGUAGE_MAP = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".rs": "rust",
    }

    def __init__(self, max_chunk_lines: int = 120) -> None:
        self._max_chunk_lines = max_chunk_lines

    def build(
        self,
        workspace_path: str | Path,
        snapshot_payload: dict[str, object],
    ) -> list[CodeChunk]:
        """Build chunks from a snapshot payload and workspace files.

        Uses snapshot nodes as chunk boundaries (AST-quality, no re-parsing).
        Reads actual code from workspace files.
        """
        workspace = Path(workspace_path).resolve()
        graph = snapshot_payload.get("graph", {})
        nodes: list[object] = graph.get("nodes", []) if isinstance(graph, dict) else []
        edges: list[object] = graph.get("edges", []) if isinstance(graph, dict) else []

        if not isinstance(nodes, list) or not isinstance(edges, list):
            return []

        # Pre-build edge lookup tables once — O(E) upfront, O(1) per chunk
        # Paths in edges may be absolute (Rust indexer) — normalise to workspace-relative.
        file_imports: dict[str, list[str]] = {}   # rel_path -> imported module names
        calls_from: dict[str, list[str]] = {}     # node_id -> called symbol names
        called_by_to: dict[str, list[str]] = {}   # node_id -> caller symbol names

        for edge in edges:
            if not isinstance(edge, dict):
                continue
            from_id = str(edge.get("from", ""))
            to_id = str(edge.get("to", ""))
            kind = str(edge.get("kind", ""))

            if kind == "Imports":
                fp = _file_path_from_node_id(from_id)
                fp = _make_relative(fp, workspace) if fp else None
                module_name = _module_name_from_node_id(to_id)
                if fp and module_name:
                    file_imports.setdefault(fp, []).append(module_name)
            elif kind == "Calls":
                to_name = _symbol_name_from_node_id(to_id)
                from_name = _symbol_name_from_node_id(from_id)
                if to_name:
                    calls_from.setdefault(from_id, []).append(to_name)
                if from_name:
                    called_by_to.setdefault(to_id, []).append(from_name)

        # Group nodes by workspace-relative file path (skip File-kind nodes)
        by_path: dict[str, list[dict[str, object]]] = {}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if str(node.get("kind", "")) == "File":
                continue
            raw_path = str(node.get("path", ""))
            rel = _make_relative(raw_path, workspace) if raw_path else None
            if rel:
                by_path.setdefault(rel, []).append(node)

        chunks: list[CodeChunk] = []
        now_ms = int(time.time() * 1000)

        for rel_path, file_nodes in by_path.items():
            abs_path = workspace / rel_path
            if not abs_path.exists() or not abs_path.is_file():
                continue

            try:
                file_mtime = abs_path.stat().st_mtime
                raw_text = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            lines = raw_text.splitlines()
            if not lines:
                continue

            language = self._LANGUAGE_MAP.get(abs_path.suffix.lower(), "unknown")
            is_test = "test" in rel_path.lower() or "spec" in rel_path.lower()
            file_import_list = list(dict.fromkeys(file_imports.get(rel_path, [])))  # dedup
            module_path = _path_to_module(rel_path)

            # Only keep symbol-kind nodes, sorted by line
            # Sort by line, then deduplicate: keep only the first node at each line.
            # The Rust indexer can emit multiple nodes at the same line (e.g. a struct
            # and its impl block share line 0, or two nodes cover the same declaration).
            _all = sorted(
                (n for n in file_nodes if str(n.get("kind", "")) in self._SYMBOL_KINDS),
                key=lambda n: _to_int(n.get("line")),
            )
            seen_lines: set[int] = set()
            symbol_nodes: list[dict[str, object]] = []
            for n in _all:
                ln = _to_int(n.get("line"))
                if ln > 0 and ln not in seen_lines:
                    seen_lines.add(ln)
                    symbol_nodes.append(n)

            for i, node in enumerate(symbol_nodes):
                line_start = _to_int(node.get("line"))
                if line_start <= 0 or line_start > len(lines):
                    continue

                # Chunk end: one line before next symbol, or max_chunk_lines from start
                if i + 1 < len(symbol_nodes):
                    next_line = _to_int(symbol_nodes[i + 1].get("line"))
                    natural_end = (next_line - 1) if next_line > line_start else len(lines)
                else:
                    natural_end = len(lines)

                line_end = min(natural_end, line_start + self._max_chunk_lines - 1)
                line_end = max(line_start, min(line_end, len(lines)))

                chunk_lines = lines[line_start - 1 : line_end]
                if not chunk_lines:
                    continue

                # Context lines before and after (do not overlap with chunk)
                before_start = max(0, line_start - 1 - self._CONTEXT_LINES)
                context_before = "\n".join(lines[before_start : line_start - 1])
                after_end = min(len(lines), line_end)
                context_after = "\n".join(lines[after_end : after_end + self._CONTEXT_LINES])

                text = "\n".join(chunk_lines)
                text_with_lines = "\n".join(
                    f"{line_start + j:4d}: {line}"
                    for j, line in enumerate(chunk_lines)
                )

                name = str(node.get("name", ""))
                kind = str(node.get("kind", ""))
                node_id = str(node.get("id", ""))

                signature = chunk_lines[0].rstrip()
                docstring = _extract_docstring(chunk_lines, language)
                parent_name, parent_kind = _extract_parent(node_id, rel_path, name, kind)

                chunks.append(CodeChunk(
                    chunk_id=f"{rel_path}::L{line_start}",
                    path=rel_path,
                    language=language,
                    line_start=line_start,
                    line_end=line_end,
                    line_count=line_end - line_start + 1,
                    name=name,
                    kind=kind,
                    signature=signature,
                    parent_name=parent_name,
                    parent_kind=parent_kind,
                    module_path=module_path,
                    is_top_level=parent_name is None,
                    imports=file_import_list,
                    calls=list(dict.fromkeys(calls_from.get(node_id, []))),
                    called_by=list(dict.fromkeys(called_by_to.get(node_id, []))),
                    docstring=docstring,
                    text=text,
                    text_with_lines=text_with_lines,
                    context_before=context_before,
                    context_after=context_after,
                    is_test=is_test,
                    has_docstring=bool(docstring),
                    file_mtime=file_mtime,
                    indexed_at_ms=now_ms,
                ))

        return chunks

    def make_embedding_text(self, chunk: CodeChunk) -> str:
        """Format the text that gets embedded.

        Leads with structural metadata so the model encodes intent + hierarchy,
        then appends the actual code body for implementation semantics.
        Capped at ~400 words to fit embedding model context windows.
        """
        parts: list[str] = []
        parts.append(f"{chunk.kind}: {chunk.name} | {chunk.path}:{chunk.line_start}")
        if chunk.parent_name:
            parts.append(f"Parent: {chunk.parent_name} ({chunk.parent_kind})")
        parts.append(f"Module: {chunk.module_path}")
        parts.append(f"Language: {chunk.language}")
        if chunk.imports:
            parts.append(f"Imports: {', '.join(chunk.imports[:8])}")
        parts.append(f"Signature: {chunk.signature}")
        if chunk.docstring:
            parts.append(f"Docstring: {chunk.docstring[:200]}")
        if chunk.calls:
            parts.append(f"Calls: {', '.join(chunk.calls[:8])}")
        parts.append("")
        # Code body — trim to keep total under ~512 tokens
        body_lines = chunk.text.splitlines()[:80]
        parts.append("\n".join(body_lines))
        return "\n".join(parts)


# ── Type helpers ─────────────────────────────────────────────────────────────

def _make_relative(raw_path: str, workspace: Path) -> str | None:
    """Return a workspace-relative POSIX path, or None if outside workspace.

    Handles both absolute paths (from the Rust indexer) and already-relative
    paths (from Python/TS snapshots).
    """
    if not raw_path:
        return None
    p = Path(raw_path)
    if p.is_absolute():
        try:
            return p.relative_to(workspace).as_posix()
        except ValueError:
            return None
    return raw_path


def _to_int(value: object, default: int = 0) -> int:
    """Safely coerce a snapshot field value to int."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and (value.isdigit() or (value.startswith("-") and value[1:].isdigit())):
        return int(value)
    return default


# ── Node ID parsing helpers ───────────────────────────────────────────────────

def _file_path_from_node_id(node_id: str) -> str | None:
    """Extract workspace-relative file path from a node ID.

    Formats:
      file:src/auth.py                           → "src/auth.py"
      class:file:src/auth.py:AuthService         → "src/auth.py"
      method:file:src/auth.py:AuthService:foo:42 → "src/auth.py"
    """
    # Pure file node
    if node_id.startswith("file:"):
        return node_id[5:] or None

    # Symbol node: <kind>:file:<path>:<rest>
    marker = ":file:"
    idx = node_id.find(marker)
    if idx == -1:
        return None

    after = node_id[idx + len(marker):]
    # The path is the portion up to the next ":" that separates it from symbol name.
    # Since Unix paths contain no ":" we can split naively.
    colon_idx = after.find(":")
    return after[:colon_idx] if colon_idx != -1 else after or None


def _module_name_from_node_id(node_id: str) -> str | None:
    """Extract module/package name from an import edge's target node ID.

    Handles:
      external:module:os          → "os"
      external:module:json        → "json"
      file:src/other.py           → "src.other"
    """
    if node_id.startswith("external:"):
        parts = node_id.split(":")
        return parts[-1] if parts else None
    fp = _file_path_from_node_id(node_id)
    return _path_to_module(fp) if fp else None


def _symbol_name_from_node_id(node_id: str) -> str | None:
    """Extract symbol name from a node ID.

    For "method:file:path:ClassName:method_name:42" → "method_name"
    For "class:file:path:ClassName"                  → "ClassName"
    """
    parts = node_id.split(":")
    if not parts:
        return None
    last = parts[-1]
    if last.isdigit() and len(parts) >= 2:
        return parts[-2] or None
    return last or None


def _extract_parent(
    node_id: str, path: str, name: str, kind: str
) -> tuple[str | None, str | None]:
    """Parse parent class/module from a Method node ID.

    Method node format: method:file:<path>:<ParentClass>:<name>[:<line>]
    Returns (parent_name, parent_kind) or (None, None) for top-level symbols.
    """
    if kind not in {"Method"}:
        return None, None

    file_marker = f":file:{path}:"
    idx = node_id.find(file_marker)
    if idx == -1:
        return None, None

    after = node_id[idx + len(file_marker):]
    parts = after.split(":")
    # Expected: [ParentClass, method_name, line] or [ParentClass, method_name]
    if len(parts) >= 2:
        potential_parent = parts[0]
        if potential_parent and not potential_parent.isdigit() and potential_parent != name:
            return potential_parent, "Class"

    return None, None


# ── Content extraction helpers ────────────────────────────────────────────────

def _extract_docstring(lines: list[str], language: str) -> str | None:
    """Extract the first docstring/doc-comment from chunk lines.

    Handles Python triple-quotes, TypeScript JSDoc, and Rust /// comments.
    The first line is the signature — search starts from line index 1.
    """
    if len(lines) < 2:
        return None

    rest = lines[1:]
    stripped = [ln.strip() for ln in rest]

    if language == "python":
        for delim in ('"""', "'''"):
            if not stripped:
                break
            first = stripped[0]
            if not first.startswith(delim):
                continue
            content = first[len(delim):]
            end_idx = content.find(delim)
            if end_idx != -1:
                return content[:end_idx].strip() or None
            # Multi-line docstring
            doc_lines: list[str] = []
            if content.strip():
                doc_lines.append(content.strip())
            for ln in stripped[1:]:
                end = ln.find(delim)
                if end != -1:
                    tail = ln[:end].strip()
                    if tail:
                        doc_lines.append(tail)
                    break
                doc_lines.append(ln)
            result = " ".join(doc_lines)
            return result[:300] if result else None

    elif language == "typescript":
        joined = "\n".join(stripped[:20])
        m = re.search(r"/\*\*(.*?)\*/", joined, re.DOTALL)
        if m:
            raw = re.sub(r"\s*\*\s?", " ", m.group(1)).strip()
            return raw[:300] if raw else None
        # Single-line // comment directly after signature
        if stripped and stripped[0].startswith("//"):
            return stripped[0][2:].strip()[:300] or None

    elif language == "rust":
        doc_lines = []
        for ln in stripped[:20]:
            if ln.startswith("///"):
                doc_lines.append(ln[3:].strip())
            else:
                break
        if doc_lines:
            return " ".join(doc_lines)[:300]

    return None


def _path_to_module(rel_path: str) -> str:
    """Convert a workspace-relative file path to dot-notation module path.

    "src/auth/client.py" → "src.auth.client"
    "apps/editor-client/src/index.ts" → "apps.editor-client.src.index"
    """
    p = Path(rel_path)
    parts = list(p.parts)
    if parts:
        stem = parts[-1]
        parts[-1] = stem.rsplit(".", 1)[0] if "." in stem else stem
    return ".".join(parts)
