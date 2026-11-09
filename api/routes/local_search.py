"""Local-mode full-text search over indexed document content.

The web file grid could only filter by file name, so nothing inside PDFs, Word files or
text sources was findable. Chunks are already stored (and mirrored into the `chunks_fts`
FTS5 table), but that table uses the `porter unicode61` tokenizer: it stems English and
treats an unbroken CJK run as one token, which makes Chinese queries match almost nothing.

So this endpoint searches with `LIKE` over `document_chunks.content` — case-insensitive for
ASCII, plain substring for CJK. With a few hundred chunks that is fast and, unlike FTS, it
finds a word in the middle of a Chinese sentence.

Titles and file names are matched too: a source named `系统架构.pdf` whose extracted text
never repeats the phrase is still the document the user wanted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from deps import get_user_id

router = APIRouter(tags=["search"])

MAX_CHUNKS_SCANNED = 2000
MAX_NAME_MATCHES = 200
SNIPPET_RADIUS = 90


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(content: str, terms: list[str]) -> str:
    """Text around the first matching term, flattened to one line."""
    lowered = content.lower()
    position = -1
    for term in terms:
        found = lowered.find(term.lower())
        if found != -1 and (position == -1 or found < position):
            position = found
    if position == -1:
        position = 0
    start = max(0, position - SNIPPET_RADIUS)
    end = min(len(content), position + SNIPPET_RADIUS)
    text = " ".join(content[start:end].split())
    return ("…" if start > 0 else "") + text + ("…" if end < len(content) else "")


@router.get("/v1/search")
async def search_content(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    scope: str = Query(default="all", max_length=400),
    user_id: str = Depends(get_user_id),
) -> dict:
    db = getattr(request.app.state, "sqlite_db", None)
    if db is None:
        raise HTTPException(status_code=501, detail="Search is only available in local mode")

    terms = [term for term in q.split() if term]
    if not terms:
        return {"query": q, "results": [], "total": 0, "has_more": False}

    def scope_clauses() -> list[str]:
        """`scope` narrows the scan before scoring: one folder, the compiled wiki, or the sources."""
        if scope == "wiki":
            return ["d.source_kind = 'wiki'"]
        if scope == "sources":
            return ["d.source_kind != 'wiki'"]
        if scope and scope != "all":
            return ["d.path LIKE ? ESCAPE '\\'"]
        return []

    def scope_params() -> list[str]:
        if not scope or scope in ("all", "wiki", "sources"):
            return []
        prefix = scope if scope.startswith("/") else "/" + scope
        return [f"{_escape_like(prefix.rstrip('/'))}/%"]

    clauses = ["dc.content LIKE ? ESCAPE '\\'" for _ in terms]
    params: list = [f"%{_escape_like(term)}%" for term in terms]

    # A bare path means "this folder and everything below it".
    clauses.extend(scope_clauses())
    params.extend(scope_params())

    cursor = await db.execute(
        f"SELECT d.id, d.filename, d.title, d.path, d.relative_path, d.file_type, "
        f"d.document_number, d.source_kind, dc.page, dc.content "
        f"FROM document_chunks dc JOIN documents d ON d.id = dc.document_id "
        f"WHERE {' AND '.join(clauses)} "
        f"LIMIT ?",
        (*params, MAX_CHUNKS_SCANNED),
    )
    rows = await cursor.fetchall()

    # Group by document and score, instead of returning whatever the scan hit first.
    # A compiled wiki page states the conclusion; a source holds the raw evidence — the
    # page should be read first, and a file whose *name* matches is likelier the answer.
    by_doc: dict[str, dict] = {}
    lowered_terms = [term.lower() for term in terms]
    for doc_id, filename, title, path, relative_path, file_type, number, kind, page, content in rows:
        entry = by_doc.get(doc_id)
        if entry is None:
            haystack = f"{filename}\n{title or ''}".lower()
            title_hits = sum(1 for term in lowered_terms if term in haystack)
            entry = by_doc[doc_id] = {
                "document_id": doc_id,
                "document_number": number,
                "filename": filename,
                "title": title or filename,
                "path": path,
                "relative_path": relative_path,
                "file_type": file_type,
                "page": page,
                "snippet": _snippet(content or "", terms),
                "match_count": 0,
                "score": 0,
                "_title_hits": title_hits,
                "_kind": kind or "source",
            }
        entry["match_count"] += 1

    # Titles and file names are matched separately: a document whose *name* carries the term
    # but whose body never repeats it would otherwise be invisible to a chunk scan.
    name_clauses = " AND ".join(
        "(d.title LIKE ? ESCAPE '\\' OR d.filename LIKE ? ESCAPE '\\')" for _ in terms
    )
    name_params: list = [f"%{_escape_like(term)}%" for term in terms for _ in range(2)]
    name_scope = scope_clauses()
    name_params.extend(scope_params())
    name_sql = (
        f"SELECT d.id, d.filename, d.title, d.path, d.relative_path, d.file_type, "
        f"d.document_number, d.source_kind "
        f"FROM documents d WHERE d.status != 'failed' AND {name_clauses}"
        + (f" AND {' AND '.join(name_scope)}" if name_scope else "")
        + " ORDER BY d.document_number, d.filename LIMIT ?"
    )
    cursor = await db.execute(name_sql, (*name_params, MAX_NAME_MATCHES))
    for doc_id, filename, title, path, relative_path, file_type, number, kind in await cursor.fetchall():
        if doc_id in by_doc:
            continue
        haystack = f"{filename}\n{title or ''}".lower()
        by_doc[doc_id] = {
            "document_id": doc_id,
            "document_number": number,
            "filename": filename,
            "title": title or filename,
            "path": path,
            "relative_path": relative_path,
            "file_type": file_type,
            "page": None,
            "snippet": _snippet(f"{title or ''}\n{filename}", terms),
            "match_count": 0,
            "score": 0,
            "_title_hits": sum(1 for term in lowered_terms if term in haystack),
            "_kind": kind or "source",
        }

    for entry in by_doc.values():
        entry["score"] = (
            (100 if entry["_kind"] == "wiki" else 0)
            + 50 * entry.pop("_title_hits")
            + min(entry["match_count"], 10)
        )
        entry.pop("_kind", None)

    ranked = sorted(
        by_doc.values(),
        key=lambda item: (-item["score"], item["document_number"] or 0, item["filename"]),
    )
    total = len(ranked)
    page_results = ranked[offset:offset + limit]

    return {
        "query": q,
        "results": page_results,
        "total": total,
        "has_more": offset + limit < total,
    }
