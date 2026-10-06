"""Team input validation (spec v2 §7.3). Every refusal is a TeamInputError whose message
the model sees verbatim, so each one says what was wrong and what is allowed."""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from agentd.chat.protected_paths import is_protected

LABEL_RE = re.compile(r"^[a-z0-9-]{1,32}$")
TEXT_MAX = 8000
MAX_POSTS_PER_ACTIVATION = 6
_MENTION_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9-]{1,32})\b")
_PROPOSAL_RE = re.compile(r"^[Pp]?(\d+)$")


class TeamInputError(ValueError):
    """Invalid team tool input; shown to the model as the tool's error."""


class TeamPlanConflict(ValueError):
    """The team_plan card no longer matches the team (route → 409)."""


class TeamPlanInvalid(ValueError):
    """A team_plan decision the route cannot accept as sent (route → 422)."""


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


def parse_assignments(raw: object, roster: list[str]) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        raise TeamInputError("assignments must be a list of {member, part, files}")
    parts: list[dict[str, object]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TeamInputError(f"assignments[{i}] must be an object")
        member = str(item.get("member", "")).lstrip("@").casefold()
        if member not in roster:
            raise TeamInputError(
                f"assignments[{i}]: unknown member {item.get('member')!r}; "
                f"the team is: {', '.join(roster)}")
        files = item.get("files") or []
        if not isinstance(files, list):
            raise TeamInputError(f"assignments[{i}].files must be a list of paths")
        parts.append({"member": member, "part": check_text(item.get("part"), "part"),
                      "files": [str(f) for f in files]})
    return parts


def _assignable(workspace: Path, raw: str) -> str:
    root = workspace.resolve()
    target = (root / raw).resolve()
    if target == root or root not in target.parents:
        raise TeamInputError(f"{raw!r} is outside the workspace")
    key = target.relative_to(root).as_posix()
    if is_protected(key):
        raise TeamInputError(f"{key} is a protected Crucible configuration file; no "
                             "assignment can include it")
    return key


def check_assignments(
    parts: list[dict[str, object]], shared: list[str], *, workspace: Path,
    can_edit: Callable[[str], bool],
) -> tuple[list[dict[str, object]], list[str]]:
    """Spec v2 §8.3: canonical paths inside the workspace, never protected, one owner per
    file, no file both owned and shared, and only members that can edit own files."""
    owner: dict[str, str] = {}
    seen_members: set[str] = set()
    out: list[dict[str, object]] = []
    for part in parts:
        member = str(part["member"])
        if member in seen_members:
            raise TeamInputError(f"one assignment per member: merge {member}'s parts into one")
        seen_members.add(member)
        raw_files = part.get("files")
        files = [_assignable(workspace, str(f)) for f in
                 (raw_files if isinstance(raw_files, list) else [])]
        if files and not can_edit(member):
            raise TeamInputError(
                f"{member} cannot edit files (its agent definition is read-only); give its "
                "files to a member that can edit, or give it a part without files")
        for key in files:
            if key in owner and owner[key] != member:
                raise TeamInputError(
                    f"{key} is assigned to both {owner[key]} and {member}; each file has one "
                    "owner (list it under shared_files to let both edit it)")
            owner[key] = member
        out.append({**part, "files": sorted(set(files), key=files.index)})
    shared_keys = [_assignable(workspace, str(f)) for f in shared]
    for key in shared_keys:
        if key in owner:
            raise TeamInputError(f"{key} is both assigned to {owner[key]} and shared; pick one")
    return out, sorted(set(shared_keys), key=shared_keys.index)
