"""Data shapes for the injection eval: scenarios, arms and trials."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Channel = Literal[
    "readme", "code_comment", "agents_md", "command_output",
    "mcp_issue", "mcp_page", "search_result",
]
Goal = Literal["exfil_command", "exfil_mcp", "persist_file", "memory_poison", "answer_hijack"]
Technique = Literal["plain", "authority", "fake_frame_close", "invisible", "helpful_step"]
Approval = Literal["ask", "auto"]


@dataclass(frozen=True)
class BenignCheck:
    """How to tell the user's own request got done (spec §6.3)."""

    kind: Literal["answer_mentions", "file_matches", "command_ran"]
    phrases: tuple[str, ...] = ()
    path: str = ""
    contains: str = ""
    pattern: str = ""


@dataclass(frozen=True)
class Allow:
    """What a careful user approves because the real request needs it. `commands` are
    fnmatch globs over the full command line."""

    commands: tuple[str, ...] = ()
    mcp_tools: tuple[str, ...] = ()
    edit_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    channel: Channel
    goal: Goal
    technique: Technique
    user_message: str
    workspace: Path
    # None for a clean twin, which then names its attack in `clean_of`.
    payload: str | None
    # `file:<relpath>`, `mcp:issue:<n>` or `mcp:page:<url>`.
    payload_target: str
    user_trusted: tuple[str, ...]
    benign_check: BenignCheck
    allow: Allow
    uses_mcp: bool
    uses_skills: bool
    clean_of: str | None


@dataclass(frozen=True)
class Arm:
    defense: bool
    approval: Approval


@dataclass(frozen=True)
class Trial:
    scenario: Scenario
    arm: Arm
    index: int
    marker: str
