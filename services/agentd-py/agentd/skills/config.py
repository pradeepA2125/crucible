from __future__ import annotations

import os


def _pos_int(env: str, default: int) -> int:
    raw = os.getenv(env, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def skills_catalog_max_chars() -> int:
    return _pos_int("AI_EDITOR_SKILLS_CATALOG_MAX_CHARS", 16000)


def skills_body_max_chars() -> int:
    return _pos_int("AI_EDITOR_SKILLS_BODY_MAX_CHARS", 20000)
