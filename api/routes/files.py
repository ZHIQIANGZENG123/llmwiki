"""Local-mode file serving (cache artifacts and workspace sources) with Range support and traversal protection."""

import asyncio
import mimetypes
import shutil
import sqlite3
import time
import uuid
import zipfile
from pathlib import Path

from deps import get_user_id, require_admin
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

router = APIRouter(tags=["files"])

ACTIVE_CONTENT_TYPES = frozenset({"text/html", "image/svg+xml", "application/xhtml+xml", "text/xml", "application/xml"})
ARCHIVE_TTL_SECONDS = 60 * 60

ACTIVE_CONTENT_TYPES = frozenset({"text/html", "image/svg+xml", "application/xhtml+xml", "text/xml", "application/xml"})

# Keep in sync with domain.watcher.IGNORE_DIRS: folders the indexer never walks.
IGNORED_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", ".llmwiki", "venv", ".venv",
    ".idea", ".vscode",
})

_workspace_root: Path | None = None


def set_workspace_root(path: str) -> None:
    global _workspace_root
    _workspace_root = Path(path).resolve()


def _resolve_safe(key: str) -> Path:
    """Resolve a key to a servable file under the workspace, rejecting traversal and hidden paths."""
    if _workspace_root is None:
        raise HTTPException(status_code=501, detail="Local file serving not configured")

    cache_root = _workspace_root / ".llmwiki" / "cache"
    cache_path = (cache_root / key).resolve()
    if cache_path.is_file() and cache_path.is_relative_to(cache_root.resolve()):
        return cache_path

    root_path = (_workspace_root / key).resolve()
    if root_path.is_file() and root_path.is_relative_to(_workspace_root) and not _is_hidden(root_path):
        return root_path

    raise HTTPException(status_code=404, detail="File not found")


def _is_hidden(path: Path) -> bool:
    """True when any segment below the workspace root is a dotfile or dot-directory (incl. .llmwiki/)."""
    return any(part.startswith(".") for part in path.relative_to(_workspace_root).parts)


@router.get("/v1/folders")
async def list_folders(
    path: str = "/",
    user_id: str = Depends(get_user_id),
) -> dict:
    """Subdirectories of `path` in the workspace.

    The file grid used to derive folders from indexed documents, so a folder that held
    nothing — freshly created, or one whose files are all ignored — never appeared and
    could not be navigated into or uploaded to.
    """
    if _workspace_root is None:
        raise HTTPException(status_code=501, detail="Local file serving not configured")

    relative = path.strip("/")
    target = (_workspace_root / relative).resolve() if relative else _workspace_root
    if not target.is_dir() or not target.is_relative_to(_workspace_root):
        raise HTTPException(status_code=404, detail="Folder not found")
    if target != _workspace_root and _is_hidden(target):
        raise HTTPException(status_code=404, detail="Folder not found")

    prefix = "" if target == _workspace_root else target.relative_to(_workspace_root).as_posix() + "/"
    at_root = target == _workspace_root
    folders = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Cannot list folder: {error}") from error

    for entry in entries:
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in IGNORED_DIRS:
            continue
        # `wiki/` holds generated pages; the Files view hides those documents, so listing
        # the folder would offer a place with nothing to show.
        if at_root and entry.name == "wiki":
            continue
        folders.append({"name": entry.name, "path": f"/{prefix}{entry.name}/"})
    return {"path": f"/{prefix}" if prefix else "/", "folders": folders}


@router.post("/v1/folders", status_code=201)
async def create_folder(
    payload: dict = Body(...),
    _admin: str = Depends(require_admin),
) -> dict:
    """Create an empty folder.

    The UI used to create a note inside the new path just to force the directory into
    existence, which left an empty `Untitled.md` in every folder. Making the directory is
    the whole job.
    """
    if _workspace_root is None:
        raise HTTPException(status_code=501, detail="Local file serving not configured")

    raw = str(payload.get("path") or "").strip()
    name = raw.rstrip("/").rsplit("/", 1)[-1]
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid folder name")
    if name.startswith(".") or name in IGNORED_DIRS:
        raise HTTPException(status_code=400, detail="That folder name is reserved")

    parent = raw.rstrip("/").rsplit("/", 1)[0].strip("/")
    parent_dir = (_workspace_root / parent).resolve() if parent else _workspace_root
    if not parent_dir.is_dir() or not parent_dir.is_relative_to(_workspace_root):
        raise HTTPException(status_code=404, detail="Parent folder not found")

    target = parent_dir / name
    if target.exists():
        raise HTTPException(status_code=409, detail="A folder with that name already exists")
    try:
        target.mkdir()
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Cannot create folder: {error}") from error

    prefix = target.relative_to(_workspace_root).as_posix()
    return {"path": f"/{prefix}/"}


def _archive_dir() -> Path:
    root = _workspace_root / ".llmwiki" / "cache" / "export"
    root.mkdir(parents=True, exist_ok=True)
    for stale in root.glob("*.zip"):
        try:
            if time.time() - stale.stat().st_mtime > ARCHIVE_TTL_SECONDS:
                stale.unlink()
        except OSError:
            pass
    return root


def _iter_archive_files(root: Path):
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        relative = entry.relative_to(root)
        if any(part.startswith(".") or part in IGNORED_DIRS for part in relative.parts):
            continue
        yield entry, relative


@router.get("/v1/archive")
async def download_archive(
    request: Request,
    path: str = Query(default="/"),
    ids: str = Query(default=""),
    _role: str = Depends(get_user_id),
) -> Response:
    """Zip a folder, or a set of documents, and hand it back as a download."""
    if _workspace_root is None:
        raise HTTPException(status_code=501, detail="Local file serving not configured")

    targets: list[tuple[Path, Path]] = []
    if ids.strip():
        db = getattr(request.app.state, "sqlite_db", None)
        if db is None:
            raise HTTPException(status_code=501, detail="Archive by id needs local mode")
        wanted = [item for item in ids.split(",") if item.strip()]
        placeholders = ",".join("?" for _ in wanted)
        cursor = await db.execute(
            f"SELECT relative_path FROM documents WHERE id IN ({placeholders})",
            tuple(wanted),
        )
        for (relative_path,) in await cursor.fetchall():
            candidate = (_workspace_root / relative_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(_workspace_root) and not _is_hidden(candidate):
                targets.append((candidate, Path(candidate.name)))
        if not targets:
            raise HTTPException(status_code=404, detail="No downloadable files in the selection")
        label = "selection"
    else:
        relative = path.strip("/")
        folder = (_workspace_root / relative).resolve() if relative else _workspace_root
        if not folder.is_dir() or not folder.is_relative_to(_workspace_root):
            raise HTTPException(status_code=404, detail="Folder not found")
        if folder != _workspace_root and _is_hidden(folder):
            raise HTTPException(status_code=404, detail="Folder not found")
        targets = list(_iter_archive_files(folder))
        if not targets:
            raise HTTPException(status_code=404, detail="This folder has no files to download")
        label = folder.name or "workspace"

    archive_dir = _archive_dir()
    archive_path = archive_dir / f"{uuid.uuid4().hex}.zip"
    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for source, arcname in targets:
                bundle.write(source, arcname.as_posix())
    except OSError as error:
        archive_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Cannot build archive: {error}") from error

    return FileResponse(
        path=str(archive_path),
        media_type="application/zip",
        filename=f"{label}.zip",
    )


async def _delete_documents_under(db_path: Path, like: str) -> None:
    """Delete index rows for every document under a folder prefix."""
    import aiosqlite

    try:
        async with aiosqlite.connect(db_path, timeout=20) as db:
            await db.execute("PRAGMA busy_timeout=20000")
            await db.execute(
                "DELETE FROM document_chunks WHERE document_id IN "
                "(SELECT id FROM documents WHERE relative_path LIKE ? ESCAPE '\\')",
                (like,),
            )
            await db.execute(
                "DELETE FROM documents WHERE relative_path LIKE ? ESCAPE '\\'",
                (like,),
            )
            await db.commit()
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            raise HTTPException(
                status_code=503,
                detail="知识库正在写入（索引中），请等几秒再删",
            ) from error
        raise HTTPException(status_code=500, detail=f"清理索引失败: {error}") from error


@router.delete("/v1/folders", status_code=204)
async def delete_folder(
    request: Request,
    path: str = Query(...),
    _admin: str = Depends(require_admin),
) -> Response:
    """Delete a folder, its files on disk, and their index rows.

    Admin-only: an accidental delete here removes real files, not just a listing.
    """
    if _workspace_root is None:
        raise HTTPException(status_code=501, detail="Local file serving not configured")

    relative = path.strip("/")
    if not relative:
        raise HTTPException(status_code=400, detail="Refusing to delete the workspace root")

    target = (_workspace_root / relative).resolve()
    if not target.is_dir() or not target.is_relative_to(_workspace_root):
        raise HTTPException(status_code=404, detail="Folder not found")
    if _is_hidden(target):
        raise HTTPException(status_code=400, detail="That folder cannot be deleted")
    if target.name in IGNORED_DIRS:
        raise HTTPException(status_code=400, detail="That folder cannot be deleted")

    # Use a dedicated connection: the shared request connection can be sitting in an
    # uncommitted transaction (another request's write), which would block this delete
    # forever. A private connection with its own busy timeout waits out the indexer.
    db_path = _workspace_root / ".llmwiki" / "index.db"
    if db_path.is_file():
        prefix = target.relative_to(_workspace_root).as_posix()
        like = f"{_escape_like(prefix)}/%"
        await _delete_documents_under(db_path, like)

    try:
        shutil.rmtree(target)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Cannot delete folder: {error}") from error

    return Response(status_code=204)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/v1/documents/{doc_id}/download")
async def download_document(
    doc_id: str,
    request: Request,
    name: str | None = None,
    _role: str = Depends(get_user_id),
) -> Response:
    """Download one document by id.

    The path is a UUID so the URL stays ASCII: the Next proxy answers 404 when a
    percent-encoded (non-ASCII) path is combined with a query string, which is exactly
    the shape `/v1/files/<中文路径>?download=1` has.
    """
    if _workspace_root is None:
        raise HTTPException(status_code=501, detail="Local file serving not configured")
    db = getattr(request.app.state, "sqlite_db", None)
    if db is None:
        raise HTTPException(status_code=501, detail="Download by id needs local mode")

    cursor = await db.execute(
        "SELECT relative_path, filename FROM documents WHERE id = ?",
        (doc_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")

    relative_path, filename = row
    target = (_workspace_root / relative_path).resolve()
    if not target.is_file() or not target.is_relative_to(_workspace_root) or _is_hidden(target):
        raise HTTPException(status_code=404, detail="File is missing on disk")

    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(
        path=str(target),
        media_type=media_type,
        filename=name or filename,
    )


@router.get("/v1/tree")
async def list_tree(
    request: Request,
    path: str = Query(default="/"),
    _role: str = Depends(get_user_id),
) -> dict:
    """One directory level: sub-folders (empty ones included) and files.

    `/v1/folders` lists folders only and `documents` knows only indexed text files, so
    neither can drive a file-explorer tree. This reads the directory and joins the index
    so each file carries its document id when it has one.
    """
    if _workspace_root is None:
        raise HTTPException(status_code=501, detail="Local file serving not configured")

    relative = path.strip("/")
    target = (_workspace_root / relative).resolve() if relative else _workspace_root
    if not target.is_dir() or not target.is_relative_to(_workspace_root):
        raise HTTPException(status_code=404, detail="Folder not found")
    if target != _workspace_root and _is_hidden(target):
        raise HTTPException(status_code=404, detail="Folder not found")

    at_root = target == _workspace_root
    prefix = "" if at_root else target.relative_to(_workspace_root).as_posix() + "/"

    folders: list[dict] = []
    files: list[dict] = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Cannot list folder: {error}") from error

    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            if entry.name in IGNORED_DIRS:
                continue
            # `wiki/` holds generated pages; the tree is the user's own documents.
            if at_root and entry.name == "wiki":
                continue
            folders.append({"name": entry.name, "path": f"/{prefix}{entry.name}/"})
        elif entry.is_file():
            rel = f"{prefix}{entry.name}"
            try:
                size = entry.stat().st_size
            except OSError:
                size = None
            files.append({
                "name": entry.name,
                "path": f"/{rel}",
                "size": size,
                "file_type": entry.suffix.lower().lstrip("."),
                "document_id": None,
                "document_number": None,
            })

    if files:
        db = getattr(request.app.state, "sqlite_db", None)
        if db is not None:
            wanted = [f["path"].lstrip("/") for f in files]
            placeholders = ",".join("?" for _ in wanted)
            cursor = await db.execute(
                f"SELECT relative_path, id, document_number FROM documents "
                f"WHERE relative_path IN ({placeholders})",
                tuple(wanted),
            )
            by_path = {row[0]: (row[1], row[2]) for row in await cursor.fetchall()}
            for item in files:
                hit = by_path.get(item["path"].lstrip("/"))
                if hit:
                    item["document_id"], item["document_number"] = hit

    return {"path": f"/{prefix}" if prefix else "/", "folders": folders, "files": files}


@router.get("/v1/files/{key:path}")
async def serve_file(
    key: str,
    request: Request,
    download: bool = False,
    name: str | None = None,
    user_id: str = Depends(get_user_id),
) -> Response:
    path = _resolve_safe(key)

    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    stat = path.stat()
    etag = f'"{hash((path, stat.st_mtime, stat.st_size)):x}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    headers = {"ETag": etag}
    if content_type in ACTIVE_CONTENT_TYPES:
        headers["Content-Security-Policy"] = "sandbox"
    if download:
        # Let FileResponse build Content-Disposition: it applies RFC 5987, so a Chinese
        # filename survives (a hand-written header is latin-1 only and raises).
        return FileResponse(
            path=str(path),
            media_type=content_type,
            headers=headers,
            filename=name or path.name,
        )
    return FileResponse(path=str(path), media_type=content_type, headers=headers)
