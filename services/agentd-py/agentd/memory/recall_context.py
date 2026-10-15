"""Bounded recall context and substantive closing selection, without model loading."""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

PAIR_TOKENS = 512
MEMORY_TOKENS = 256
QUERY_TOKENS = 380


def clean_closing(text: str) -> str:
    # Compatibility for notices persisted before system_notice metadata existed.
    # Do not discard legitimate task answers merely because they contain a warning.
    legacy_notices = (
        "⚠️ Usage limit reached.", "⚠️ The turn failed and had to stop:",
        "⚠️ ChatGPT plan usage isn't available", "⚠️ Your ChatGPT sign-in has ended.",
        "⚠️ ChatGPT plan usage doesn't support", "⚠️ ChatGPT refused Crucible's request",
    )
    stripped = text.strip()
    if stripped.startswith(legacy_notices) or stripped in {"Stopped", "✗ Stopped"}:
        return ""
    return text


def previous_closing(messages: Iterable[Mapping[str, object]]) -> str:
    """Consume newest-first messages; skip notices, progress and unfinished answers."""
    for message in messages:
        metadata = message.get("metadata")
        if isinstance(metadata, Mapping) and any(metadata.get(key) for key in (
            "system_notice", "breadcrumb", "progress", "inflight_turn_id",
        )):
            continue
        if message.get("role") == "agent" and message.get("type", "text") == "text":
            text = clean_closing(str(message.get("content") or ""))
            if text.strip():
                return text[:600]
    return ""


class TokenCodec(Protocol):
    def encode(self, text: str, **kwargs: Any) -> list[int]: ...
    def decode(self, tokens: list[int]) -> str: ...
    def num_special_tokens_to_add(self, pair: bool = False) -> int: ...


class CharacterCodec:
    """Deterministic budget for injected test scorers; real models use their tokenizer."""
    def encode(self, text: str, **kwargs: Any) -> list[int]:
        return list(text.encode("utf-8"))

    def decode(self, tokens: list[int]) -> str:
        return bytes(tokens).decode("utf-8", errors="ignore")

    def num_special_tokens_to_add(self, pair: bool = False) -> int:
        return 4


@dataclass
class ContextRanking:
    scores: list[float]
    query: str
    truncated_signals: list[str]
    truncated_documents: list[int]
    max_pair_tokens: int


def _clip(text: str, budget: int, codec: TokenCodec) -> tuple[str, bool]:
    tokens = codec.encode(text, add_special_tokens=False)
    if len(tokens) <= budget:
        return text, False
    head = math.ceil(budget * .67)
    tail = max(0, budget - head - 3)
    value = codec.decode(tokens[:head]) + " … " + codec.decode(tokens[-tail:] if tail else [])
    # Retokenization can add boundaries around a decoded excerpt.
    while len(codec.encode(value, add_special_tokens=False)) > budget:
        if tail:
            tail -= 1
        elif head:
            head -= 1
        else:
            return "", True
        value = codec.decode(tokens[:head]) + " … " + codec.decode(tokens[-tail:] if tail else [])
    return value, True


def context_pairs(
    signals: Mapping[str, str], documents: list[str], codec: TokenCodec,
) -> tuple[list[tuple[str, str]], ContextRanking]:
    """Match the evaluated RCQ/body input; cap oversized documents explicitly."""
    clipped = [_clip(document, MEMORY_TOKENS, codec) for document in documents]
    bodies = [body for body, _ in clipped]
    active = {key: text for key, text in signals.items() if text.strip()}
    labels = {"Q": "Current request", "C": "Previous substantive answer", "R": "Thread recap"}
    header = "\n\n".join(labels[key] + ":\n" for key in "RCQ" if key in active)
    special = codec.num_special_tokens_to_add(pair=True)
    overhead = max(28, len(codec.encode(header, add_special_tokens=False)) + 4)
    longest = max((len(codec.encode(body, add_special_tokens=False)) for body in bodies), default=0)
    budget = min(QUERY_TOKENS, PAIR_TOKENS - special - longest - overhead)
    if budget < 1:
        raise ValueError("Recall context cannot fit the reranker input budget")
    weights = {"Q": .65, "C": .20, "R": .15}
    total = sum(weights[key] for key in active)
    parts, truncated = [], []
    for key in "RCQ":
        if key not in active:
            continue
        value, shortened = _clip(active[key], int(budget * weights[key] / total), codec)
        parts.append(labels[key] + ":\n" + value)
        if shortened:
            truncated.append(key)
    query = "\n\n".join(parts)
    length = len(codec.encode(query, add_special_tokens=False)) + longest + special
    if length > PAIR_TOKENS:
        raise ValueError("Combined recall input exceeds the reranker pair budget")
    return [(query, body) for body in bodies], ContextRanking(
        scores=[], query=query, truncated_signals=truncated,
        truncated_documents=[i for i, (_, shortened) in enumerate(clipped) if shortened],
        max_pair_tokens=length,
    )
