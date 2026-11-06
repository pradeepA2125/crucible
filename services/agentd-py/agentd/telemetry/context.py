"""Context the span helpers read. Imports nothing from OpenTelemetry, so the call sites that
set these values (the Auto judge, the memory curator, the eval runner) cost nothing with
tracing off."""
from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from types import MappingProxyType
from typing import Literal

CallPurpose = Literal["agent", "auto_judge", "memory_curator", "memory_summary", "eval_judge"]

# Why a model call is made: the agent's own reasoning, or a call Crucible makes for itself
# (so its cost is never mistaken for the agent's).
CALL_PURPOSE: ContextVar[CallPurpose] = ContextVar("crucible_call_purpose", default="agent")
# Attributes the eval runner puts on every turn span of a case (crucible.eval_run_id, …).
EVAL_TAGS: ContextVar[Mapping[str, str]] = ContextVar(
    "crucible_eval_tags", default=MappingProxyType({}))
