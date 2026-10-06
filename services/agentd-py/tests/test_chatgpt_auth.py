"""Sign in with ChatGPT: registration, reauthorization, token lifecycle, sign-out."""
from __future__ import annotations

import asyncio
import logging
import os
import stat
import urllib.parse
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa

from agentd.chatgpt_auth.oauth import AttemptManager, SignInInProgress
from agentd.chatgpt_auth.oidc import OidcClient
from agentd.chatgpt_auth.session import ChatGPTPlanBearer, sign_out
from agentd.chatgpt_auth.store import CredentialStore, UnknownRegistration
from agentd.providers.openai_compatible_transport import TransientTransportError
from agentd.providers.plan_access import PlanSessionInvalid, PlanUsageDisabled
from tests._chatgpt_fake import ISSUER, FakeAuthServer


@pytest.fixture
def fake() -> FakeAuthServer:
    return FakeAuthServer()


@pytest.fixture
def store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(tmp_path / "auth")


@pytest_asyncio.fixture
async def oidc(fake: FakeAuthServer) -> AsyncIterator[OidcClient]:
    async with fake.client() as http:
        yield OidcClient(http, issuer=ISSUER)


@pytest_asyncio.fixture
async def manager(store: CredentialStore, oidc: OidcClient) -> AsyncIterator[AttemptManager]:
    m = AttemptManager(store, oidc, preferred_port=0)
    yield m
    await m.aclose()


def _query(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


async def _callback(redirect_uri: str, params: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient() as http:
        return await http.get(redirect_uri, params=params)


async def _sign_in(manager: AttemptManager, fake: FakeAuthServer, **authorize: object) -> str:
    status, url = await manager.start()
    await _callback(_query(url)["redirect_uri"], fake.authorize(url, **authorize))  # type: ignore[arg-type]
    done = await manager.wait(status.attempt_id)
    assert done.state == "succeeded", done
    assert done.registration_id is not None
    return done.registration_id


# ------------------------------------------------------------ first registration


@pytest.mark.asyncio
async def test_first_sign_in_registers_a_client_and_saves_a_private_record(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore,
) -> None:
    status, url = await manager.start()
    params = _query(url)
    assert url.startswith(f"{ISSUER}/api/accounts/authorize?")
    assert params["client_id"] == "dynamic_agent_client"
    assert params["agent_name_hint"] == "Crucible"
    assert params["ext_agent_host_id"].startswith("urn:uuid:")
    assert params["response_type"] == "code"
    assert params["scope"] == ("openid profile email offline_access resource.invoke "
                               "chatgpt.tokens.use.direct")
    assert params["resource"] == "https://api.openai.com/v1"
    assert params["code_challenge_method"] == "S256"
    redirect = urllib.parse.urlsplit(params["redirect_uri"])
    assert (redirect.scheme, redirect.hostname, redirect.path) == (
        "http", "127.0.0.1", "/auth/callback")
    assert "id_token_hint" not in params and "prompt" not in params

    page = await _callback(params["redirect_uri"], fake.authorize(url))
    done = await manager.wait(status.attempt_id)

    assert page.status_code == 200 and "signed in" in page.text
    assert done.state == "succeeded" and done.plan_enabled and done.first_plan_sign_in
    exchange = fake.token_requests[0]
    assert exchange["client_id"] == "oaiapp_0001"  # the issued id, never the entrypoint
    assert exchange["redirect_uri"] == params["redirect_uri"]
    assert done.registration_id is not None
    record = store.require(done.registration_id)
    assert record.client_id == "oaiapp_0001"
    assert record.subject == "user-1" and record.email == "a@example.com"
    assert record.ext_agent_host_id == params["ext_agent_host_id"]
    assert record.refresh_token and record.access_token and record.id_token
    path = store.root / "registrations" / f"{record.registration_id}.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.root).st_mode) == 0o700


@pytest.mark.asyncio
async def test_missing_direct_scope_signs_in_with_plan_usage_disabled(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore,
) -> None:
    reg = await _sign_in(manager, fake, scope="openid profile email offline_access")
    assert store.require(reg).plan_enabled is False


@pytest.mark.asyncio
async def test_a_callback_without_an_issued_client_is_an_incomplete_registration(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore,
) -> None:
    status, url = await manager.start()
    await _callback(_query(url)["redirect_uri"], fake.authorize(url, issue_client=False))
    done = await manager.wait(status.attempt_id)
    assert (done.state, done.reason) == ("failed", "registration_incomplete")
    assert store.list() == [] and fake.token_requests == []


@pytest.mark.asyncio
async def test_declined_consent_stops_without_exchanging(
    manager: AttemptManager, fake: FakeAuthServer,
) -> None:
    status, url = await manager.start()
    await _callback(_query(url)["redirect_uri"],
                    {"state": _query(url)["state"], "error": "access_denied"})
    done = await manager.wait(status.attempt_id)
    assert (done.state, done.reason) == ("failed", "access_denied")
    assert fake.token_requests == []


@pytest.mark.asyncio
async def test_a_wrong_state_is_ignored_and_the_real_callback_still_works(
    manager: AttemptManager, fake: FakeAuthServer,
) -> None:
    status, url = await manager.start()
    redirect = _query(url)["redirect_uri"]
    stray = await _callback(redirect, {"state": "forged", "code": "x"})
    assert stray.status_code == 400
    assert manager.status(status.attempt_id).state == "pending"  # type: ignore[union-attr]
    await _callback(redirect, fake.authorize(url))
    assert (await manager.wait(status.attempt_id)).state == "succeeded"


@pytest.mark.asyncio
async def test_id_token_with_the_wrong_nonce_or_key_is_rejected(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore,
) -> None:
    status, url = await manager.start()
    await _callback(_query(url)["redirect_uri"], fake.authorize(url, nonce="other"))
    done = await manager.wait(status.attempt_id)
    assert (done.state, done.reason) == ("failed", "invalid_id_token")

    fake.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fake.kid = "kid-rotated"
    # A rotated key is fetched again by kid, so a correctly signed token still passes.
    assert (await manager.wait((await _sign_in_status(manager, fake)).attempt_id)).state \
        == "succeeded"
    assert len(store.list()) == 1


async def _sign_in_status(manager: AttemptManager, fake: FakeAuthServer):  # noqa: ANN202
    status, url = await manager.start()
    await _callback(_query(url)["redirect_uri"], fake.authorize(url))
    return status


@pytest.mark.asyncio
async def test_one_sign_in_at_a_time(manager: AttemptManager) -> None:
    await manager.start()
    with pytest.raises(SignInInProgress):
        await manager.start()


# ------------------------------------------------------------ returning accounts


@pytest.mark.asyncio
async def test_returning_sign_in_reuses_the_issued_client_and_hints(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore,
) -> None:
    reg = await _sign_in(manager, fake)
    first = store.require(reg)

    status, url = await manager.start(registration_id=reg, reconsent=True)
    params = _query(url)
    assert params["client_id"] == first.client_id
    assert params["id_token_hint"] == first.id_token
    assert params["login_hint"] == "a@example.com"
    assert params["ext_agent_host_id"] == first.ext_agent_host_id
    assert params["prompt"] == "consent"
    assert "agent_name_hint" not in params

    await _callback(params["redirect_uri"], fake.authorize(url))
    done = await manager.wait(status.attempt_id)
    assert done.state == "succeeded" and done.registration_id == reg
    assert done.first_plan_sign_in is False
    again = store.require(reg)
    assert again.access_token != first.access_token and again.client_id == first.client_id
    assert len(store.list()) == 1


@pytest.mark.asyncio
async def test_returning_sign_in_as_another_account_replaces_nothing(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore,
) -> None:
    reg = await _sign_in(manager, fake)
    before = store.require(reg)
    status, url = await manager.start(registration_id=reg)
    await _callback(_query(url)["redirect_uri"], fake.authorize(url, subject="user-2"))
    done = await manager.wait(status.attempt_id)
    assert (done.state, done.reason) == ("failed", "account_mismatch")
    assert store.require(reg) == before


@pytest.mark.asyncio
async def test_returning_callback_with_a_different_client_is_rejected(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore,
) -> None:
    reg = await _sign_in(manager, fake)
    before = store.require(reg)
    status, url = await manager.start(registration_id=reg)
    callback = fake.authorize(url)
    callback["client_id"] = "oaiapp_9999"
    await _callback(_query(url)["redirect_uri"], callback)
    done = await manager.wait(status.attempt_id)
    assert (done.state, done.reason) == ("failed", "client_mismatch")
    assert store.require(reg) == before


@pytest.mark.asyncio
async def test_two_registrations_with_one_email_stay_separate(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore,
) -> None:
    a = await _sign_in(manager, fake, subject="user-1")
    b = await _sign_in(manager, fake, subject="user-1")  # a second workspace
    ra, rb = store.require(a), store.require(b)
    assert a != b and ra.client_id != rb.client_id
    assert (ra.label, rb.label) == ("a@example.com", "a@example.com (2)")


# ------------------------------------------------------------ store


def test_host_id_is_created_once(store: CredentialStore) -> None:
    host = store.host_id()
    assert host.startswith("urn:uuid:") and store.host_id() == host


def test_registration_ids_are_never_paths(store: CredentialStore) -> None:
    with pytest.raises(UnknownRegistration):
        store.get("../../etc/passwd")


# ------------------------------------------------------------ refresh


async def _expiring(store: CredentialStore, reg: str, seconds: float) -> None:
    record = store.require(reg)
    store.save(record.model_copy(update={"expires_at": record.saved_at + seconds,
                                         "saved_at": record.saved_at}))


@pytest.mark.asyncio
async def test_a_fresh_token_is_used_without_refreshing(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
) -> None:
    reg = await _sign_in(manager, fake)
    bearer = ChatGPTPlanBearer(reg, store, oidc)
    assert await bearer.bearer() == store.require(reg).access_token
    assert len(fake.token_requests) == 1  # just the code exchange


@pytest.mark.asyncio
async def test_near_expiry_refreshes_and_saves_the_rotated_tokens(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
) -> None:
    reg = await _sign_in(manager, fake)
    before = store.require(reg)
    await _expiring(store, reg, 60)
    token = await ChatGPTPlanBearer(reg, store, oidc).bearer()
    after = store.require(reg)
    refresh = fake.token_requests[-1]
    assert refresh["grant_type"] == "refresh_token"
    assert refresh["client_id"] == before.client_id
    assert refresh["resource"] == "https://api.openai.com/v1" and "scope" not in refresh
    assert token == after.access_token != before.access_token
    assert after.refresh_token != before.refresh_token
    assert after.expires_at is not None and after.expires_at > after.saved_at + 3000


@pytest.mark.asyncio
async def test_backends_racing_a_refresh_rotate_exactly_once(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
) -> None:
    reg = await _sign_in(manager, fake)
    await _expiring(store, reg, 60)
    # Separate bearers and stores = separate backends: only the file lock is shared.
    bearers = [ChatGPTPlanBearer(reg, CredentialStore(store.root), oidc) for _ in range(4)]
    tokens = await asyncio.gather(*(b.bearer() for b in bearers))
    refreshes = [r for r in fake.token_requests if r["grant_type"] == "refresh_token"]
    assert len(refreshes) == 1
    assert set(tokens) == {store.require(reg).access_token}


@pytest.mark.asyncio
async def test_an_unusable_refresh_token_needs_a_new_sign_in(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
) -> None:
    reg = await _sign_in(manager, fake)
    await _expiring(store, reg, -10)
    fake.refresh_error = (400, "refresh_token_reused")
    with pytest.raises(PlanSessionInvalid):
        await ChatGPTPlanBearer(reg, store, oidc).bearer()
    record = store.require(reg)
    assert record.needs_sign_in and record.access_token is None and record.refresh_token is None
    assert record.client_id.startswith("oaiapp_")  # kept for the next sign-in


@pytest.mark.asyncio
async def test_a_server_error_keeps_credentials(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
) -> None:
    reg = await _sign_in(manager, fake)
    fake.refresh_error = (503, "temporarily_unavailable")
    await _expiring(store, reg, 60)
    still_valid = store.require(reg).access_token
    assert await ChatGPTPlanBearer(reg, store, oidc).bearer() == still_valid
    await _expiring(store, reg, -10)
    with pytest.raises(TransientTransportError):
        await ChatGPTPlanBearer(reg, store, oidc).bearer()
    assert store.require(reg).refresh_token is not None


@pytest.mark.asyncio
async def test_a_401_forces_one_refresh(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
) -> None:
    reg = await _sign_in(manager, fake)
    bearer = ChatGPTPlanBearer(reg, store, oidc)
    old = await bearer.bearer()
    assert await bearer.on_unauthorized() is True
    assert await bearer.bearer() != old


@pytest.mark.asyncio
async def test_plan_usage_disabled_is_not_a_session_problem(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
) -> None:
    reg = await _sign_in(manager, fake, scope="openid profile email offline_access")
    with pytest.raises(PlanUsageDisabled):
        await ChatGPTPlanBearer(reg, store, oidc).bearer()


# ------------------------------------------------------------ sign-out


@pytest.mark.asyncio
async def test_sign_out_revokes_then_clears_tokens_but_keeps_the_registration(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
) -> None:
    reg = await _sign_in(manager, fake)
    before = store.require(reg)
    result = await sign_out(reg, store, oidc, retry_delay_sec=0)
    assert result.remote_revoked is True
    assert fake.revocations == [{"token": before.refresh_token,
                                 "token_type_hint": "refresh_token",
                                 "client_id": before.client_id}]
    after = store.require(reg)
    assert (after.access_token, after.refresh_token, after.id_token) == (None, None, None)
    assert (after.client_id, after.subject, after.label) == (
        before.client_id, before.subject, before.label)
    with pytest.raises(PlanSessionInvalid):
        await ChatGPTPlanBearer(reg, store, oidc).bearer()


@pytest.mark.asyncio
async def test_unconfirmed_revocation_still_signs_out_locally(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
) -> None:
    reg = await _sign_in(manager, fake)
    fake.revoke_status = 503
    result = await sign_out(reg, store, oidc, retry_delay_sec=0)
    assert result.remote_revoked is False and len(fake.revocations) == 3
    assert store.require(reg).refresh_token is None


# ------------------------------------------------------------ secrecy


@pytest.mark.asyncio
async def test_no_token_ever_reaches_the_logs(
    manager: AttemptManager, fake: FakeAuthServer, store: CredentialStore, oidc: OidcClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    reg = await _sign_in(manager, fake)
    first = store.require(reg)
    await _expiring(store, reg, 60)
    await ChatGPTPlanBearer(reg, store, oidc).bearer()
    second = store.require(reg)
    fake.revoke_status = 503
    await sign_out(reg, store, oidc, retry_delay_sec=0)
    secrets_seen = {first.access_token, first.refresh_token, first.id_token,
                    second.access_token, second.refresh_token}
    for secret in secrets_seen:
        assert secret is not None and secret not in caplog.text
