"""Team input validation (spec v2 §7.3). Every refusal is a TeamInputError whose message
the model sees verbatim, so each one says what was wrong and what is allowed."""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

LABEL_RE = re.compile(r"^[a-z0-9-]{1,32}$")
TEXT_MAX = 8000
MAX_POSTS_PER_ACTIVATION = 6
_MENTION_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9-]{1,32})\b")
_PROPOSAL_RE = re.compile(r"^[Pp]?(\d+)$")


class TeamInputError(ValueError):
    """Invalid team tool input; shown to the model as the tool's error."""


def check_label(label: str) -> str:
    if not LABEL_RE.match(label):
        raise TeamInputError(
            f"label {label!r} must be 1-32 characters of a-z, 0-9 and '-' (it is written @label)")
    return label


def check_text(text: object, what: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise TeamInputError(f"{what} is empty")
    if len(text) > TEXT_MAX:
        raise TeamInputError(f"{what} is {len(text)} characters; the limit is {TEXT_MAX}")
    return text.strip()


def _norm(label: str) -> str:
    return label.strip().lstrip("@").casefold()


def effective_mentions(text: str, explicit: object, roster: list[str]) -> list[str]:
    known = set(roster) | {"team"}
    found: set[str] = set()
    if explicit is not None:
        if not isinstance(explicit, list):
            raise TeamInputError("mentions must be a list of member labels")
        for raw in explicit:
            label = _norm(str(raw))
            if label not in known:
                raise TeamInputError(
                    f"unknown member {raw!r}; the team is: {', '.join(roster)} (or @team)")
            found.add(label)
    for token in _MENTION_RE.findall(text):
        label = token.casefold()
        if label in known:
            found.add(label)  # unknown @tokens are emails or handles, not mentions
    return [label for label in [*roster, "team"] if label in found]


def parse_proposal_id(raw: object) -> int:
    match = _PROPOSAL_RE.match(str(raw).strip()) if raw is not None else None
    if match is None:
        raise TeamInputError(f"{raw!r} is not a proposal id (write it as P<number>, e.g. P3)")
    return int(match.group(1))


def _canonical(workspace: Path, raw: str) -> str:
    root = workspace.resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise TeamInputError(f"evidence file {raw!r} is outside the workspace")
    return target.relative_to(root).as_posix()


def validate_evidence(
    raw: object, *, workspace: Path, assignment_files: set[str],
    post_exists: Callable[[int], bool],
) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise TeamInputError("evidence must be an object: {files, line} | {command, output} "
                             "| {quote_seq}")
    files_raw = raw.get("files") or []
    if not isinstance(files_raw, list):
        raise TeamInputError("evidence.files must be a list of paths")
    files = [_canonical(workspace, str(f)) for f in files_raw]
    line = raw.get("line")
    command = str(raw.get("command") or "").strip()
    output = str(raw.get("output") or "").strip()
    quote_seq = raw.get("quote_seq")
    has_file_line = bool(files) and isinstance(line, int)
    has_command = bool(command) and bool(output)
    has_quote = isinstance(quote_seq, int)
    if not (has_file_line or has_command or has_quote):
        raise TeamInputError(
            "evidence needs at least one of: files + line, command + output, or quote_seq")
    for path in files:
        if not (workspace.resolve() / path).is_file() and path not in assignment_files:
            raise TeamInputError(f"evidence file {path!r} does not exist")
    out: dict[str, object] = {}
    if files:
        out["files"] = files
    if isinstance(line, int):
        first = workspace.resolve() / files[0] if files else None
        if first is not None and first.is_file():
            count = len(first.read_text(encoding="utf-8", errors="replace").splitlines())
            if not 1 <= line <= count:
                raise TeamInputError(f"line {line} is out of range: {files[0]} has {count} lines")
        out["line"] = line
    if command:
        out["command"] = command
    if output:
        out["output"] = output[:4000]
    if has_quote:
        assert isinstance(quote_seq, int)
        if not post_exists(quote_seq):
            raise TeamInputError(f"quote_seq {quote_seq}: there is no post with that seq")
        out["quote_seq"] = quote_seq
    return out
