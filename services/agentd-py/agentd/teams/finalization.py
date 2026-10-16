"""Validation and explicit input for a lead's final recommendation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from agentd.subagents.framing import frame
from agentd.teams.models import TeamPost, TeamRecord
from agentd.teams.validation import TeamInputError, check_assignments, parse_assignments

if TYPE_CHECKING:
    from agentd.teams.service import TeamService
    from agentd.teams.store import TeamStore


def validate_final_result(
    text: str, metadata: object, basis_proposal_id: str | None
) -> dict[str, str]:
    if not isinstance(metadata, dict):
        raise TeamInputError(
            "Finalization requires final_result with summary, outcome and basis_proposal_id"
        )
    if not text.strip() or len(text) > 20_000:
        raise TeamInputError("Final result Markdown must contain 1 to 20,000 characters")
    short = metadata.get("summary")
    if not isinstance(short, str) or not short.strip() or len(short) > 1000:
        raise TeamInputError("final_result.summary must contain 1 to 1,000 characters")
    outcome = metadata.get("outcome")
    if outcome not in ("actionable", "no_change", "needs_decision"):
        raise TeamInputError("final_result.outcome must be actionable, no_change or needs_decision")
    if not basis_proposal_id or metadata.get("basis_proposal_id") != basis_proposal_id:
        raise TeamInputError("final_result.basis_proposal_id must match the selected proposal")
    return {
        "summary": short.strip(),
        "outcome": str(outcome),
        "basis_proposal_id": basis_proposal_id,
    }


def fingerprint_files(workspace: Path, paths: list[str]) -> dict[str, str | None]:
    root = workspace.resolve()
    result: dict[str, str | None] = {}
    for raw in paths:
        path = (root / raw).resolve()
        if root not in path.parents:
            raise TeamInputError(f"execution path {raw!r} is outside the workspace")
        if path.exists() and not path.is_file():
            raise TeamInputError(f"execution path {raw!r} must be a file")
        result[path.relative_to(root).as_posix()] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
    return result


def changed_fingerprints(workspace: Path, snapshot: dict[str, str | None]) -> list[str]:
    current = fingerprint_files(workspace, list(snapshot))
    # Resolving a replaced symlink can change the canonical key itself. That is
    # file drift, including when the new target has identical contents.
    return [
        path for path, value in snapshot.items() if path not in current or current[path] != value
    ]


def recommendation_fingerprints(
    workspace: Path, paths: list[str]
) -> tuple[dict[str, str | None], list[str]]:
    """Planning may name directory scopes without promising executable file ownership."""
    root = workspace.resolve()
    files: list[str] = []
    directories: list[str] = []
    for raw in paths:
        path = (root / raw).resolve()
        if root not in path.parents:
            raise TeamInputError(f"planning path {raw!r} is outside the workspace")
        if path.is_dir():
            directories.append(path.relative_to(root).as_posix())
        else:
            files.append(raw)
    return fingerprint_files(root, files), sorted(set(directories))


def validate_execution(service: TeamService, team: TeamRecord, proposal: TeamPost | None) -> None:
    if team.mode == "deliberate":
        raise TeamInputError("Deliberate mode cannot start implementation")
    if proposal is None or proposal.kind != "proposal":
        raise TeamInputError("Implementation requires a supplied proposal")
    parts = parse_assignments(
        proposal.payload.get("assignments", []),
        [m.label for m in service._store.members(team.team_id)],
    )
    if not parts:
        raise TeamInputError("Implementation requires nonempty executable assignments")
    check_assignments(
        parts,
        proposal.payload.get("shared_files", []),
        workspace=service.workspace,
        can_edit=lambda label: service.member_can_edit(team.team_id, label),
    )
    snapshot = proposal.payload.get("file_fingerprints")
    if isinstance(snapshot, dict):
        changed = changed_fingerprints(service.workspace, snapshot)
        if changed:
            raise TeamInputError("stale_plan: execution files changed: " + ", ".join(changed))
    # Combined workflows fingerprint their accepted result. Check before consuming an
    # approval gate as well as immediately before starting executable activations.
    result = service._store.get_post(team.team_id, team.result_seq or 0)
    if (
        result
        and result.payload.get("run_id") == team.run_id
        and result.payload.get("basis_proposal_id") == proposal.proposal_id
    ):
        changed = changed_fingerprints(
            service.workspace, result.payload.get("file_fingerprints", {})
        )
        if changed:
            raise TeamInputError("stale_plan: execution files changed: " + ", ".join(changed))


def finalization_input(store: TeamStore, team: TeamRecord) -> str:
    selected = team.adopted_proposal_id
    lines = [
        "Finalize the requested deliverable. Do not implement or silently change its scope.",
        f"Original request: {team.goal}",
        f"Deliverable: {team.deliverable_kind or 'recommendation'}: "
        f"{team.deliverable_description or team.goal}",
        f"Selected basis: {selected}; mode: {team.mode}; run: {team.run_id}.",
        "If the original request asks for documents, write and save them in the workspace "
        "using ordinary edits before reporting. Save a spec when requested, and a plan if "
        "requested or needed. Use requested paths or suitable project documentation paths. "
        "Include saved file paths in your report so main can read them. This finalization "
        "permission is for requested documents; do not implement the proposed feature or fix. "
        "If an edit is blocked or rejected, report that accurately instead of claiming "
        "it was saved.",
    ]
    for post in store.posts(team.team_id):
        if post.kind in ("report", "result") or post.recipient is not None:
            continue
        lines.append(
            frame(
                post.author,
                f"{post.kind} #{post.seq}",
                post.text + ("\n" + json.dumps(post.payload) if post.kind == "proposal" else ""),
            )
        )
    for label, post in store.current_reports(team.team_id, team.run_id).items():
        lines.append(frame(label, "current-run member report", post.text))
    lines.append(
        "End with report(status=completed, summary=<full Markdown>, final_result={summary, "
        "outcome: actionable|no_change|needs_decision, basis_proposal_id}). "
        "For material revisions use report(status=completed, finalization_feedback={reason}) "
        "instead of final_result. Report partial only if blocked; that pauses finalization."
    )
    return "\n\n".join(lines)
