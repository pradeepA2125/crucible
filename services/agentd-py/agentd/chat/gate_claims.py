"""Exact decision identity; a receipt proves acceptance, never execution or idleness."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GateClaimError(ValueError):
    """A definite refusal before acceptance; routes return a conflict."""


class GateClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")
    process_id: str = Field(min_length=1, max_length=128)
    workspace_path: str = Field(min_length=1, max_length=8192)
    gate_id: str = Field(min_length=1, max_length=1024)
    kind: Literal["mode", "clarify", "feedback"]
    value: str = Field(min_length=1, max_length=65536)

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported exact gate protocol")
        return value

    def identity(self, thread_id: str) -> dict[str, object]:
        # Hash the exact submitted value, not its trimmed execution form. Receipts
        # never keep a second copy of feedback or a question's answer.
        digest = hashlib.sha256(
            json.dumps(
                [self.kind, self.value],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            **self.model_dump(exclude={"value"}),
            "thread_id": thread_id,
            "payload_sha256": digest,
        }


def matches(receipt: dict[str, object], identity: dict[str, object]) -> bool:
    return all(receipt.get(key) == value for key, value in identity.items())


class GateReceiptLookup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")
    process_id: str = Field(min_length=1, max_length=128)
    workspace_path: str = Field(min_length=1, max_length=8192)
    gate_id: str = Field(min_length=1, max_length=1024)
    kind: Literal["mode", "clarify", "feedback"]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")

    def identity(self, thread_id: str) -> dict[str, object]:
        return {"version": 1, **self.model_dump(exclude={"nonce"}), "thread_id": thread_id}
