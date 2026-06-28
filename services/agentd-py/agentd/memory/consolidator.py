from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agentd.memory.models import CandidateMemory, Memory

logger = logging.getLogger(__name__)

DistillFn = Callable[[str, list[Memory]], Awaitable[list[CandidateMemory]]]

CANDIDATE_MEMORY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["episodic", "semantic", "procedural"]},
                    "content": {"type": "string"},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer"},
                    "contradicts": {"type": ["string", "null"]},
                },
                "required": ["kind", "content", "entities", "importance"],
            },
        }
    },
    "required": ["memories"],
}

_CONSOLIDATION_SYSTEM = (
    "You distill an AI coding session into a few durable memory notes for your future self. "
    "You are given the recent transcript and any EXISTING MEMORIES (each with an id). Propose "
    "only NEW, durable notes worth recalling in a later session.\n"
    "\n"
    "Each note has a kind:\n"
    "- episodic: a specific thing that happened this session "
    "(e.g. \"User rejected the first plan and asked to keep the change minimal\"). "
    "Episodic notes are immutable — NEVER set contradicts on them.\n"
    "- semantic: a durable fact about the code/project/user "
    "(e.g. \"Patch ops are applied in patch/engine.py; it supports 7 op types\").\n"
    "- procedural: a reusable how-to / process "
    "(e.g. \"Run the backend via start-backend.sh, always quoting --workspace\"). "
    "Procedural is the hardest to spot — only record a genuinely reusable method.\n"
    "\n"
    "Rules:\n"
    "- One atomic fact per note. Keep entities exact: list the verbatim file paths and "
    "path:Symbol tokens the note is about.\n"
    "- importance: rate 1-10 how much this would help a future session (a project-wide fact = "
    "high; a one-off detail = low).\n"
    "- contradicts: set to an EXISTING MEMORY id only when your note directly conflicts with it "
    "(a fact that changed). Never for episodic.\n"
    "- Do NOT record: ephemeral chit-chat, tool mechanics, or anything obvious from the code. "
    "If nothing is worth keeping, return an empty list."
)


def _render_existing(existing: list[Memory]) -> str:
    if not existing:
        return "(none)"
    return "\n".join(f"[{m.id}] ({m.kind}) {m.content}" for m in existing)


def _parse_candidate(item: object) -> CandidateMemory | None:
    # Per-item validation: a single malformed candidate must not discard the good ones.
    if not isinstance(item, dict):
        return None
    try:
        c = CandidateMemory.model_validate(item)
    except Exception:  # noqa: BLE001
        return None
    c.importance = max(1, min(10, c.importance))  # clamp so out-of-range never skews recall
    return c


def make_engine_consolidator(transport: object, model: str) -> DistillFn:
    async def _distill(transcript: str, existing: list[Memory]) -> list[CandidateMemory]:
        payload: dict[str, object] = {
            "transcript": f"{transcript}\n\nEXISTING MEMORIES (with ids):\n"
                          f"{_render_existing(existing)}"
        }
        try:
            raw = await transport.generate_json(  # type: ignore[attr-defined]
                model=model, schema_name="consolidated_memories",
                schema=CANDIDATE_MEMORY_SCHEMA, system_instructions=_CONSOLIDATION_SYSTEM,
                user_payload=payload,
            )
        except Exception:  # noqa: BLE001 — best-effort: never break the turn
            logger.warning("[memory] consolidation distill failed for model=%s", model)
            return []
        items = raw.get("memories", []) if isinstance(raw, dict) else []
        out: list[CandidateMemory] = []
        for it in items if isinstance(items, list) else []:
            parsed = _parse_candidate(it)
            if parsed is not None:
                out.append(parsed)
        return out

    return _distill
