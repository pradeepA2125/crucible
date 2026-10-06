"""ChatGPT plan usage: the errors that stop inference until the user acts.

These are deliberately NOT "provider unavailable". A usage limit, an ineligible
account or a revoked session can't be fixed by retrying or by a correction message,
so they must end the turn (and pause a team) instead of being re-queued. OpenAI never
switches the request to another billing path on its own, and neither do we.

Codes and recovery: https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery
"""
from __future__ import annotations


class ProviderAccessStopped(RuntimeError):
    """Inference stopped because of the account's access, not the model or the network."""

    kind = "access_stopped"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        request_id: str | None = None,
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.request_id = request_id
        self.param = param


class PlanUsageLimitReached(ProviderAccessStopped):
    """The ChatGPT plan's limit, or this app's own limit, is used up."""

    kind = "usage_limit"


class PlanNotEligible(ProviderAccessStopped):
    """Plan usage isn't available for this user, workspace, policy or region."""

    kind = "not_eligible"


class PlanSessionInvalid(ProviderAccessStopped):
    """The sign-in is no longer accepted (revoked, disconnected, or refresh failed)."""

    kind = "session_invalid"


class PlanUsageDisabled(ProviderAccessStopped):
    """Signed in, but the user didn't allow ChatGPT plan usage (the direct scope is
    missing). Enabling it is a fresh consent, never something to retry."""

    kind = "plan_disabled"


class PlanUnsupportedCapability(ProviderAccessStopped):
    """The request used a model, field or tool the plan route doesn't support."""

    kind = "unsupported_capability"


class PlanAccessMisconfigured(ProviderAccessStopped):
    """The route or grant doesn't authorize the call — a bug on our side, not the user's."""

    kind = "misconfigured"


_BY_CODE: dict[str, type[ProviderAccessStopped]] = {
    "subscription_sharing_usage_limit_exceeded": PlanUsageLimitReached,
    "subscription_sharing_user_not_eligible": PlanNotEligible,
    "subscription_sharing_invalid_user": PlanSessionInvalid,
    "subscription_sharing_unsupported_capability": PlanUnsupportedCapability,
    "subscription_sharing_route_not_supported": PlanAccessMisconfigured,
    "chatpass_v2_scope_not_authorized": PlanAccessMisconfigured,
    "chatpass_v2_invalid_authorization_context": PlanAccessMisconfigured,
}

# Temporary on OpenAI's side: keep the credentials and retry with bounded backoff.
TRANSIENT_PLAN_CODES = frozenset({
    "subscription_sharing_usage_unavailable",
    "subscription_sharing_user_unavailable",
})


def classify_plan_error(
    code: str | None,
    message: str,
    *,
    status: int | None = None,
    request_id: str | None = None,
    param: str | None = None,
) -> ProviderAccessStopped | None:
    """The stop error for a documented plan code, or None when the code isn't one."""
    cls = _BY_CODE.get(code or "")
    if cls is None:
        return None
    return cls(message or code or cls.kind, code=code, status=status,
               request_id=request_id, param=param)


def find_access_stop(exc: BaseException) -> ProviderAccessStopped | None:
    """The access stop in `exc`'s cause chain, if any — wrappers must not hide it."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ProviderAccessStopped):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


# What the user reads when a turn ends on an access stop. Usage-limit wording follows
# the UI/UX guidelines ("Usage limit reached — Review your plan or this app's limit in
# ChatGPT settings").
_DESCRIPTIONS: dict[str, str] = {
    "usage_limit": "Usage limit reached. Review your plan or Crucible's limit in "
                   "ChatGPT settings → Usage.",
    "not_eligible": "ChatGPT plan usage isn't available for this account or workspace. "
                    "You can use an API key instead in Crucible settings.",
    "session_invalid": "Your ChatGPT sign-in has ended. Sign in again in Crucible settings.",
}


def describe_access_stop(stop: ProviderAccessStopped) -> str:
    text = _DESCRIPTIONS.get(stop.kind)
    if text is not None:
        return text
    if stop.kind == "unsupported_capability":
        what = f" ({stop.param})" if stop.param else ""
        return f"ChatGPT plan usage doesn't support part of this request{what}: {stop}"
    if stop.kind == "misconfigured":
        ref = f" Request id: {stop.request_id}." if stop.request_id else ""
        return f"ChatGPT refused Crucible's request — this is a Crucible bug: {stop}.{ref}"
    return str(stop)


def access_payload(stop: ProviderAccessStopped) -> dict[str, object]:
    """The `/live` `provider_access` shape the UI renders its cards from."""
    return {"kind": stop.kind, "message": describe_access_stop(stop), "code": stop.code,
            "status": stop.status, "request_id": stop.request_id}
