"""Local-mode shared-password session endpoints.

The web UI posts the password once, receives an HMAC-signed session cookie, and then
relies on the browser to attach it to every same-origin request (including `<img>` and
SSE, which cannot send an Authorization header). Scripts and MCP clients may instead
send `Authorization: Bearer <password>`.
"""

from config import settings
from fastapi import APIRouter, HTTPException, Request, Response
from infra.auth.local import SESSION_COOKIE, SESSION_TTL_SECONDS, LocalAuthProvider
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class AuthStatus(BaseModel):
    required: bool
    authenticated: bool
    role: str = "admin"


class AuthConfig(BaseModel):
    mode: str
    required: bool
    public_url: str
    mcp_url: str


def _provider(request: Request) -> LocalAuthProvider | None:
    provider = getattr(request.app.state, "auth_provider", None)
    return provider if isinstance(provider, LocalAuthProvider) else None


@router.get("/status", response_model=AuthStatus)
async def auth_status(request: Request) -> AuthStatus:
    provider = _provider(request)
    if provider is None:
        return AuthStatus(required=False, authenticated=True)
    role = provider.role_for_request(request)
    return AuthStatus(
        required=provider.requires_login,
        authenticated=role is not None,
        role=role or "",
    )


@router.get("/config", response_model=AuthConfig)
async def auth_config() -> AuthConfig:
    """Non-secret deployment hints the UI needs to build MCP client snippets."""
    return AuthConfig(
        mode=settings.MODE,
        required=settings.local_auth_required,
        public_url=settings.local_public_url,
        mcp_url=(
            f"{settings.local_public_url}/mcp"
            if settings.LOCAL_MCP_TRANSPORT == "streamable-http"
            else ""
        ),
    )


@router.post("/login", response_model=AuthStatus)
async def login(payload: LoginRequest, request: Request, response: Response) -> AuthStatus:
    provider = _provider(request)
    if provider is None or not provider.requires_login:
        return AuthStatus(required=False, authenticated=True)
    role = provider.resolve_role(payload.password)
    if role is None:
        raise HTTPException(status_code=401, detail="Invalid password")

    response.set_cookie(
        SESSION_COOKIE,
        provider.issue_session(role),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return AuthStatus(required=True, authenticated=True, role=role)


@router.post("/logout", status_code=204)
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
