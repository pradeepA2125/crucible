"""Project words that prime Whisper's speech recognition: the open project's name and its
most referenced file and symbol names, from the index snapshot. Best effort: no snapshot,
or one that cannot be read, gives no words, and voice falls back to the coding glossary."""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_SNAPSHOT_PATH_ENV = "CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH"
_SYMBOL_KINDS = frozenset({"Class", "Interface", "Function", "Method"})
# Names Whisper would not spell on its own: camelCase, PascalCase with two capitals, or
# snake_case and kebab-case. Plain words ("build", "Error") are already in its vocabulary.
_IDENTIFIER = re.compile(r"[a-z0-9][A-Z]|[A-Z].*[A-Z]|[A-Za-z0-9][_-][A-Za-z0-9]")
_SEPARATOR = ", "
# Test files and their references: a name tests call often is not a name people say.
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*$|\.(test|spec)\.[^/]+$")


def snapshot_path_for(workspace: Path) -> Path:
    """The same snapshot the retrieval stack reads."""
    override = os.environ.get(_SNAPSHOT_PATH_ENV)
    return Path(override) if override else workspace / ".crucible" / "index-snapshot.json"


def _word_for(node: dict[str, object], root: str) -> str | None:
    name, kind, path = node.get("name"), node.get("kind"), node.get("path")
    node_id = node.get("id")
    if not isinstance(name, str) or not isinstance(path, str) or not isinstance(node_id, str):
        return None
    # `external:` nodes are calls the indexer could not resolve (library names).
    if node_id.startswith("external:") or _TEST_PATH.search(path):
        return None
    if root and not path.startswith(root):
        return None
    if kind == "File":
        word = Path(name).stem
    elif kind in _SYMBOL_KINDS:
        word = name
    else:
        return None
    if word.startswith("_") or word.lower().startswith("test") or not _IDENTIFIER.search(word):
        return None
    return word


@lru_cache(maxsize=4)
def _ranked_names(path: str, mtime_ns: int) -> tuple[str, ...]:
    """Names by how often the graph references them, most first. Cached per file version:
    a large snapshot takes most of a second to parse."""
    del mtime_ns  # part of the cache key only
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    graph = data["graph"]
    nodes, edges = graph["nodes"], graph.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("snapshot graph is not a node and edge list")
    root = data.get("workspace_root") if isinstance(data.get("workspace_root"), str) else ""
    references = Counter(
        edge.get("to") for edge in edges
        if isinstance(edge, dict) and not _TEST_PATH.search(str(edge.get("from", ""))))
    scores: Counter[str] = Counter()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        word = _word_for(node, root)
        if word is not None:
            scores[word] += references.get(node.get("id"), 0)
    return tuple(word for word, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0])))


def project_vocabulary(snapshot_path: Path, project: str, max_chars: int = 600) -> list[str]:
    """The project name, then its most referenced names, until the joined list would pass
    `max_chars`. [] when the snapshot is missing or unreadable."""
    try:
        names = _ranked_names(str(snapshot_path), snapshot_path.stat().st_mtime_ns)
    except FileNotFoundError:
        return []
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        logger.warning("[voice] project vocabulary unavailable from %s: %s", snapshot_path, error)
        return []
    words: list[str] = []
    length = 0
    for word in ([project] if project else []) + list(names):
        if word in words:
            continue
        added = len(word) + (len(_SEPARATOR) if words else 0)
        if length + added > max_chars:
            break
        words.append(word)
        length += added
    return words
