"""Wire types and conflicts for backend-wide approval ownership."""

from typing import Literal

from pydantic import BaseModel, Field, StrictBool

from agentd.sandbox.runtime import SandboxStatus


class ApprovalState(BaseModel):
    supported: bool = True
    auto_accept: bool
    shell_policy: Literal["ask", "allow_all", "auto"]
    # The policies this backend accepts; the extension offers Auto only when listed.
    shell_policies: list[str] = Field(default_factory=lambda: ["ask", "allow_all", "auto"])
    version: int
    owned: bool
    process_id: str
    # The command sandbox; None on a backend without one (older clients ignore it).
    sandbox: SandboxStatus | None = None


class ApprovalModeResult(ApprovalState):
    auto_resolved_edits: int = 0
    background: int = 0


class ApprovalModeRequest(BaseModel):
    auto_accept: StrictBool
    shell_policy: Literal["ask", "allow_all", "auto"]
    resolve_waiting: StrictBool
    expected_process_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0, strict=True)
    # Omitted = leave the sandbox switch as it is (older extensions never send it).
    sandbox: StrictBool | None = None


class ApprovalConflict(Exception):
    def __init__(self, state: ApprovalState):
        self.state = state
        super().__init__("approval policy changed or backend restarted")


class ApprovalManaged(Exception):
    def __init__(self):
        super().__init__("approval mode is managed by the approval-mode API; update the extension")
