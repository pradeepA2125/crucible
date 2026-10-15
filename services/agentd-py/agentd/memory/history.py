"""Private evidence metadata and durable message identity for v2 controller history."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Iterable
from typing import Self, SupportsIndex, overload

from agentd.history_metadata import Message, message_sequence

SequenceAllocator = Callable[[int, int], int]
logger = logging.getLogger(__name__)


class EvidenceHistory(list[Message]):
    """Append stamps the original dict, so pending tool slots remain updateable in place.

    The allocator commits reservations before history is persisted. Crash gaps are harmless;
    committed IDs cannot be reused after compaction, restart or rewind. Synthetic anchors have
    private kind metadata and no source sequence: they restate earlier evidence.
    """

    def __init__(self, messages: Iterable[Message], allocate: SequenceAllocator) -> None:
        self._allocate = allocate
        entries = [dict(message) for message in messages]
        self._prepare(entries)
        super().__init__(entries)

    def __copy__(self) -> list[Message]:
        return list(self)

    def __deepcopy__(self, memo: dict[int, object]) -> list[Message]:
        # Image/provider projections copy data, never the allocator or SQLite connection.
        result: list[Message] = []
        memo[id(self)] = result
        result.extend(copy.deepcopy(message, memo) for message in self)
        return result

    def _prepare(self, messages: list[Message]) -> None:
        existing = [seq for message in messages if (seq := message_sequence(message)) is not None]
        if len(existing) != len(set(existing)):
            raise ValueError("Duplicate evidence message sequences")
        pending = []
        for message in messages:
            evidence = message.get("_evidence")
            if isinstance(evidence, dict):
                if evidence.get("kind") == "compaction_summary" or evidence.get(
                    "source_identity_unavailable"
                ):
                    continue
            if message_sequence(message) is None:
                pending.append(message)
        try:
            start = self._allocate(len(pending), max(existing, default=0) + 1)
        except Exception:
            # Evidence persistence must not fail an ordinary chat turn. Do not invent
            # temporary IDs that could later collide with a committed source ID.
            logger.warning("[memory] message sequence allocation unavailable", exc_info=True)
            for message in pending:
                previous = message.get("_evidence")
                evidence = previous if isinstance(previous, dict) else {}
                message.pop("mseq", None)
                message["_evidence"] = {**evidence, "source_identity_unavailable": True}
            return
        for index, message in enumerate(pending):
            message["mseq"] = start + index

    def append(self, message: Message) -> None:
        if message_sequence(message) is not None:
            message = {key: value for key, value in message.items() if key != "mseq"}
        self._prepare([message])
        super().append(message)

    def extend(self, messages: Iterable[Message]) -> None:
        for message in list(messages):
            self.append(message)

    # list.__add__ widens its element type; mutating __iadd__ cannot safely do so.
    def __iadd__(self, messages: Iterable[Message]) -> Self:  # type: ignore[override,misc]
        self.extend(messages)
        return self

    def insert(self, index: SupportsIndex, message: Message) -> None:
        if message_sequence(message) is not None:
            message = {key: value for key, value in message.items() if key != "mseq"}
        self._prepare([message])
        super().insert(index, message)

    @overload
    def __setitem__(self, key: SupportsIndex, value: Message) -> None: ...

    @overload
    def __setitem__(self, key: slice, value: Iterable[Message]) -> None: ...

    def __setitem__(
        self,
        key: SupportsIndex | slice,
        value: Message | Iterable[Message],
    ) -> None:
        if isinstance(key, slice):
            assert not isinstance(value, dict)
            entries = list(value)
            self._prepare(entries)
            super().__setitem__(key, entries)
        else:
            assert isinstance(value, dict)
            self._prepare([value])
            super().__setitem__(key, value)
