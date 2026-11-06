import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Keep in sync with infra.local_http.LOOPBACK_HOSTS.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback_host(value: str) -> bool:
    host = value.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin_of(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
        return None
    scheme = parsed.scheme.lower()
    return scheme, parsed.hostname.lower(), port or (443 if scheme == "https" else 80)


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    MODE: Literal["local", "hosted"] = "local"
    WORKSPACE_PATH: str = "."

    DATABASE_URL: str = ""
    # Non-pooler URL for LISTEN/NOTIFY: Supavisor recycles pooled sessions, silently killing LISTEN.
    DIRECT_DATABASE_URL: str = ""
    SUPABASE_URL: str = ""
    LOGFIRE_TOKEN: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET: str = ""
    MISTRAL_API_KEY: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""
    CLOUDFLARE_AUTH_TOKEN: str = ""
    CLOUDFLARE_AI_GATEWAY_ID: str = ""
    QUIZ_GRADE_DAILY_LIMIT: int = Field(default=100, ge=1, le=10_000)
    # Only "mistral" is special-cased; anything else goes to the converter, which picks its own PDF_ENGINE.
    PDF_BACKEND: str = "opendataloader"
    STAGE: str = "dev"
    APP_URL: str = "http://localhost:3000"
    API_URL: str = "http://localhost:8000"
    # Comma-separated; an unpacked dev build needs its chrome://extensions ID here.
    LOCAL_EXTENSION_ORIGINS: str = (
        "chrome-extension://dibilaenlekndomfbampadehjeahemha"
    )

    # === Local mode: LAN exposure ===
    # Leave off for the original loopback-only behaviour. The CLI sets this together with
    # LOCAL_AUTH_TOKEN and LOCAL_PUBLIC_URL; it also switches on when the API itself is
    # bound to a non-loopback address.
    LOCAL_LAN_ENABLED: bool = False
    LOCAL_BIND_HOST: str = "127.0.0.1"
    # Canonical URL browsers use to reach the web app, e.g. http://192.168.1.10:3000.
    LOCAL_PUBLIC_URL: str = ""
    # Shared password for the web UI login and for `Authorization: Bearer` API/MCP clients.
    LOCAL_AUTH_TOKEN: str = ""
    # Optional second password: read-only. Browse, search and download, but no writes.
    LOCAL_READ_TOKEN: str = ""
    # Extra Host header values the boundary middleware accepts (comma-separated, may include ports).
    LOCAL_ALLOWED_HOSTS: str = ""
    # Extra browser origins allowed to make write requests (comma-separated full origins).
    LOCAL_ALLOWED_ORIGINS: str = ""
    # MCP transport for local mode: stdio (default) or streamable-http.
    LOCAL_MCP_TRANSPORT: Literal["stdio", "streamable-http"] = "stdio"
    LOCAL_MCP_HOST: str = "127.0.0.1"
    LOCAL_MCP_PORT: int = Field(default=8100, ge=1, le=65535)

    QUOTA_MAX_PAGES_PER_DOC: int = 300
    QUOTA_MAX_STORAGE_BYTES: int = 1_073_741_824  # 1 GiB

    CONVERTER_URL: str = ""
    CONVERTER_SECRET: str = ""
    # Must not exceed the converter's MAX_CONCURRENT_EXTRACTIONS, or excess requests exhaust 503 retries.
    CONVERTER_MAX_CONCURRENCY: int = Field(default=2, ge=1, le=64)

    GLOBAL_OCR_ENABLED: bool = True
    GLOBAL_MAX_PAGES: int = 1_000_000
    GLOBAL_MAX_USERS: int = 10_000

    SENTRY_DSN: str = ""

    @model_validator(mode="after")
    def require_password_for_lan_binding(self) -> "Settings":
        """Fail at settings load rather than silently publishing an unauthenticated vault."""
        if self.MODE != "local" or not self.lan_enabled:
            return self
        if not self.LOCAL_AUTH_TOKEN.strip():
            raise ValueError(
                "LAN exposure is on (LOCAL_LAN_ENABLED or a non-loopback LOCAL_BIND_HOST), "
                "so LOCAL_AUTH_TOKEN is required — local mode has no per-user auth of its own"
            )
        if not self.LOCAL_PUBLIC_URL.strip():
            raise ValueError(
                "LAN exposure is on, so LOCAL_PUBLIC_URL is required "
                "(the browser URL used to reach the web app, e.g. http://192.168.1.10:3000); "
                "it also seeds the Host and Origin allowlists"
            )
        return self

    @model_validator(mode="after")
    def require_isolated_parser_for_hosted_uploads(self) -> "Settings":
        """Fail at settings load, before any network client starts, if hosted uploads would parse in-process."""
        hosted_uploads_enabled = bool(self.S3_BUCKET)
        if (
            self.MODE == "hosted"
            and hosted_uploads_enabled
            and not self.CONVERTER_URL.strip()
        ):
            raise ValueError(
                "CONVERTER_URL is required when hosted uploads are enabled; "
                "the hosted API must not parse uploaded PDF or Office files in-process"
            )
        if (
            self.MODE == "hosted"
            and hosted_uploads_enabled
            and not self.CONVERTER_SECRET.strip()
        ):
            raise ValueError(
                "CONVERTER_SECRET is required when hosted uploads are enabled"
            )
        return self

    @property
    def listen_database_url(self) -> str:
        """Connection for the LISTEN loop — direct if configured, else the pooler."""
        return self.DIRECT_DATABASE_URL or self.DATABASE_URL

    @property
    def local_extension_origins(self) -> tuple[str, ...]:
        return tuple(
            origin.strip()
            for origin in self.LOCAL_EXTENSION_ORIGINS.split(",")
            if origin.strip()
        )

    @property
    def lan_enabled(self) -> bool:
        """True once the local workspace is reachable from anything but loopback."""
        return self.LOCAL_LAN_ENABLED or not _is_loopback_host(self.LOCAL_BIND_HOST)

    @property
    def local_public_url(self) -> str:
        return (self.LOCAL_PUBLIC_URL or self.APP_URL).rstrip("/")

    @property
    def local_auth_required(self) -> bool:
        return bool(self.LOCAL_AUTH_TOKEN.strip())

    @property
    def local_allowed_hosts(self) -> tuple[str, ...]:
        """Hosts beyond loopback that the boundary middleware accepts."""
        hosts = list(_split_csv(self.LOCAL_ALLOWED_HOSTS))
        origin = _origin_of(self.local_public_url)
        if origin is not None:
            for candidate in (origin[1], f"{origin[1]}:{origin[2]}"):
                if candidate not in hosts:
                    hosts.append(candidate)
        return tuple(hosts)

    @property
    def local_extra_origins(self) -> tuple[str, ...]:
        """Browser origins beyond the public URL that may issue writes."""
        origins = list(_split_csv(self.LOCAL_ALLOWED_ORIGINS))
        for candidate in (self.local_public_url, self.APP_URL):
            if candidate and candidate not in origins:
                origins.append(candidate)
        return tuple(origins)

    @property
    def cors_origins(self) -> list[str]:
        """Exact origins allowed by CORS; local mode also trusts its configured LAN URL."""
        if self.MODE != "local":
            return [self.APP_URL]
        return list(self.local_extra_origins)

    @property
    def session_cookie_secure(self) -> bool:
        """Mark the session cookie Secure when the public URL is HTTPS."""
        return self.local_public_url.startswith("https://")


settings = Settings()
