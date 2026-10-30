"""The two ways an agent asks past the sandbox, shared by run_command and start_session."""
from __future__ import annotations

from collections.abc import Mapping

from agentd.domain.models import Escalation
from agentd.sandbox.hosts import normalize_host

ESCALATION_PROPERTIES: dict[str, object] = {
    "outside_sandbox": {
        "type": "boolean",
        "description": ("Run this command outside the sandbox. Only after the sandbox "
                        "blocked it; needs approval and a reason."),
    },
    "allow_hosts": {
        "type": "array", "items": {"type": "string"},
        "description": ("Hosts this command may reach from inside the sandbox (bare host "
                        "names). Only after the sandbox blocked them; needs approval."),
    },
    "reason": {"type": "string",
               "description": "Why the command needs outside_sandbox or allow_hosts."},
}


def escalation_from_args(args: Mapping[str, object]) -> Escalation | None:
    """The escalation an agent asked for, or None. outside_sandbox wins over hosts. Raises
    ValueError for a host that isn't a bare name, before anyone is asked about it."""
    reason = str(args.get("reason") or "")
    if args.get("outside_sandbox") is True:
        return Escalation(kind="outside_sandbox", reason=reason)
    hosts = args.get("allow_hosts")
    if isinstance(hosts, list) and hosts:
        return Escalation(kind="allow_hosts", hosts=[normalize_host(str(h)) for h in hosts],
                          reason=reason)
    return None
