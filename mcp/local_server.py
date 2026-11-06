"""Local stdio MCP server for one workspace: the filesystem is truth, SQLite is the index."""

import argparse
import asyncio
import logging
import os
import sys
import uuid
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("llmwiki.local")

_LOCAL_USER_ID = os.environ.get("LLMWIKI_USER_ID", str(uuid.uuid5(uuid.NAMESPACE_DNS, "local")))
os.environ["SUPAVAULT_USER_ID"] = _LOCAL_USER_ID


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM Wiki local MCP server")
    parser.add_argument("workspace", nargs="?", default=".", help="Path to workspace folder")
    parser.add_argument("--workspace", dest="workspace_flag", default=None, help="Path to workspace folder")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8100, help="Port for streamable-http transport")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address for streamable-http transport")
    return parser.parse_args()


class _BearerTokenMiddleware:
    """Require `Authorization: Bearer <token>` on every MCP request.

    The local MCP endpoint exposes full read/write access to the workspace, so whenever it
    is reachable beyond loopback it must be gated — the shared workspace password doubles
    as the bearer token.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        import hmac

        headers = dict(scope.get("headers") or [])
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(
            presented.strip().encode("utf-8"), self._token
        ):
            await self.app(scope, receive, send)
            return

        from starlette.responses import PlainTextResponse

        response = PlainTextResponse(
            "Unauthorized",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


def _allow_lan_hosts(mcp, public_url: str) -> None:
    """Permit the web origin's Host header; the bearer token is the real access control."""
    settings = getattr(mcp, "settings", None)
    if settings is None or not public_url:
        return
    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError:
        return

    host = public_url.split("://", 1)[-1]
    bare_host = host.split(":")[0]
    candidates = [host, bare_host]
    try:
        settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
            allowed_hosts=candidates,
            allowed_origins=[public_url],
        )
    except (TypeError, ValueError):
        logger.warning("Could not relax MCP transport security for %s", public_url)


async def _init_workspace(workspace_path: str) -> None:
    """Initialize workspace: create dirs, SQLite, default workspace row, scaffold wiki files."""
    ws = Path(workspace_path).resolve()

    (ws / "wiki").mkdir(parents=True, exist_ok=True)
    (ws / ".llmwiki").mkdir(parents=True, exist_ok=True)
    (ws / ".llmwiki" / "cache").mkdir(parents=True, exist_ok=True)

    from vaultfs import SqliteVaultFS
    await SqliteVaultFS.init(str(ws))

    fs = SqliteVaultFS(_LOCAL_USER_ID)
    existing = await fs.get_workspace()
    if not existing:
        ws_name = ws.name
        ws_id = await fs.ensure_workspace(ws_name)
        today = date.today().isoformat()
        overview_content = (
            "---\n"
            "title: Overview\n"
            f"description: Research hub for {ws_name}.\n"
            f"date: {today}\n"
            "tags: [overview, wiki]\n"
            "---\n\n"
            f"This wiki tracks research on {ws_name}.\n\n"
            "## Key Findings\n\n"
            "No sources ingested yet."
        )

        await fs.create_document(
            ws_id, "overview.md", "Overview", "/wiki/", "md",
            overview_content,
            ["overview", "wiki"],
            date=today,
            metadata={"description": f"Research hub for {ws_name}."},
        )
        overview_path = ws / "wiki" / "overview.md"
        if not overview_path.exists():
            overview_path.write_text(overview_content + "\n", encoding="utf-8")

        logger.info("Initialized workspace: %s", ws)
    else:
        logger.info("Workspace ready: %s", ws)


def main():
    args = _parse_args()
    workspace = args.workspace_flag or args.workspace
    workspace = str(Path(workspace).resolve())

    sys.modules["local_server"] = sys.modules[__name__]

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_init_workspace(workspace))

    from tools import register
    from vaultfs import SqliteVaultFS

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        name="LLM Wiki",
        instructions=(
            "You are connected to an LLM Wiki workspace. The user has uploaded files, notes, "
            "and documents that you can read, search, edit, and organize. "
            "Call the `guide` tool first to see available knowledge bases and learn the full workflow."
        ),
    )

    def _get_user_id(ctx):
        return _LOCAL_USER_ID

    register(mcp, _get_user_id, lambda user_id: SqliteVaultFS(user_id))

    @mcp.tool(name="ping", description="Test connectivity")
    async def ping() -> str:
        return "pong"

    logger.info("Local MCP server ready — workspace: %s", workspace)
    if args.transport == "streamable-http":
        _run_streamable_http(mcp, args.host, args.port)
    else:
        asyncio.run(mcp.run_stdio_async())


def _run_streamable_http(mcp, host: str, port: int) -> None:
    """Serve Streamable HTTP ourselves so a bearer token can wrap the ASGI app."""
    import uvicorn

    public_url = os.environ.get("LOCAL_PUBLIC_URL", "").strip()
    token = os.environ.get("LOCAL_AUTH_TOKEN", "").strip()

    mcp.settings.host = host
    mcp.settings.port = port
    _allow_lan_hosts(mcp, public_url)

    app = mcp.streamable_http_app()
    if token:
        app.add_middleware(_BearerTokenMiddleware, token=token)
        logger.info("MCP requires a bearer token")
    else:
        logger.warning("MCP is unauthenticated — keep it on loopback only")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
