from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class IntentType(StrEnum):
    QA = "qa"
    SMALL_CHANGE = "small_change"
    LARGE_CHANGE = "large_change"


class IntentClassification(BaseModel):
    intent: IntentType
    rationale: str
    files_examined: list[str] = Field(default_factory=list)
    likely_targets: list[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: Literal["user", "agent"]
    content: str
    type: Literal["text", "plan_card", "diff_card", "diff_summary"] = "text"
    task_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatThread(BaseModel):
    thread_id: str
    workspace_path: str
    title: str = "New Chat"
    messages: list[ChatMessage] = Field(default_factory=list)
    touched_files: list[str] = Field(default_factory=list)


class ChatEvent(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
