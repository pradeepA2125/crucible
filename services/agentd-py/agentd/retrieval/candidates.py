"""Bounded metadata catalogs for on-demand code discovery. Never delivers source code."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

MAX_FILES = 8
MAX_SYMBOLS = 3
MAX_RELATIONS = 6
MAX_CHARS = 5_000
_PATH = re.compile(
    r"(?<![\w./:@-])(?:[\w.@-]+/)+[\w.@-]+\.(?:py|tsx?|jsx?|mjs|cjs|rs|go|java)"
    r"(?![\w/-]|\.\w)"
)


def explicit_paths(query: str) -> list[str]:
    return list(dict.fromkeys(
        match.group() for match in _PATH.finditer(query)
        if ".." not in PurePosixPath(match.group()).parts
    ))


def render_candidates(
    candidates: list[dict[str, Any]], relationships: list[dict[str, str]], *, query: str,
    include_explicit: bool = True,
) -> str:
    """Schema-once rows, bounded as whole entries, with indexed relation endpoints."""
    paths = explicit_paths(query)[:MAX_FILES] if include_explicit else []
    items: dict[str, dict[str, Any]] = {path: {"path": path} for path in paths}
    for candidate in candidates:
        path = candidate.get("path")
        if not isinstance(path, str) or not path:
            continue
        if path in items:
            items[path] = candidate
        elif len(items) < MAX_FILES:
            items[path] = candidate
    if not items:
        return ""

    def encode(rows: list[list[Any]], links: list[list[Any]]) -> str:
        data: dict[str, Any] = {
            "columns": ["path", "symbols", "ranges"],
            "symbol_columns": ["name", "kind", "line"],
            "candidates": rows,
        }
        if links:
            data["relation_columns"] = ["from_index", "to_index", "kind"]
            data["relations"] = links
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    rows: list[list[Any]] = []
    delivered_paths: list[str] = []
    for path, item in items.items():
        symbols = [
            [symbol.get("name"), symbol.get("kind"), symbol.get("line")]
            for symbol in item.get("symbols", [])[:MAX_SYMBOLS]
        ]
        ranges = [
            bounds for bounds in item.get("ranges", [])[:MAX_SYMBOLS]
            if (isinstance(bounds, list) and len(bounds) == 2
                and type(bounds[0]) is int and type(bounds[1]) is int
                and 0 < bounds[0] <= bounds[1])
        ]
        row = [path, symbols, ranges]
        if len(encode([*rows, row], [])) <= MAX_CHARS:
            rows.append(row)
            delivered_paths.append(path)
    if not rows:
        return ""
    index = {path: i for i, path in enumerate(delivered_paths)}
    links: list[list[Any]] = []
    for relation in relationships:
        source, target, kind = relation.get("from"), relation.get("to"), relation.get("kind")
        if source not in index or target not in index or not isinstance(kind, str):
            continue
        link = [index[source], index[target], kind]
        if link not in links and len(links) < MAX_RELATIONS:
            if len(encode(rows, [*links, link])) <= MAX_CHARS:
                links.append(link)
    return encode(rows, links)


def format_catalog(text: str, *, max_chars: int = MAX_CHARS) -> str:
    """Display discovery metadata as compact rows; never add source or invent ranges.

    The internal JSON carries indexed graph endpoints for this result.
    Whole rows/relations fit the output budget; cell JSON escapes pipes/newlines.
    """
    if not text:
        return ""
    data = json.loads(text)
    rows = data.get("candidates", [])
    if not rows:
        return ""
    header = ("Code candidates (hints; may be unrelated).\n"
              "row|path|symbols[name,kind,line]|ranges[start,end]")
    footer = ("Source: read_file. More: search_code; "
              "search_semantic/query_graph if available.")

    def cell(value: Any) -> str:
        return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                .replace("|", "\\u007c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))

    lines = [header]
    delivered = 0
    for index, row in enumerate(rows):
        line = str(index) + "|" + "|".join(cell(value) for value in row)
        if len("\n".join([*lines, line, footer])) > max_chars:
            break
        lines.append(line)
        delivered += 1
    if not delivered:
        return ""
    relation_header = "relations[from_index,to_index,kind]"
    links: list[str] = []
    for a, b, kind in data.get("relations", []):
        if type(a) is int and type(b) is int and 0 <= a < delivered and 0 <= b < delivered:
            line = cell([a, b, kind])
            if len("\n".join([*lines, relation_header, *links, line, footer])) <= max_chars:
                links.append(line)
    if links:
        lines.extend([relation_header, *links])
    return "\n".join([*lines, footer])
