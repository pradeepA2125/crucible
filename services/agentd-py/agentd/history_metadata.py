"""Provider-independent boundary between stored evidence and public model history."""

from __future__ import annotations

Message = dict[str, object]
PRIVATE_HISTORY_KEYS = frozenset({"mseq", "_evidence"})


def project_history(history: list[Message]) -> list[Message]:
    if not any(PRIVATE_HISTORY_KEYS.intersection(message) for message in history):
        return history
    return [
        {key: value for key, value in message.items() if key not in PRIVATE_HISTORY_KEYS}
        for message in history
    ]


def message_sequence(message: Message) -> int | None:
    value = message.get("mseq")
    return value if type(value) is int and value > 0 else None
