"""Shared-password sessions for a LAN-exposed local workspace."""

import pytest
from fastapi import HTTPException

import infra.auth.local as local_auth
from infra.auth.local import SESSION_COOKIE, LocalAuthProvider


class _Request:
    """Minimal stand-in for a Starlette request: only cookies and headers are read."""

    def __init__(self, cookie: str | None = None, bearer: str | None = None):
        self.cookies = {SESSION_COOKIE: cookie} if cookie else {}
        self.headers = {"authorization": f"Bearer {bearer}"} if bearer else {}


def test_loopback_mode_needs_no_credentials():
    provider = LocalAuthProvider("local-user")

    assert provider.requires_login is False
    assert provider.is_authenticated(_Request()) is True


def test_password_mode_rejects_anonymous_requests():
    provider = LocalAuthProvider("local-user", "hunter2")

    assert provider.requires_login is True
    assert provider.is_authenticated(_Request()) is False


async def test_a_issued_session_authenticates_the_web_ui():
    provider = LocalAuthProvider("local-user", "hunter2")

    assert provider.is_authenticated(_Request(cookie=provider.issue_session())) is True
    assert await provider.get_current_user(_Request(cookie=provider.issue_session())) == "local-user"


def test_bearer_token_authenticates_scripts_and_mcp_clients():
    provider = LocalAuthProvider("local-user", "hunter2")

    assert provider.is_authenticated(_Request(bearer="hunter2")) is True
    assert provider.is_authenticated(_Request(bearer="wrong")) is False


def test_tampered_and_malformed_sessions_are_rejected():
    provider = LocalAuthProvider("local-user", "hunter2")
    expires, _, signature = provider.issue_session().partition(".")
    forged = f"{expires}.{'0' * len(signature)}"

    assert provider.verify_session(forged) is False
    assert provider.verify_session("garbage") is False
    assert provider.verify_session("") is False
    assert provider.verify_session(None) is False


def test_expired_sessions_are_rejected(monkeypatch):
    provider = LocalAuthProvider("local-user", "hunter2")
    session = provider.issue_session()
    later = local_auth.time.time() + local_auth.SESSION_TTL_SECONDS + 5

    monkeypatch.setattr(local_auth.time, "time", lambda: later)

    assert provider.verify_session(session) is False


def test_changing_the_password_invalidates_existing_sessions():
    session = LocalAuthProvider("local-user", "hunter2").issue_session()

    assert LocalAuthProvider("local-user", "hunter3").verify_session(session) is False


async def test_anonymous_requests_raise_401():
    provider = LocalAuthProvider("local-user", "hunter2")

    with pytest.raises(HTTPException) as raised:
        await provider.get_current_user(_Request())

    assert raised.value.status_code == 401
    assert raised.value.headers["WWW-Authenticate"] == "Bearer"
