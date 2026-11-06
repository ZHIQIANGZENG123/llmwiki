"""Local auth provider: single fixed user, gated by one or two shared passwords.

Loopback bindings stay unauthenticated (unchanged behaviour). Once the API is reachable
from the LAN, a password is required and every request must present either the signed
session cookie the login route sets, or `Authorization: Bearer <password>`.

Two passwords may be configured. The admin password unlocks everything; the read-only
password only unlocks browsing, search and downloads. A reader is not a separate account —
it is the same single-user workspace seen through a narrower gate, so the knowledge base
cannot be reorganised by someone who only needs to read it.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request

SESSION_COOKIE = "llmwiki_session"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60

ROLE_ADMIN = "admin"
ROLE_READER = "reader"


class LocalAuthProvider:
    def __init__(self, user_id: str, password: str = "", read_password: str = ""):
        self._user_id = user_id
        self._password = password or ""
        self._read_password = read_password or ""

    @property
    def requires_login(self) -> bool:
        return bool(self._password or self._read_password)

    @property
    def has_reader(self) -> bool:
        return bool(self._read_password)

    def _signing_key(self) -> bytes:
        # Sessions are signed with the admin password so rotating it invalidates every
        # existing cookie. Without any password the gate is open anyway.
        return (self._password or "llmwiki-open").encode("utf-8")

    def _sign(self, payload: str) -> str:
        return hmac.new(
            self._signing_key(),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def resolve_role(self, candidate: str | None) -> str | None:
        """Map a password or bearer token to a role; None when it matches nothing."""
        value = (candidate or "").strip()
        if not self.requires_login:
            return ROLE_ADMIN
        if not value:
            return None
        if self._password and hmac.compare_digest(value.encode(), self._password.encode()):
            return ROLE_ADMIN
        if self._read_password and hmac.compare_digest(
            value.encode(), self._read_password.encode()
        ):
            return ROLE_READER
        return None

    def check_password(self, candidate: str) -> bool:
        return self.resolve_role(candidate) is not None

    def issue_session(self, role: str = ROLE_ADMIN) -> str:
        expires = str(int(time.time()) + SESSION_TTL_SECONDS)
        payload = f"{expires}.{role}"
        return f"{payload}.{self._sign(payload)}"

    def verify_session(self, value: str | None) -> str | None:
        """Return the role carried by a session cookie, or None when it is invalid."""
        if not self.requires_login:
            return ROLE_ADMIN
        if not value:
            return None
        parts = value.split(".")
        if len(parts) != 3:
            return None
        expires, role, signature = parts
        if not expires.isdigit() or role not in (ROLE_ADMIN, ROLE_READER):
            return None
        if int(expires) < int(time.time()):
            return None
        if not hmac.compare_digest(signature, self._sign(f"{expires}.{role}")):
            return None
        return role

    def _bearer_role(self, request: Request) -> str | None:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer":
            return None
        return self.resolve_role(token)

    def role_for_request(self, request: Request) -> str | None:
        if not self.requires_login:
            return ROLE_ADMIN
        cookie_role = self.verify_session(request.cookies.get(SESSION_COOKIE))
        if cookie_role:
            return cookie_role
        return self._bearer_role(request)

    def is_authenticated(self, request: Request) -> bool:
        return self.role_for_request(request) is not None

    def is_admin(self, request: Request) -> bool:
        return self.role_for_request(request) == ROLE_ADMIN

    async def get_current_user(self, request: Request) -> str:
        if self.is_authenticated(request):
            return self._user_id
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
