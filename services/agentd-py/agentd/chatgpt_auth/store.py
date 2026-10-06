"""ChatGPT account registrations on this machine.

Layout under the auth root (default `~/.crucible/auth/chatgpt`, shared by every
workspace's backend — one machine install is one agent host):

    host.json                    {"ext_agent_host_id": "urn:uuid:…"}  (written once)
    registrations/<id>.json      one CredentialRecord per registration
    registrations/<id>.lock      sidecar lock serializing refresh / sign-in / sign-out

A registration is one issued `client_id` bound to one ChatGPT account + workspace.
Two registrations can share an email, so the key is our own opaque id, never the email.
Records are kept after sign-out (tokens cleared) so a later sign-in reuses the issued
client id, as the docs require.
"""
from __future__ import annotations

import os
import re
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import BaseModel

from agentd.chatgpt_auth.files import (
    ensure_private_dir,
    exclusive_lock,
    read_private_json,
    write_private_json,
)

PLAN_SCOPE = "chatgpt.tokens.use.direct"
_REGISTRATION_ID_RE = re.compile(r"reg_[0-9a-f]{12}")


def default_auth_root() -> Path:
    override = os.environ.get("CRUCIBLE_CHATGPT_AUTH_DIR")
    return Path(override) if override else Path.home() / ".crucible" / "auth" / "chatgpt"


class CredentialRecord(BaseModel):
    registration_id: str
    label: str
    email: str | None = None
    name: str | None = None
    issuer: str
    subject: str
    client_id: str
    ext_agent_host_id: str
    id_token: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: float | None = None
    earliest_refresh_at: float | None = None
    scopes: list[str] = []
    saved_at: float
    needs_sign_in: bool = False

    @property
    def plan_enabled(self) -> bool:
        return PLAN_SCOPE in self.scopes

    @property
    def signed_in(self) -> bool:
        return self.access_token is not None and not self.needs_sign_in


class RegistrationSummary(BaseModel):
    """What leaves the backend about a registration: never a token."""

    registration_id: str
    label: str
    email: str | None
    name: str | None
    plan_enabled: bool
    signed_in: bool


def summarize(record: CredentialRecord) -> RegistrationSummary:
    return RegistrationSummary(
        registration_id=record.registration_id, label=record.label, email=record.email,
        name=record.name, plan_enabled=record.plan_enabled, signed_in=record.signed_in)


class UnknownRegistration(KeyError):
    pass


class CredentialStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or default_auth_root()

    @property
    def root(self) -> Path:
        return self._root

    def host_id(self) -> str:
        """This host's stable `ext_agent_host_id`, created on first use and kept forever."""
        path = self._root / "host.json"
        existing = read_private_json(path)
        found = existing.get("ext_agent_host_id") if existing else None
        if isinstance(found, str):
            return found
        host_id = f"urn:uuid:{uuid.uuid4()}"
        write_private_json(path, {"ext_agent_host_id": host_id})
        return host_id

    def new_registration_id(self) -> str:
        return f"reg_{secrets.token_hex(6)}"

    def list(self) -> list[CredentialRecord]:
        directory = self._root / "registrations"
        if not directory.is_dir():
            return []
        records = []
        for path in sorted(directory.glob("reg_*.json")):
            record = self.get(path.stem)
            if record is not None:
                records.append(record)
        return sorted(records, key=lambda r: r.saved_at)

    def get(self, registration_id: str) -> CredentialRecord | None:
        data = read_private_json(self._path(registration_id))
        return CredentialRecord.model_validate(data) if data is not None else None

    def require(self, registration_id: str) -> CredentialRecord:
        record = self.get(registration_id)
        if record is None:
            raise UnknownRegistration(registration_id)
        return record

    def save(self, record: CredentialRecord) -> None:
        ensure_private_dir(self._root / "registrations")
        write_private_json(self._path(record.registration_id), record.model_dump())

    def unique_label(self, email: str | None, exclude: str | None = None) -> str:
        """Email when free, else `email (2)`, `email (3)`… — labels must stay distinct."""
        base = email or "ChatGPT account"
        taken = {r.label for r in self.list() if r.registration_id != exclude}
        if base not in taken:
            return base
        n = 2
        while f"{base} ({n})" in taken:
            n += 1
        return f"{base} ({n})"

    @asynccontextmanager
    async def locked(self, registration_id: str) -> AsyncIterator[None]:
        """Exclusive across every backend on this machine: refresh tokens rotate."""
        self._check_id(registration_id)
        async with exclusive_lock(self._root / "registrations" / f"{registration_id}.lock"):
            yield

    def _path(self, registration_id: str) -> Path:
        self._check_id(registration_id)
        return self._root / "registrations" / f"{registration_id}.json"

    @staticmethod
    def _check_id(registration_id: str) -> None:
        # Ids reach file paths and come from requests: refuse anything but our format.
        if not _REGISTRATION_ID_RE.fullmatch(registration_id):
            raise UnknownRegistration(registration_id)

