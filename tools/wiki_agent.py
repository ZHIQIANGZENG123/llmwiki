#!/usr/bin/env python3
"""Drive an LLM Wiki workspace with an LLM over MCP.

The wiki is written by an agent, not by this repository: the app exposes an MCP tool
surface and something has to call it. This script is the smallest possible bridge 鈥?MCP tools in, model tool calls out 鈥?and it speaks both OpenAI-compatible endpoints and
Ollama's native chat API, so an online model today and a local model tomorrow are the same
command with a different `--base-url`.

Requirements: the `mcp` and `httpx` packages (both installed by the documented setup).

    # 0. Check the MCP endpoint and see the tool surface (no model needed)
    python tools/wiki_agent.py --token <password> --list-tools

    # 1. Online model (OpenAI-compatible endpoint)
    set LLMWIKI_API_KEY=sk-...
    python tools/wiki_agent.py --token <password> ^
        --base-url https://api.deepseek.com/v1 --model deepseek-flash --system prompts/compiler.md

    # 2. Local model later (Ollama exposes an OpenAI-compatible endpoint too)
    python tools/wiki_agent.py --token <password> ^
        --base-url http://127.0.0.1:11434/v1 --model qwen2.5:14b

Anything the model cannot do with the exposed tools, it must ask for 鈥?this script never
invents file access of its own.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"

COMPILER_SYSTEM = """You are the compiler of this knowledge base, not a chat assistant.

1. Call `guide` first: it describes the workspace and the knowledge-base slugs.
2. Organise pages by TOPIC, not by source file. Several sources about one subject belong
   in one page.
3. Use `read` (with page ranges for long documents) to read a source completely before
   writing about it.
4. Every factual statement needs a footnote citation back to its source document and page.
   If you cannot cite it, do not write it.
5. Paraphrase; do not paste source text. Keep proper nouns, numbers and identifiers exact.
6. Finish by calling `lint` and fixing every broken citation, dangling link and orphan page.

Never invent content that is not in the sources."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an LLM against LLM Wiki over MCP")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:3000/mcp", help="MCP endpoint")
    parser.add_argument(
        "--token",
        default=os.environ.get("LLMWIKI_TOKEN", ""),
        help="Workspace password (LOCAL_AUTH_TOKEN). Required when shared on a LAN.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Model API base: OpenAI-compatible (鈥?v1) or Ollama native (:11434)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("LLMWIKI_API_KEY", ""),
        help="API key for online providers. Prefer the LLMWIKI_API_KEY environment variable.",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "openai", "ollama"],
        default="auto",
        help="auto: use OpenAI-compatible when the base URL ends in /v1, else Ollama native.",
    )
    parser.add_argument("--model", default="deepseek-flash", help="Model id")
    parser.add_argument("--prompt", default="", help="Instruction to run")
    parser.add_argument("--system", default="", help="System prompt, or a path to a file holding one")
    parser.add_argument("--max-steps", type=int, default=40, help="Tool-call rounds before stopping")
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    parser.add_argument("--timeout", type=float, default=600.0, help="Per-request timeout in seconds")
    parser.add_argument("--out", default="", help="Also write the final answer to this file as UTF-8")
    parser.add_argument("--list-tools", action="store_true", help="Print the MCP tool surface and exit")
    parser.add_argument("--verbose", action="store_true", help="Log every tool call and result")
    return parser.parse_args()


def _emit_answer(text: str, args: argparse.Namespace) -> None:
    """Print the final answer, and optionally save it, without depending on console encoding.

    A Windows console in GBK raises UnicodeEncodeError on characters a model emits freely
    (鈭? 鉁? emoji), which used to abort the run after the work was already done.
    """
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for stream in (sys.stdout,):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    print(text)


def _resolve_provider(args: argparse.Namespace) -> str:
    if args.provider != "auto":
        return args.provider
    return "openai" if args.base_url.rstrip("/").endswith("/v1") else "ollama"


def _system_prompt(args: argparse.Namespace) -> str:
    if not args.system:
        return ""
    candidate = Path(args.system)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return args.system


def _unwrap_streams(streams):
    """`streamablehttp_client` yields 2- or 3-tuples depending on the mcp SDK version."""
    if not isinstance(streams, tuple):
        return streams, None
    return streams[0], streams[1]


def _tool_result_text(result) -> str:
    """Flatten an MCP CallToolResult into text for the model."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        else:
            parts.append(json.dumps({"type": getattr(block, "type", "unknown")}))
    if getattr(result, "isError", False):
        return "ERROR: " + ("\n".join(parts) or "tool reported an error")
    return "\n".join(parts) or "(no content)"


def _tool_schemas(tools) -> list[dict]:
    """OpenAI/Anthropic-style function schemas; Ollama accepts the same shape."""
    schemas = []
    for tool in tools:
        parameters = tool.inputSchema or {}
        if parameters.get("type") != "object":
            parameters = {"type": "object", "properties": {}, "required": []}
        schemas.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": parameters,
            },
        })
    return schemas


def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _call_tool(session, name: str, arguments: dict, verbose: bool, step: int) -> str:
    if verbose:
        preview = json.dumps(arguments, ensure_ascii=False)[:400]
        print(f"[{step}] {name} {preview}", file=sys.stderr)
    try:
        return _tool_result_text(await session.call_tool(name, arguments))
    except Exception as error:  # noqa: BLE001 - surface any tool failure to the model
        return f"ERROR: {error}"


def _endpoint_error(status: int, body: str, args: argparse.Namespace, provider: str) -> str | None:
    if status < 400:
        return None
    if status == 401:
        return "Authentication failed (401). Check --api-key / LLMWIKI_API_KEY."
    if status == 404:
        if provider == "ollama":
            return f"Model {args.model!r} not found. Pull it first: ollama pull {args.model}"
        return f"Not found (404) for model {args.model!r}. Check --model and --base-url."
    if status == 402:
        return "Insufficient balance (402). Top up the provider account."
    if status == 429:
        return "Rate limited (429). Retry later or lower concurrency."
    return f"HTTP {status}: {body[:400]}"


async def _run(args: argparse.Namespace) -> int:
    import importlib.util

    missing = [name for name in ("httpx", "mcp") if importlib.util.find_spec(name) is None]
    if missing:
        print(
            f"Missing dependencies: {', '.join(missing)}. Install with:\n"
            "  pip install -r api/requirements.txt -r mcp/requirements.txt",
            file=sys.stderr,
        )
        return 2

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}

    async with streamablehttp_client(args.mcp_url, headers=headers) as streams:
        read_stream, write_stream = _unwrap_streams(streams)
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            discovered = await session.list_tools()
            tools = discovered.tools

            if args.list_tools:
                print(f"{len(tools)} tools exposed by {args.mcp_url}:\n")
                for tool in tools:
                    print(f"  {tool.name}")
                    if tool.description:
                        print(f"      {tool.description.splitlines()[0]}")
                return 0

            if not args.prompt:
                print("Nothing to do: pass --prompt or --list-tools.", file=sys.stderr)
                return 2

            return await _converse(args, session, tools)


async def _converse(args: argparse.Namespace, session, tools) -> int:
    import httpx

    provider = _resolve_provider(args)
    system = _system_prompt(args)
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": args.prompt})

    schemas = _tool_schemas(tools)
    if provider == "openai":
        url = f"{args.base_url.rstrip('/')}/chat/completions"
    else:
        url = f"{args.base_url.rstrip('/')}/api/chat"

    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    total_in = total_out = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout, connect=15.0)) as client:
        for step in range(1, args.max_steps + 1):
            payload: dict = {
                "model": args.model,
                "messages": messages,
                "tools": schemas,
                "stream": False,
                "temperature": args.temperature,
            }
            if provider == "ollama":
                payload.pop("temperature", None)
                payload["options"] = {"temperature": args.temperature}

            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as error:
                print(f"Cannot reach {url}: {error}", file=sys.stderr)
                return 3

            problem = _endpoint_error(response.status_code, response.text, args, provider)
            if problem:
                print(problem, file=sys.stderr)
                return 3

            data = response.json()
            usage = data.get("usage") or {}
            total_in += int(usage.get("prompt_tokens") or 0)
            total_out += int(usage.get("completion_tokens") or 0)

            if provider == "openai":
                message = (data.get("choices") or [{}])[0].get("message") or {}
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    _emit_answer((message.get("content") or "").strip(), args)
                    _report_usage(total_in, total_out, args)
                    return 0
                messages.append({
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                })
                for call in tool_calls:
                    function = call.get("function") or {}
                    output = await _call_tool(
                        session, function.get("name") or "",
                        _parse_arguments(function.get("arguments")), args.verbose, step,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id") or "",
                        "content": output,
                    })
            else:
                message = data.get("message") or {}
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    _emit_answer((message.get("content") or "").strip(), args)
                    _report_usage(total_in, total_out, args)
                    return 0
                messages.append({
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                })
                for call in tool_calls:
                    function = call.get("function") or {}
                    name = function.get("name") or ""
                    output = await _call_tool(
                        session, name, _parse_arguments(function.get("arguments")),
                        args.verbose, step,
                    )
                    messages.append({"role": "tool", "tool_name": name, "content": output})

        _report_usage(total_in, total_out, args)
        print(
            f"Stopped after {args.max_steps} tool rounds without a final answer. "
            "Raise --max-steps or use a model that finishes its plan.",
            file=sys.stderr,
        )
        return 1


def _report_usage(prompt_tokens: int, completion_tokens: int, args: argparse.Namespace) -> None:
    if not (prompt_tokens or completion_tokens):
        return
    suffix = ""
    if args.verbose:
        suffix = f"  (model {args.model})"
    print(
        f"\n[tokens] in={prompt_tokens:,} out={completion_tokens:,}{suffix}",
        file=sys.stderr,
    )


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    sys.exit(main())
