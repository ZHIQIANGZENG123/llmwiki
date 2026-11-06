"""Chat bridge: the browser talks to `pi`, and `pi` talks to this app over MCP.

The web UI never holds a model key. It posts a message here; this route starts (or reuses)
a `pi --mode rpc` subprocess, forwards the prompt as one JSONL command, and relays the
assistant's streamed text back as Server-Sent Events.

`pi` is the agent: retrieval, tool choice and citations all happen inside it, against the
MCP endpoint this same app serves. Keeping the model out of this process means the offline
vLLM endpoint and the hosted DeepSeek endpoint differ only in `~/.pi/agent/models.json`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from deps import get_user_id

router = APIRouter(tags=["chat"])

PI_TURN_TIMEOUT = 300.0
# Idle streams die around 30 s at the proxy; a comment every few seconds keeps them open.
HEARTBEAT_SECONDS = 5.0

# The conversation is a reading aid, so it gets the read-only half of the tool surface.
READ_ONLY_TOOLS = ",".join(
    f"mcp__llmwiki__{name}"
    for name in ("guide", "list_knowledge_bases", "search", "read", "ping")
)
PI_IDLE_TIMEOUT = 30 * 60


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[ChatTurn] = []
    session_id: str | None = None
    model: str | None = None
    provider: str | None = None


def _pi_command(request: Request, body: ChatRequest) -> list[str]:
    """Build the argv for a long-lived RPC-mode Pi process.

    `LLMWIKI_PI_BIN` may hold a command with arguments (for example
    `node C:/npm/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js`), which
    avoids spawning the Windows `.cmd` shim — CreateProcess cannot run one directly.
    """
    configured = os.environ.get("LLMWIKI_PI_BIN", "").strip()
    argv = shlex.split(configured, posix=False) if configured else []
    if not argv:
        found = shutil.which("pi")
        if not found:
            raise HTTPException(status_code=503, detail="pi is not installed on this machine")
        argv = [found]
    argv += ["--mode", "rpc", "--no-session"]
    # Pi is a coding agent: by default it carries its own read/bash/glob/grep tools and
    # will happily answer from the repository it happens to sit in. Restrict it to this
    # app's MCP tools — and to the read-only ones, so a reader's conversation can never
    # create, edit or delete pages.
    argv += ["--tools", os.environ.get("LLMWIKI_PI_TOOLS", READ_ONLY_TOOLS)]
    model = body.model or os.environ.get("LLMWIKI_PI_MODEL")
    provider = body.provider or os.environ.get("LLMWIKI_PI_PROVIDER")
    if model:
        argv += ["--model", model]
    if provider:
        argv += ["--provider", provider]
    return argv


def _pi_workdir() -> str:
    """Run Pi from an empty directory so a stray file tool finds nothing to read."""
    repo_root = Path(__file__).resolve().parents[2]
    workdir = repo_root / ".pi-agent" / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    return str(workdir)


def _pi_env(request: Request) -> dict[str, str]:
    """Pi needs the MCP bearer token, the model credentials, and a writable agent dir.

    The default agent directory (`~/.pi/agent`) is not always writable — on Windows the
    harness denies `mkdir` below the user profile, and Pi takes a lock there before it
    starts. Keeping it inside the checkout also makes the offline bundle self-contained.
    """
    env = os.environ.copy()
    token = os.environ.get("LOCAL_AUTH_TOKEN") or ""
    if token:
        env["LLMWIKI_TOKEN"] = token
    env.setdefault("PI_NO_UPDATE_CHECK", "1")
    if not env.get("PI_CODING_AGENT_DIR"):
        repo_root = Path(__file__).resolve().parents[2]
        agent_dir = repo_root / ".pi-agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        env["PI_CODING_AGENT_DIR"] = str(agent_dir)
    return env


async def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _run_pi_turn(
    request: Request,
    body: ChatRequest,
) -> AsyncIterator[str]:
    """Start Pi, send one prompt, relay its streamed deltas, then stop it."""
    argv = _pi_command(request, body)
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_pi_env(request),
            cwd=_pi_workdir(),
            # One JSONL record can carry a whole tool result; CJK text is three bytes per
            # character, so the 64 KiB default reader limit aborts the stream mid-answer.
            limit=64 * 1024 * 1024,
        )
    except FileNotFoundError as error:
        yield await _sse("error", {"message": f"pi not found: {error}"})
        return

    async def emit(text: str) -> str:
        return await _sse("delta", {"text": text})

    # Pi writes diagnostics to stderr. Nothing else reads it, so a chatty run would fill
    # the pipe and block the child; read() consumes it as it arrives.
    stderr_task = asyncio.create_task(process.stderr.read()) if process.stderr is not None else None
    pump_task: asyncio.Task[None] | None = None

    try:
        assert process.stdin is not None and process.stdout is not None

        prompt = body.message
        if body.history:
            transcript = "\n".join(f"{turn.role}: {turn.content}" for turn in body.history[-8:])
            prompt = f"先前的对话：\n{transcript}\n\n当前问题：{body.message}"

        command = {"id": "turn-1", "type": "prompt", "message": prompt}
        process.stdin.write((json.dumps(command, ensure_ascii=False) + "\n").encode("utf-8"))
        await process.stdin.drain()

        yield await _sse("start", {"model": body.model or "", "provider": body.provider or ""})

        # Agent turns go quiet for tens of seconds while pi reads documents. Both the Next
        # proxy and the browser drop an idle stream around 30 s, so bytes must keep flowing:
        # a dedicated reader task feeds a queue and the loop emits a comment every few seconds.
        lines: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def pump() -> None:
            assert process.stdout is not None
            while True:
                try:
                    line = await process.stdout.readline()
                except ValueError:
                    # Record past the reader limit; drop it and keep the answer alive.
                    continue
                if not line:
                    await lines.put(None)
                    return
                await lines.put(line)

        pump_task = asyncio.create_task(pump())

        deadline = time.monotonic() + PI_TURN_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield await _sse("error", {"message": "Timed out waiting for the agent"})
                break

            try:
                raw = await asyncio.wait_for(lines.get(), timeout=min(remaining, HEARTBEAT_SECONDS))
            except asyncio.TimeoutError:
                yield await _sse("ping", {})
                continue
            if raw is None:
                break

            try:
                record = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue

            kind = record.get("type")
            if kind == "message_update":
                event = record.get("assistantMessageEvent") or {}
                # Only final prose. `thinking_delta` and `toolcall_delta` are the model's
                # internal state and raw arguments — they must not reach the chat bubble.
                if event.get("type") == "text_delta" and event.get("delta"):
                    yield await emit(event["delta"])
            elif kind == "tool_execution_start":
                name = (
                    record.get("toolName")
                    or record.get("name")
                    or (record.get("toolCall") or {}).get("name")
                    or ""
                )
                yield await _sse("tool", {"name": name})
            elif kind == "agent_settled":
                yield await _sse("done", {})
                break
    finally:
        if pump_task is not None and not pump_task.done():
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - reader shutdown is best effort
                pass
        try:
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.close()
        except Exception:  # noqa: BLE001 - best effort shutdown
            pass
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        if stderr_task is not None:
            if not stderr_task.done():
                stderr_task.cancel()
            try:
                await stderr_task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - drain is best effort
                pass


@router.post("/v1/chat/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    user_id: str = Depends(get_user_id),
) -> StreamingResponse:
    return StreamingResponse(
        _run_pi_turn(request, body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/v1/chat/ping-stream")
async def ping_stream(user_id: str = Depends(get_user_id)) -> StreamingResponse:
    """Probe: does SSE survive the Next.js rewrite proxy intact?"""

    async def generator() -> AsyncIterator[str]:
        for index in range(3):
            yield await _sse("tick", {"index": index, "at": time.time()})
            await asyncio.sleep(0.5)
        yield await _sse("done", {})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/v1/chat/status")
async def chat_status(user_id: str = Depends(get_user_id)) -> dict:
    binary = os.environ.get("LLMWIKI_PI_BIN") or shutil.which("pi")
    if not binary:
        raise HTTPException(status_code=503, detail="pi is not installed on this machine")
    return {
        "pi": binary,
        "model": os.environ.get("LLMWIKI_PI_MODEL", ""),
        "provider": os.environ.get("LLMWIKI_PI_PROVIDER", ""),
        "home": str(Path.home()),
    }
