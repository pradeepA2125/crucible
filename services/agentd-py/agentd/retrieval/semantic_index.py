"""Semantic code search index backed by LanceDB.

Builds and queries an ANN vector index over CodeChunk objects. Delta indexing
tracks file modification times so only changed files are re-embedded.

Requires the optional `semantic` extras:
    pip install -e ".[semantic]"   (lancedb, sentence-transformers)

When CRUCIBLE_SEMANTIC_RETRIEVAL is not set or false, this module is never
imported and the retrieval pipeline falls back to pure graph scoring.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentd.retrieval.chunker import CodeChunk, CodeChunker, ScoredChunk

logger = logging.getLogger(__name__)

# Embedding dimension by model name — used to define the LanceDB schema.
# Unknown models default to 768.
_KNOWN_DIMS: dict[str, int] = {
    "all-MiniLM-L6-v2": 384,
    "all-MiniLM-L12-v2": 384,
    "all-mpnet-base-v2": 768,
    "nomic-ai/nomic-embed-code": 3584,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-large-en-v1.5": 1024,
}

_TABLE_NAME = "code_chunks"


@dataclass
class IndexStats:
    total_chunks: int
    updated_files: int
    skipped_files: int
    build_time_ms: int

    def __str__(self) -> str:
        return (
            f"IndexStats(total={self.total_chunks}, updated={self.updated_files}, "
            f"skipped={self.skipped_files}, time={self.build_time_ms}ms)"
        )


class SemanticIndex:
    """LanceDB-backed semantic search index for code chunks.

    Lifecycle:
    1. Call build_or_update() when a new snapshot is available.
       Only files whose mtime changed are re-embedded (delta update).
    2. Call query() / chunks_for_file() to retrieve relevant chunks.
    3. is_ready() returns True once the table exists and is queryable.
    """

    def __init__(
        self,
        index_path: str | Path,
        *,
        model_name: str = "BAAI/bge-small-en-v1.5",
        embed_batch_size: int = 64,
        max_chunk_lines: int = 120,
    ) -> None:
        self._index_path = Path(index_path)
        self._model_name = model_name
        self._embed_batch_size = embed_batch_size
        self._chunker = CodeChunker(max_chunk_lines=max_chunk_lines)
        self._model: Any = None     # lazy-loaded SentenceTransformer
        self._table: Any = None     # lazy-loaded LanceDB table
        self._embed_dim = _KNOWN_DIMS.get(model_name, 768)

    @classmethod
    def from_env(cls, workspace_path: str | Path | None = None) -> "SemanticIndex":
        import os
        raw_path = os.getenv("CRUCIBLE_VECTOR_INDEX_PATH", ".ai-editor/vector-index")
        index_path: Path
        if Path(raw_path).is_absolute():
            index_path = Path(raw_path)
        elif workspace_path is not None:
            index_path = Path(workspace_path).resolve() / raw_path
        else:
            index_path = Path(raw_path)
        return cls(
            index_path=index_path,
            model_name=os.getenv("CRUCIBLE_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
            embed_batch_size=int(os.getenv("CRUCIBLE_EMBED_BATCH_SIZE", "64")),
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def build_or_update(
        self,
        workspace_path: str | Path,
        snapshot_payload: dict[str, object],
    ) -> IndexStats:
        """Incrementally build or update the index from a snapshot.

        1. Build chunks from snapshot boundaries + workspace file reads.
        2. Compare per-file mtimes with the stored index state.
        3. Delete stale chunks for changed files.
        4. Embed and insert new chunks for changed files.
        5. Skip files with unchanged mtimes entirely.
        """
        start = time.time()
        workspace = Path(workspace_path).resolve()

        index_path = self._resolve_index_path(workspace)
        table = self._get_or_create_table(index_path)

        all_chunks = self._chunker.build(workspace, snapshot_payload)
        if not all_chunks:
            return IndexStats(total_chunks=0, updated_files=0, skipped_files=0, build_time_ms=0)

        # Determine which files need re-embedding
        existing_mtimes = self._get_existing_mtimes(table)
        files_to_update: set[str] = set()
        all_file_paths: set[str] = set()
        for chunk in all_chunks:
            if chunk.path in all_file_paths:
                continue
            all_file_paths.add(chunk.path)
            stored = existing_mtimes.get(chunk.path)
            if stored is None or abs(stored - chunk.file_mtime) > 0.001:
                files_to_update.add(chunk.path)

        skipped = len(all_file_paths) - len(files_to_update)

        if not files_to_update:
            elapsed = int((time.time() - start) * 1000)
            return IndexStats(
                total_chunks=len(all_chunks),
                updated_files=0,
                skipped_files=skipped,
                build_time_ms=elapsed,
            )

        self._delete_chunks_for_files(table, files_to_update)

        new_chunks = [c for c in all_chunks if c.path in files_to_update]
        self._embed_and_insert(table, new_chunks)

        elapsed = int((time.time() - start) * 1000)
        logger.info(
            "Semantic index updated: %d files re-embedded, %d skipped, %dms",
            len(files_to_update), skipped, elapsed,
        )
        return IndexStats(
            total_chunks=len(all_chunks),
            updated_files=len(files_to_update),
            skipped_files=skipped,
            build_time_ms=elapsed,
        )

    def query(
        self,
        text: str,
        *,
        top_k: int = 20,
        path_filter: list[str] | None = None,
        exclude_tests: bool = False,
        kind_filter: list[str] | None = None,
    ) -> list[ScoredChunk]:
        """ANN search over the full index.

        Returns up to top_k chunks ordered by cosine similarity to text.
        Filters are applied post-ANN (cheap for the sizes involved here).
        """
        if self._table is None or not text.strip():
            return []

        try:
            vec = self._embed_single(text)
            # Over-fetch to allow for post-filters
            fetch_k = top_k * 4 if (path_filter or exclude_tests or kind_filter) else top_k
            rows = (
                self._table
                .search(vec, vector_column_name="vector")
                .metric("cosine")
                .limit(fetch_k)
                .to_list()
            )

            if path_filter:
                path_set = set(path_filter)
                rows = [r for r in rows if r.get("path", "") in path_set]
            if exclude_tests:
                rows = [r for r in rows if not r.get("is_test", False)]
            if kind_filter:
                kind_set = set(kind_filter)
                rows = [r for r in rows if r.get("kind", "") in kind_set]

            return [self._row_to_scored(r) for r in rows[:top_k]]
        except Exception:
            logger.warning("Semantic query failed", exc_info=True)
            return []

    def chunks_for_file(
        self,
        path: str,
        *,
        query: str,
        top_k: int = 5,
    ) -> list[ScoredChunk]:
        """Return the top_k most relevant chunks from one specific file."""
        return self.query(query, top_k=top_k, path_filter=[path])

    def is_ready(self) -> bool:
        """True once the LanceDB table is open and queryable."""
        return self._table is not None

    # ── Private helpers ────────────────────────────────────────────────────

    def _resolve_index_path(self, workspace: Path) -> Path:
        if self._index_path.is_absolute():
            return self._index_path
        return workspace / self._index_path

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
                self._model = SentenceTransformer(self._model_name)
                # Update dim from actual model in case it differs from our table
                get_dim = getattr(
                    self._model, "get_embedding_dimension",
                    getattr(self._model, "get_sentence_embedding_dimension", None),
                )
                if get_dim is not None:
                    self._embed_dim = get_dim() or self._embed_dim
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is required for semantic retrieval. "
                    "Install with: pip install 'crucible-agentd[semantic]'"
                ) from exc
        return self._model

    def _embed_single(self, text: str) -> list[float]:
        model = self._get_model()
        vec = model.encode([text], normalize_embeddings=True)[0]
        return vec.tolist()

    def _get_or_create_table(self, index_path: Path) -> Any:
        try:
            import lancedb  # type: ignore[import-untyped]
            import pyarrow as pa  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "lancedb is required for semantic retrieval. "
                "Install with: pip install 'crucible-agentd[semantic]'"
            ) from exc

        index_path.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(index_path))

        # Try to open existing table first — avoids loading the model just to check schema.
        try:
            table = db.open_table(_TABLE_NAME)
            self._table = table
            return table
        except Exception:
            pass

        # Table doesn't exist: load model now so self._embed_dim reflects the true
        # output dimension (important for models not in _KNOWN_DIMS).
        self._get_model()

        schema = pa.schema([
            pa.field("chunk_id", pa.string()),
            pa.field("path", pa.string()),
            pa.field("language", pa.string()),
            pa.field("line_start", pa.int32()),
            pa.field("line_end", pa.int32()),
            pa.field("line_count", pa.int32()),
            pa.field("name", pa.string()),
            pa.field("kind", pa.string()),
            pa.field("signature", pa.string()),
            pa.field("parent_name", pa.string()),   # "" when None
            pa.field("parent_kind", pa.string()),   # "" when None
            pa.field("module_path", pa.string()),
            pa.field("is_top_level", pa.bool_()),
            pa.field("imports", pa.list_(pa.string())),
            pa.field("calls", pa.list_(pa.string())),
            pa.field("called_by", pa.list_(pa.string())),
            pa.field("docstring", pa.string()),     # "" when None
            pa.field("text", pa.string()),
            pa.field("text_with_lines", pa.string()),
            pa.field("context_before", pa.string()),
            pa.field("context_after", pa.string()),
            pa.field("is_test", pa.bool_()),
            pa.field("has_docstring", pa.bool_()),
            pa.field("file_mtime", pa.float64()),
            pa.field("indexed_at_ms", pa.int64()),
            pa.field("vector", pa.list_(pa.float32(), self._embed_dim)),
        ])

        table = db.create_table(_TABLE_NAME, schema=schema)
        self._table = table
        return table

    def _get_existing_mtimes(self, table: Any) -> dict[str, float]:
        """Return {path: file_mtime} for the most recent chunk of each file."""
        try:
            rows = (
                table
                .search()
                .select(["path", "file_mtime"])
                .limit(500_000)
                .to_list()
            )
            mtimes: dict[str, float] = {}
            for row in rows:
                path = str(row.get("path", ""))
                mtime = float(row.get("file_mtime", 0.0))
                if path and (path not in mtimes or mtime > mtimes[path]):
                    mtimes[path] = mtime
            return mtimes
        except Exception:
            logger.warning("Could not read existing mtimes from index", exc_info=True)
            return {}

    def _delete_chunks_for_files(self, table: Any, paths: set[str]) -> None:
        if not paths:
            return
        try:
            # LanceDB DELETE WHERE supports IN with string values
            escaped = [p.replace("'", "\\'") for p in paths]
            in_clause = ", ".join(f"'{p}'" for p in escaped)
            table.delete(f"path IN ({in_clause})")
        except Exception:
            logger.warning(
                "Failed to delete stale chunks for %d files; they will accumulate",
                len(paths),
                exc_info=True,
            )

    def _embed_and_insert(self, table: Any, chunks: list[CodeChunk]) -> None:
        if not chunks:
            return

        model = self._get_model()
        texts = [self._chunker.make_embedding_text(c) for c in chunks]

        # Batch embedding — keeps peak GPU/CPU memory bounded
        all_vectors: list[list[float]] = []
        for i in range(0, len(texts), self._embed_batch_size):
            batch = texts[i : i + self._embed_batch_size]
            vecs = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
            all_vectors.extend(v.tolist() for v in vecs)

        rows = [self._chunk_to_row(chunk, vec) for chunk, vec in zip(chunks, all_vectors)]
        table.add(rows)

        # Build ANN index for fast approximate search (skip for tiny datasets)
        if len(chunks) >= 256:
            try:
                table.create_index(
                    vector_column_name="vector",
                    metric="cosine",
                    num_partitions=min(256, max(4, len(chunks) // 40)),
                    replace=True,
                )
            except Exception:
                pass  # ANN index is an optimization; exact search still works without it

    @staticmethod
    def _chunk_to_row(chunk: CodeChunk, vector: list[float]) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "path": chunk.path,
            "language": chunk.language,
            "line_start": chunk.line_start,
            "line_end": chunk.line_end,
            "line_count": chunk.line_count,
            "name": chunk.name,
            "kind": chunk.kind,
            "signature": chunk.signature,
            "parent_name": chunk.parent_name or "",
            "parent_kind": chunk.parent_kind or "",
            "module_path": chunk.module_path,
            "is_top_level": chunk.is_top_level,
            "imports": chunk.imports,
            "calls": chunk.calls,
            "called_by": chunk.called_by,
            "docstring": chunk.docstring or "",
            "text": chunk.text,
            "text_with_lines": chunk.text_with_lines,
            "context_before": chunk.context_before,
            "context_after": chunk.context_after,
            "is_test": chunk.is_test,
            "has_docstring": chunk.has_docstring,
            "file_mtime": chunk.file_mtime,
            "indexed_at_ms": chunk.indexed_at_ms,
            "vector": vector,
        }

    @staticmethod
    def _row_to_scored(row: dict[str, Any]) -> ScoredChunk:
        chunk = CodeChunk(
            chunk_id=str(row.get("chunk_id", "")),
            path=str(row.get("path", "")),
            language=str(row.get("language", "")),
            line_start=int(row.get("line_start", 0)),
            line_end=int(row.get("line_end", 0)),
            line_count=int(row.get("line_count", 0)),
            name=str(row.get("name", "")),
            kind=str(row.get("kind", "")),
            signature=str(row.get("signature", "")),
            parent_name=row.get("parent_name") or None,
            parent_kind=row.get("parent_kind") or None,
            module_path=str(row.get("module_path", "")),
            is_top_level=bool(row.get("is_top_level", True)),
            imports=list(row.get("imports") or []),
            calls=list(row.get("calls") or []),
            called_by=list(row.get("called_by") or []),
            docstring=row.get("docstring") or None,
            text=str(row.get("text", "")),
            text_with_lines=str(row.get("text_with_lines", "")),
            context_before=str(row.get("context_before", "")),
            context_after=str(row.get("context_after", "")),
            is_test=bool(row.get("is_test", False)),
            has_docstring=bool(row.get("has_docstring", False)),
            file_mtime=float(row.get("file_mtime", 0.0)),
            indexed_at_ms=int(row.get("indexed_at_ms", 0)),
        )
        # LanceDB cosine distance: 0 = identical, 1 = orthogonal, 2 = opposite.
        # Map to similarity in [0, 1]: identical→1.0, orthogonal→0.5, opposite→0.0.
        distance = float(row.get("_distance", 2.0))
        similarity = max(0.0, (2.0 - distance) / 2.0)
        return ScoredChunk(chunk=chunk, score=similarity)
