"""Security boundary tests for the unauthenticated local HTTP API."""

import importlib.machinery
import importlib.util
import re
from pathlib import Path

import httpx
import pytest
from infra.local_http import (
    LocalHTTPBoundaryMiddleware,
    extension_origin_regex,
    is_allowed_local_host,
    is_allowed_local_origin,
)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


async def _endpoint(request):
    return JSONResponse({"ok": True})


TRUSTED_EXTENSIONS = (
    "chrome-extension://dibilaenlekndomfbampadehjeahemha",
    "moz-extension://d28b2a6e-2456-4f45-9f65-e11c8ea1e65d",
)


def _app() -> Starlette:
    app = Starlette(
        routes=[Route("/", _endpoint, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])]
    )
    app.add_middleware(
        LocalHTTPBoundaryMiddleware,
        app_origin="http://localhost:3000",
        extension_origins=TRUSTED_EXTENSIONS,
    )
    return app


async def _request(
    method: str = "GET",
    *,
    host: str = "localhost:8000",
    origin: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    headers = {"host": host, **(extra_headers or {})}
    if origin is not None:
        headers["origin"] = origin
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()),
        base_url="http://localhost:8000",
    ) as client:
        return await client.request(method, "/", headers=headers)


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
async def test_accepts_only_explicit_loopback_hosts(host):
    assert (await _request(host=host)).status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example:8000",
        "workspace.localhost:8000",
        "127.0.0.2:8000",
        "",
        "[::1]:bad",
        f"localhost:{'9' * 5000}",
    ],
)
async def test_rejects_dns_rebinding_and_malformed_hosts(host):
    response = await _request(host=host)
    assert response.status_code == 400
    assert response.text == "Invalid Host header"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_allows_local_web_ui_mutations(method):
    response = await _request(method, origin="http://localhost:3000")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "chrome-extension://dibilaenlekndomfbampadehjeahemha",
        "moz-extension://d28b2a6e-2456-4f45-9f65-e11c8ea1e65d",
    ],
)
async def test_allows_browser_extension_mutations(origin):
    assert is_allowed_local_origin(origin, "http://localhost:3000", TRUSTED_EXTENSIONS)
    assert (await _request("POST", origin=origin)).status_code == 200


async def test_rejects_untrusted_browser_extension():
    origin = "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef"
    assert not is_allowed_local_origin(origin, "http://localhost:3000", TRUSTED_EXTENSIONS)
    assert (await _request("POST", origin=origin)).status_code == 403


def test_extension_cors_regex_matches_only_configured_ids():
    pattern = extension_origin_regex(TRUSTED_EXTENSIONS)
    assert re.fullmatch(pattern, TRUSTED_EXTENSIONS[0])
    assert not re.fullmatch(
        pattern,
        "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef",
    )


@pytest.mark.parametrize("origin", ["https://attacker.example", "null", "http://localhost:3001"])
async def test_rejects_foreign_browser_mutations(origin):
    response = await _request("POST", origin=origin)
    assert response.status_code == 403
    assert response.text == "Origin not allowed"


def test_configured_app_origin_must_itself_be_loopback():
    assert not is_allowed_local_origin(
        "https://attacker.example",
        "https://attacker.example",
    )


def test_non_loopback_app_origin_requires_explicit_allowlisting():
    origin = "http://192.168.1.10:3000"
    assert not is_allowed_local_origin(origin, origin)
    assert is_allowed_local_origin(origin, origin, (), (origin,))


@pytest.mark.parametrize(
    ("host", "allowed", "expected"),
    [
        ("localhost:8000", (), True),
        ("127.0.0.1:8000", (), True),
        ("192.168.1.10:3000", ("192.168.1.10",), True),
        ("192.168.1.10:3000", ("192.168.1.11",), False),
        ("attacker.example:3000", ("192.168.1.10",), False),
    ],
)
def test_allowed_hosts_gate_beyond_loopback(host, allowed, expected):
    assert is_allowed_local_host(host, allowed) is expected


async def test_middleware_accepts_configured_lan_host_and_origin():
    app = Starlette(routes=[Route("/", _endpoint, methods=["GET", "POST"])])
    app.add_middleware(
        LocalHTTPBoundaryMiddleware,
        app_origin="http://192.168.1.10:3000",
        allowed_hosts=("192.168.1.10",),
        allowed_origins=("http://192.168.1.10:3000",),
    )

    async def post(host: str, origin: str | None) -> httpx.Response:
        headers = {"host": host}
        if origin:
            headers["origin"] = origin
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://192.168.1.10:3000",
        ) as client:
            return await client.post("/", headers=headers)

    assert (await post("192.168.1.10:3000", "http://192.168.1.10:3000")).status_code == 200
    assert (await post("192.168.1.10:3000", "http://evil.example")).status_code == 403
    assert (await post("evil.example:3000", None)).status_code == 400


async def test_non_browser_mutation_without_origin_still_works():
    assert (await _request("POST")).status_code == 200


async def test_cross_site_browser_mutation_cannot_hide_missing_origin():
    response = await _request("POST", extra_headers={"sec-fetch-site": "cross-site"})
    assert response.status_code == 403


async def test_foreign_origin_is_harmless_on_safe_get():
    assert (await _request("GET", origin="https://attacker.example")).status_code == 200


def _load_cli():
    loader = importlib.machinery.SourceFileLoader(
        "llmwiki_cli_for_test",
        str(Path(__file__).resolve().parents[2] / "llmwiki"),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    return cli


def _prepare_serve(tmp_path, monkeypatch, cli, launches):
    """Stub the web build and process spawning, returning a ready workspace path."""
    workspace = tmp_path / "workspace"
    (workspace / ".llmwiki").mkdir(parents=True)
    (workspace / ".llmwiki" / "index.db").touch()

    class _Process:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_popen(args, **kwargs):
        launches.append((args, dict(kwargs.get("env") or {})))
        return _Process()

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli, "_ensure_web_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_vendor_pdf_worker", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_node_executable", lambda: "node")
    monkeypatch.setattr(cli, "_next_binary", lambda: Path("next"))
    monkeypatch.setattr(cli, "_supervise", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_primary_lan_address", lambda: "192.168.1.10")
    return workspace


def test_cli_keeps_api_on_loopback_and_serves_a_built_web_app(tmp_path, monkeypatch):
    cli = _load_cli()
    launches: list[tuple[list[str], dict[str, str]]] = []
    workspace = _prepare_serve(tmp_path, monkeypatch, cli, launches)

    cli.cmd_serve(str(workspace), open_browser=False)

    api_args, api_env = launches[0]
    mcp_args, mcp_env = launches[1]
    web_args, web_env = launches[2]

    assert api_args[api_args.index("--host") + 1] == "127.0.0.1"
    assert "--transport" in mcp_args and "streamable-http" in mcp_args
    # `next start`, not `next dev`: a dev server is not safe to expose on a network.
    assert web_args[2] == "start"
    assert web_args[web_args.index("--hostname") + 1] == "127.0.0.1"

    assert api_env["LOCAL_LAN_ENABLED"] == "0"
    assert api_env["LOCAL_AUTH_TOKEN"] == ""
    assert api_env["LOCAL_PUBLIC_URL"] == "http://localhost:3000"
    assert mcp_env["LOCAL_AUTH_TOKEN"] == ""
    assert web_env["NEXT_PUBLIC_API_URL"] == "/api"
    assert web_env["NEXT_PUBLIC_MODE"] == "local"


def test_cli_exposes_lan_only_with_a_shared_password(tmp_path, monkeypatch):
    cli = _load_cli()
    launches: list[tuple[list[str], dict[str, str]]] = []
    workspace = _prepare_serve(tmp_path, monkeypatch, cli, launches)

    cli.cmd_serve(str(workspace), open_browser=False, host="0.0.0.0", password="s3cret-pass")

    api_args, api_env = launches[0]
    web_args, _ = launches[2]

    assert api_env["LOCAL_LAN_ENABLED"] == "1"
    assert api_env["LOCAL_AUTH_TOKEN"] == "s3cret-pass"
    assert api_env["LOCAL_PUBLIC_URL"] == "http://192.168.1.10:3000"
    assert api_env["LOCAL_ALLOWED_HOSTS"].split(",") and "192.168.1.10:3000" in api_env["LOCAL_ALLOWED_HOSTS"]
    assert api_args[api_args.index("--host") + 1] == "127.0.0.1"
    assert web_args[web_args.index("--hostname") + 1] == "0.0.0.0"


def test_cli_generates_a_lan_password_once_and_reuses_it(tmp_path, monkeypatch):
    cli = _load_cli()
    launches: list[tuple[list[str], dict[str, str]]] = []
    workspace = _prepare_serve(tmp_path, monkeypatch, cli, launches)

    cli.cmd_serve(str(workspace), open_browser=False, host="0.0.0.0")
    password = (workspace / ".llmwiki" / "lan-password.txt").read_text(encoding="utf-8").strip()
    assert len(password) >= 16
    assert launches[0][1]["LOCAL_AUTH_TOKEN"] == password

    launches.clear()
    cli.cmd_serve(str(workspace), open_browser=False, host="0.0.0.0")
    assert launches[0][1]["LOCAL_AUTH_TOKEN"] == password
