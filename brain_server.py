"""
brain_server.py — FastAPI second brain server with semantic search.

Run with: ~/mike-pod/venv/bin/python ~/mike-pod/brain_server.py
Serves on port 7847. Root path /brain for Caddy reverse proxy.
"""
import sys
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "brain"))

from brain_config import CHROMA_PATH, COLLECTION_NAME, OLLAMA_EMBED_MODEL
from typing import Optional

import chromadb
import ollama
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

PORT = 7847

# ── ChromaDB client ────────────────────────────────────────────────────────────
_client: Optional[chromadb.PersistentClient] = None

def get_collection():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )

def embed_query(text: str) -> list:
    resp = ollama.embeddings(model=OLLAMA_EMBED_MODEL, prompt=text)
    return resp["embedding"]

# ── FastAPI ────────────────────────────────────────────────────────────────────
app = FastAPI(title="Mike's Second Brain", root_path="/brain")

class SearchRequest(BaseModel):
    query: str
    n_results: int = 10
    source: Optional[str] = None

class ContextRequest(BaseModel):
    query: str
    limit: int = 8
    sources: Optional[list[str]] = None

# Source badge colors
SOURCE_COLORS = {
    "blog": "#6366f1",      # indigo
    "github": "#22c55e",    # green
    "youtube": "#ef4444",   # red
    "articles": "#f97316",  # orange
    "convex": "#f97316",    # backwards compatibility
}

def source_color(source: str) -> str:
    return SOURCE_COLORS.get(source, "#94a3b8")

DATE_FIELDS = ("date", "upload_date", "published_at", "created_at", "updated_at", "timestamp")

EVAL_QUERIES = [
    {"name": "Convex agents", "query": "Convex AI agents", "expected_sources": ["youtube", "articles", "github"], "min_results": 1},
    {"name": "Podcast preferences", "query": "mike-pod podcast personal relevance enterprise fluff", "min_results": 1},
    {"name": "Daily Brief preferences", "query": "Daily Brief AI software Australian tech crypto exclusion", "min_results": 1},
    {"name": "Kid Vibes stack", "query": "Kid Vibes Expo React Native Convex agents", "expected_sources": ["github"], "min_results": 1},
    {"name": "Readable code", "query": "write readable code 30 years experience", "expected_sources": ["blog"], "min_results": 1},
    {"name": "Convex aggregate component", "query": "Convex aggregate component count sum max", "expected_sources": ["articles"], "min_results": 1},
]


def parse_item_date(value) -> datetime | None:
    """Best-effort metadata date parser for freshness reporting."""
    if value is None or value == "":
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.isdigit() and len(raw) == 8:  # YouTube upload_date: YYYYMMDD
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        if raw.isdigit() and len(raw) >= 10:  # Unix timestamp-ish
            return datetime.fromtimestamp(int(raw[:10]), tz=timezone.utc)
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = parsedate_to_datetime(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def metadata_date(meta: dict) -> tuple[datetime | None, str | None, str | None]:
    for field in DATE_FIELDS:
        if field in meta:
            dt = parse_item_date(meta.get(field))
            if dt:
                return dt, field, str(meta.get(field))
    return None, None, None


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def days_ago(dt: datetime | None) -> int | None:
    if not dt:
        return None
    return max(0, int((datetime.now(timezone.utc) - dt).total_seconds() // 86400))

def canonical_key(doc_id: str, meta: dict) -> str:
    """Stable key for a real item, not an embedding chunk.

    Chroma stores long items as many chunks for retrieval. The web UI should show
    one card per source URL/video/repo/article, otherwise a 20-minute video looks
    like 20 duplicate documents. Keep the chunks in Chroma; collapse only at API
    presentation time.
    """
    src = meta.get("source", "unknown")
    url = meta.get("url") or ""
    if src == "youtube" and meta.get("video_id"):
        return f"youtube:{meta['video_id']}"
    if url:
        return f"{src}:{url.rstrip('/')}"
    title = meta.get("title") or doc_id
    return f"{src}:{title}"

def build_item(doc_id: str, meta: dict, doc: str, *, score: float | None = None, chunk_count: int = 1) -> dict:
    item = {
        "id": doc_id,
        "title": meta.get("title", doc_id),
        "source": meta.get("source", "unknown"),
        "url": meta.get("url", ""),
        "date": meta.get("date", meta.get("upload_date", meta.get("published_at", meta.get("timestamp", "")))),
        "excerpt": (doc or "")[:400],
        "chunk_count": chunk_count,
        "metadata": meta,
    }
    if score is not None:
        item["score"] = round(score, 4)
    return item

def collapse_chunks(ids: list, metas: list, docs: list, scores: list | None = None) -> list:
    """Collapse chunk rows into unique source items, preserving input order.

    For search, input order is relevance order, so the first chunk per item is
    the strongest representative. For browsing, input order is Chroma insertion
    order, so the first chunk is the natural excerpt.
    """
    grouped = {}
    order = []
    for i, doc_id in enumerate(ids):
        meta = metas[i] or {}
        key = canonical_key(doc_id, meta)
        score = scores[i] if scores is not None else None
        if key not in grouped:
            grouped[key] = {
                "id": doc_id,
                "meta": meta,
                "doc": docs[i] or "",
                "score": score,
                "chunk_count": 1,
            }
            order.append(key)
        else:
            grouped[key]["chunk_count"] += 1
            # For search, keep the best scoring representative if one appears later.
            if score is not None and (grouped[key]["score"] is None or score > grouped[key]["score"]):
                grouped[key].update({"id": doc_id, "meta": meta, "doc": docs[i] or "", "score": score})
    return [build_item(g["id"], g["meta"], g["doc"], score=g["score"], chunk_count=g["chunk_count"]) for g in (grouped[k] for k in order)]

# ── API endpoints ──────────────────────────────────────────────────────────────
@app.get("/api/stats")
def api_stats():
    col = get_collection()
    total = col.count()
    all_meta = col.get(include=["metadatas", "documents"])
    source_counts = {}
    for meta in all_meta["metadatas"]:
        src = meta.get("source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1

    unique_items = collapse_chunks(all_meta["ids"], all_meta["metadatas"], all_meta["documents"])
    unique_source_counts = {}
    for item in unique_items:
        src = item.get("source", "unknown")
        unique_source_counts[src] = unique_source_counts.get(src, 0) + 1

    return {
        "total": total,
        "unique_total": len(unique_items),
        "by_source": source_counts,
        "by_source_unique": unique_source_counts,
    }

@app.get("/api/sources")
def api_sources():
    col = get_collection()
    all_meta = col.get(include=["metadatas", "documents"])
    unique_items = collapse_chunks(all_meta["ids"], all_meta["metadatas"], all_meta["documents"])
    source_counts = {}
    chunk_counts = {}
    for meta in all_meta["metadatas"]:
        src = meta.get("source", "unknown")
        chunk_counts[src] = chunk_counts.get(src, 0) + 1
    for item in unique_items:
        src = item.get("source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
    return [{"source": k, "count": v, "chunks": chunk_counts.get(k, 0), "color": source_color(k)} 
            for k, v in sorted(source_counts.items())]


def freshness_summary() -> dict:
    col = get_collection()
    all_docs = col.get(include=["metadatas", "documents"])
    unique_items = collapse_chunks(all_docs["ids"], all_docs["metadatas"], all_docs["documents"])
    now = datetime.now(timezone.utc)
    chroma_path = Path(CHROMA_PATH)
    db_mtime = datetime.fromtimestamp(chroma_path.stat().st_mtime, tz=timezone.utc) if chroma_path.exists() else None

    by_source: dict[str, dict] = {}
    recent_items = []
    undated_count = 0
    for item in unique_items:
        src = item.get("source", "unknown")
        meta = item.get("metadata") or {}
        dt, field, raw = metadata_date(meta)
        bucket = by_source.setdefault(src, {
            "source": src,
            "unique_items": 0,
            "chunks": 0,
            "dated_items": 0,
            "undated_items": 0,
            "latest_item_at": None,
            "oldest_item_at": None,
            "latest_item_title": None,
            "latest_item_url": None,
            "latest_date_field": None,
            "latest_days_ago": None,
            "color": source_color(src),
        })
        bucket["unique_items"] += 1
        bucket["chunks"] += int(item.get("chunk_count") or 1)
        if dt:
            bucket["dated_items"] += 1
            if bucket["latest_item_at"] is None or dt > parse_item_date(bucket["latest_item_at"]):
                bucket["latest_item_at"] = iso(dt)
                bucket["latest_item_title"] = item.get("title")
                bucket["latest_item_url"] = item.get("url")
                bucket["latest_date_field"] = field
                bucket["latest_days_ago"] = days_ago(dt)
            if bucket["oldest_item_at"] is None or dt < parse_item_date(bucket["oldest_item_at"]):
                bucket["oldest_item_at"] = iso(dt)
            recent_items.append({
                "title": item.get("title"),
                "source": src,
                "url": item.get("url"),
                "date": iso(dt),
                "days_ago": days_ago(dt),
                "date_field": field,
                "raw_date": raw,
                "chunk_count": item.get("chunk_count", 1),
            })
        else:
            bucket["undated_items"] += 1
            undated_count += 1

    recent_items.sort(key=lambda i: i.get("date") or "", reverse=True)
    sources = sorted(by_source.values(), key=lambda s: (s.get("latest_item_at") or ""), reverse=True)
    warnings = []
    for s in sources:
        if s["dated_items"] == 0:
            warnings.append(f"{s['source']} has no parseable item dates")
        elif s["latest_days_ago"] is not None and s["latest_days_ago"] > 120:
            warnings.append(f"{s['source']} latest dated item is {s['latest_days_ago']} days old")
    if undated_count:
        warnings.append(f"{undated_count} unique items have no parseable date metadata")

    return {
        "generated_at": iso(now),
        "database_path": CHROMA_PATH,
        "database_mtime": iso(db_mtime),
        "database_mtime_days_ago": days_ago(db_mtime),
        "unique_total": len(unique_items),
        "chunk_total": all_docs and len(all_docs.get("ids", [])) or 0,
        "undated_unique_items": undated_count,
        "sources": sources,
        "recent_items": recent_items[:20],
        "warnings": warnings,
    }


@app.get("/api/freshness")
def api_freshness():
    return freshness_summary()

@app.get("/api/documents")
def api_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source: Optional[str] = Query(None)
):
    col = get_collection()
    where = {"source": source} if source else None
    
    if where:
        all_docs = col.get(where=where, include=["metadatas", "documents"])
    else:
        all_docs = col.get(include=["metadatas", "documents"])
    
    ids = all_docs["ids"]
    metas = all_docs["metadatas"]
    docs = all_docs["documents"]
    
    unique_items = collapse_chunks(ids, metas, docs)
    total = len(unique_items)
    start = (page - 1) * page_size
    end = start + page_size
    results = unique_items[start:end]
    
    return {"total": total, "page": page, "page_size": page_size, "results": results}

@app.get("/api/documents/{doc_id:path}")
def api_document(doc_id: str):
    col = get_collection()
    result = col.get(ids=[doc_id], include=["metadatas", "documents"])
    if not result["ids"]:
        raise HTTPException(status_code=404, detail="Document not found")
    meta = result["metadatas"][0]
    return {
        "id": doc_id,
        "title": meta.get("title", doc_id),
        "source": meta.get("source", "unknown"),
        "url": meta.get("url", ""),
        "date": meta.get("date", meta.get("upload_date", "")),
        "text": result["documents"][0],
        "metadata": meta,
    }

@app.post("/api/search")
def api_search(req: SearchRequest):
    col = get_collection()
    if col.count() == 0:
        return {"results": [], "query": req.query}
    
    query_embedding = embed_query(req.query)
    where = {"source": req.source} if req.source else None
    
    # Ask Chroma for extra chunks because multiple high-scoring chunks can belong
    # to the same video/article. We collapse to unique source items below.
    raw_n_results = min(max(req.n_results * 5, req.n_results), col.count())
    results = col.query(
        query_embeddings=[query_embedding],
        n_results=raw_n_results,
        where=where,
        include=["documents", "metadatas", "distances"]
    )
    
    ids = results["ids"][0]
    metas = results["metadatas"][0]
    docs = results["documents"][0]
    distances = results["distances"][0]
    scores = [max(0.0, 1.0 - d) for d in distances]  # cosine similarity
    items = collapse_chunks(ids, metas, docs, scores)[:req.n_results]
    
    return {"results": items, "query": req.query}


def relevance_note(query: str, item: dict) -> str:
    """Short deterministic note explaining why a result is being supplied."""
    text = " ".join([
        str(item.get("title", "")),
        str(item.get("excerpt", "")),
        str(item.get("url", "")),
    ]).lower()
    terms = []
    stopwords = {"the", "and", "for", "with", "from", "this", "that", "mike", "prior", "writing", "videos", "projects", "interests"}
    for term in re.findall(r"[a-zA-Z][a-zA-Z0-9+-]{2,}", query.lower()):
        if term in stopwords:
            continue
        if term in text and term not in terms:
            terms.append(term)
    if terms:
        return "Matches query terms: " + ", ".join(terms[:6])
    source = item.get("source", "source")
    return f"Semantically related {source} item from Mike's indexed corpus."


@app.post("/api/ask-context")
def api_ask_context(req: ContextRequest):
    """Return agent-ready personal context with citations.

    This endpoint is intentionally not a chat/answer endpoint. It retrieves and
    packages relevant Second Brain items so Bruce, mike-pod, Daily Brief, or other
    agents can cite Mike's prior writing/videos/projects without pretending the
    vector store is the final source of truth.
    """
    col = get_collection()
    if col.count() == 0:
        return {"query": req.query, "results": [], "count": 0, "sources": req.sources or []}

    query_embedding = embed_query(req.query)
    requested_sources = {s for s in (req.sources or []) if s}
    where = {"source": next(iter(requested_sources))} if len(requested_sources) == 1 else None
    raw_n_results = min(max(req.limit * 8, req.limit), col.count())

    results = col.query(
        query_embeddings=[query_embedding],
        n_results=raw_n_results,
        where=where,
        include=["documents", "metadatas", "distances"]
    )

    ids = results["ids"][0]
    metas = results["metadatas"][0]
    docs = results["documents"][0]
    distances = results["distances"][0]
    scores = [max(0.0, 1.0 - d) for d in distances]
    items = collapse_chunks(ids, metas, docs, scores)
    if requested_sources:
        items = [i for i in items if i.get("source") in requested_sources]
    items = items[: max(1, min(req.limit, 20))]

    context_items = []
    for item in items:
        context_items.append({
            "title": item.get("title", ""),
            "source": item.get("source", "unknown"),
            "url": item.get("url", ""),
            "relevance": item.get("score"),
            "why_relevant": relevance_note(req.query, item),
            "excerpt": item.get("excerpt", ""),
            "chunk_count": item.get("chunk_count", 1),
            "id": item.get("id", ""),
        })

    return {
        "query": req.query,
        "count": len(context_items),
        "sources": sorted(requested_sources) if requested_sources else [],
        "results": context_items,
        "usage_note": "Use as cited context only; verify claims against linked source when precision matters.",
    }


def run_evaluation_checks() -> dict:
    """Run a small deterministic retrieval regression suite."""
    checks = []
    failures = []
    for spec in EVAL_QUERIES:
        req = ContextRequest(query=spec["query"], limit=5, sources=None)
        data = api_ask_context(req)
        results = data.get("results", [])
        sources = {r.get("source") for r in results}
        expected_sources = set(spec.get("expected_sources") or [])
        min_results = int(spec.get("min_results") or 1)
        passed = len(results) >= min_results
        notes = []
        if len(results) < min_results:
            passed = False
            notes.append(f"expected at least {min_results} results, got {len(results)}")
        if expected_sources and not (sources & expected_sources):
            passed = False
            notes.append("expected one of sources: " + ", ".join(sorted(expected_sources)))
        top = results[0] if results else {}
        check = {
            "name": spec["name"],
            "query": spec["query"],
            "passed": passed,
            "result_count": len(results),
            "sources": sorted(s for s in sources if s),
            "top_title": top.get("title"),
            "top_source": top.get("source"),
            "top_url": top.get("url"),
            "top_relevance": top.get("relevance"),
            "notes": notes,
        }
        checks.append(check)
        if not passed:
            failures.append(check)
    return {
        "generated_at": iso(datetime.now(timezone.utc)),
        "passed": len(failures) == 0,
        "check_count": len(checks),
        "failure_count": len(failures),
        "checks": checks,
        "failures": failures,
        "usage_note": "Regression checks verify retrieval shape and source coverage, not factual truth.",
    }


@app.get("/api/evaluation")
def api_evaluation():
    return run_evaluation_checks()

# ── Web UI ─────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mike's Second Brain</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f1117;--surface:#1a1d27;--surface2:#252836;--border:#2e3248;
  --text:#e2e8f0;--muted:#64748b;--accent:#818cf8;
  --blog:#6366f1;--github:#22c55e;--youtube:#ef4444;--convex:#f97316;
}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}

/* Header */
header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 24px;height:56px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.logo{font-size:18px;font-weight:600;display:flex;align-items:center;gap:8px}
.logo span{font-size:22px}
.stats-pill{background:var(--surface2);border:1px solid var(--border);border-radius:20px;padding:4px 14px;font-size:13px;color:var(--muted)}

/* Main layout */
.layout{display:flex;gap:0;max-width:1400px;margin:0 auto;padding:24px 24px 48px}
.sidebar{width:220px;flex-shrink:0;margin-right:24px}
.main{flex:1;min-width:0}

/* Search */
.search-wrap{position:relative;margin-bottom:20px}
.search-wrap input{width:100%;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 16px 12px 44px;font-size:15px;color:var(--text);outline:none;transition:.15s border-color}
.search-wrap input:focus{border-color:var(--accent)}
.search-wrap input::placeholder{color:var(--muted)}
.search-icon{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:18px;pointer-events:none}
.search-clear{position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;color:var(--muted);cursor:pointer;font-size:18px;display:none}
.search-clear.visible{display:block}

/* Source tabs */
.source-tabs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}
.tab{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer;color:var(--muted);transition:.15s all;user-select:none}
.tab:hover{border-color:var(--accent);color:var(--text)}
.tab.active{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:500}
.tab .count{opacity:.7;font-size:11px;margin-left:4px}

/* Sidebar stats */
.sidebar h3{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:12px}
.stat-row{display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border-radius:8px;margin-bottom:4px;background:var(--surface);border:1px solid var(--border)}
.stat-label{font-size:13px;display:flex;align-items:center;gap:6px}
.stat-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.stat-count{font-size:13px;font-weight:600;color:var(--text)}
.sidebar-total{margin-top:12px;padding:10px;background:var(--surface2);border-radius:8px;text-align:center;font-size:13px;color:var(--muted)}
.sidebar-total strong{color:var(--text);font-size:20px;display:block;margin-bottom:2px}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:16px}
.panel-title{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px}
.fresh-row,.eval-row{font-size:12px;color:var(--muted);line-height:1.5;margin-bottom:8px}
.fresh-row strong,.eval-row strong{color:var(--text);font-weight:600}
.warn{color:#fbbf24}.ok{color:#4ade80}.bad{color:#f87171}
.recent-list{margin:8px 0 0 16px;color:var(--muted);font-size:12px;line-height:1.5}
.recent-list a{color:var(--text)}

/* Result cards */
.results-meta{font-size:13px;color:var(--muted);margin-bottom:16px;height:20px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:12px;cursor:pointer;transition:.15s all}
.card:hover{border-color:#3e4266}
.card.expanded{border-color:var(--accent)}
.card-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.card-title{font-size:15px;font-weight:500;color:var(--text);line-height:1.4;flex:1}
.card-expand{color:var(--muted);font-size:12px;flex-shrink:0;margin-top:2px}
.card-meta{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-top:6px}
.badge{border-radius:5px;padding:2px 8px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.badge-blog{background:#6366f122;color:#818cf8}
.badge-github{background:#22c55e22;color:#22c55e}
.badge-youtube{background:#ef444422;color:#ef4444}
.badge-articles,.badge-convex{background:#f9731622;color:#f97316}
.badge-unknown{background:#94a3b822;color:#94a3b8}
.score-chip{border-radius:5px;padding:2px 7px;font-size:11px;font-weight:600}
.card-date{font-size:12px;color:var(--muted)}
.card-url{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px}
.card-excerpt{font-size:13px;color:var(--muted);line-height:1.6;margin-top:10px}
.card-body{margin-top:12px;padding-top:12px;border-top:1px solid var(--border);display:none;font-size:13px;color:var(--muted);line-height:1.7;white-space:pre-wrap;word-break:break-word}
.card.expanded .card-body{display:block}

/* Loading / empty */
.status-msg{text-align:center;padding:48px;color:var(--muted);font-size:14px}
.spinner{display:inline-block;width:20px;height:20px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite;margin-right:8px;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}

/* Pagination */
.pagination{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:20px}
.pagination button{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:6px 16px;border-radius:8px;cursor:pointer;font-size:13px}
.pagination button:hover:not(:disabled){border-color:var(--accent)}
.pagination button:disabled{opacity:.3;cursor:default}
.pagination .page-info{font-size:13px;color:var(--muted);padding:0 8px}

@media(max-width:768px){
  .layout{flex-direction:column;padding:16px}
  .sidebar{width:100%;margin-right:0;margin-bottom:16px}
  .source-tabs{overflow-x:auto;flex-wrap:nowrap;padding-bottom:4px}
}
</style>
</head>
<body>
<header>
  <div class="logo"><span>🧠</span> Second Brain</div>
  <div class="stats-pill" id="total-pill">Loading…</div>
</header>

<div class="layout">
  <aside class="sidebar">
    <h3>Sources</h3>
    <div id="sidebar-stats">
      <div class="status-msg"><span class="spinner"></span></div>
    </div>
    <div class="panel" id="freshness-panel">
      <div class="panel-title">Freshness</div>
      <div class="fresh-row"><span class="spinner"></span>Checking ingestion…</div>
    </div>
    <div class="panel" id="eval-panel">
      <div class="panel-title">Regression checks</div>
      <div class="eval-row"><span class="spinner"></span>Running evals…</div>
    </div>
  </aside>

  <div class="main">
    <div class="search-wrap">
      <span class="search-icon">🔍</span>
      <input type="text" id="search-input" placeholder="Search your second brain…" autocomplete="off">
      <button class="search-clear" id="search-clear">✕</button>
    </div>
    <div class="source-tabs" id="source-tabs">
      <div class="tab active" data-source="">All</div>
    </div>
    <div class="results-meta" id="results-meta"></div>
    <div id="results"></div>
    <div class="pagination" id="pagination" style="display:none"></div>
  </div>
</div>

<script>
const BASE = '/brain';
let currentSource = '';
let currentQuery = '';
let currentPage = 1;
const PAGE_SIZE = 20;
let searchTimer = null;

async function apiFetch(path, opts={}) {
  const r = await fetch(BASE + path, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function esc(s) {
  return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function sourceBadge(source) {
  const cls = ['blog','github','youtube','articles','convex'].includes(source) ? source : 'unknown';
  const labels = {blog:'📝 Blog',github:'💻 GitHub',youtube:'🎥 YouTube',articles:'📄 Articles',convex:'⚡ Convex'};
  return `<span class="badge badge-${cls}">${labels[source]||source}</span>`;
}

function scoreChip(score) {
  const pct = Math.round(score * 100);
  const col = score >= 0.7 ? '#4ade80' : score >= 0.4 ? '#fbbf24' : '#f87171';
  return `<span class="score-chip" style="background:${col}22;color:${col}">${pct}%</span>`;
}

function renderCard(item, isSearch) {
  const d = document.createElement('div');
  d.className = 'card';
  const dateStr = item.date ? item.date.replace('T',' ').substring(0,16) : '';
  const scoreHtml = isSearch && item.score != null ? scoreChip(item.score) : '';
  d.innerHTML = `
    <div class="card-header">
      <div style="flex:1">
        <div class="card-title">${esc(item.title)}</div>
        <div class="card-meta">
          ${sourceBadge(item.source)}
          ${scoreHtml}
          ${item.chunk_count > 1 ? `<span class="card-date">${item.chunk_count} chunks</span>` : ''}
          ${dateStr ? `<span class="card-date">${esc(dateStr)}</span>` : ''}
          ${item.url ? `<a class="card-url" href="${esc(item.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(item.url)}</a>` : ''}
        </div>
        <div class="card-excerpt">${esc((item.excerpt||'').substring(0,280))}</div>
      </div>
      <div class="card-expand">▶</div>
    </div>
    <div class="card-body"></div>`;
  
  d.querySelector('.card-header').addEventListener('click', () => {
    const expanded = d.classList.toggle('expanded');
    d.querySelector('.card-expand').textContent = expanded ? '▼' : '▶';
    if (expanded && !d.querySelector('.card-body').textContent) {
      d.querySelector('.card-body').textContent = item.excerpt || '(no content)';
    }
  });
  return d;
}

async function loadStats() {
  try {
    const stats = await apiFetch('/api/stats');
    document.getElementById('total-pill').textContent = `${stats.unique_total.toLocaleString()} items · ${stats.total.toLocaleString()} chunks`;
    
    const sources = await apiFetch('/api/sources');
    const sidebar = document.getElementById('sidebar-stats');
    const tabs = document.getElementById('source-tabs');
    
    // Clear existing tabs except "All"
    tabs.querySelectorAll('[data-source]:not([data-source=""])').forEach(t => t.remove());
    
    let sidebarHtml = '';
    for (const s of sources) {
      sidebarHtml += `
        <div class="stat-row">
          <span class="stat-label">
            <span class="stat-dot" style="background:${s.color}"></span>
            ${esc(s.source)}
          </span>
          <span class="stat-count">${s.count.toLocaleString()}</span>
        </div>`;
      
      const tab = document.createElement('div');
      tab.className = 'tab';
      tab.dataset.source = s.source;
      tab.innerHTML = `${esc(s.source)} <span class="count">${s.count}</span>`;
      tab.addEventListener('click', () => selectSource(s.source));
      tabs.appendChild(tab);
    }
    sidebarHtml += `<div class="sidebar-total"><strong>${stats.unique_total.toLocaleString()}</strong>unique items<br><span>${stats.total.toLocaleString()} chunks indexed</span></div>`;
    sidebar.innerHTML = sidebarHtml;
    
    document.querySelectorAll('.tab').forEach(t => {
      t.addEventListener('click', () => selectSource(t.dataset.source));
    });
  } catch(e) {
    console.error('stats error', e);
  }
}

function daysText(days) {
  if (days == null) return 'unknown';
  if (days === 0) return 'today';
  if (days === 1) return '1 day ago';
  return `${days} days ago`;
}

async function loadFreshness() {
  const panel = document.getElementById('freshness-panel');
  try {
    const data = await apiFetch('/api/freshness');
    const sourceRows = data.sources.map(s => `
      <div class="fresh-row"><strong>${esc(s.source)}</strong>: ${esc(daysText(s.latest_days_ago))}<br>
      <span>${s.unique_items.toLocaleString()} items · ${s.chunks.toLocaleString()} chunks${s.undated_items ? ` · ${s.undated_items} undated` : ''}</span></div>`).join('');
    const recent = data.recent_items.slice(0,5).map(i => `
      <li>${i.url ? `<a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title||'(untitled)')}</a>` : esc(i.title||'(untitled)')}<br><span>${esc(i.source)} · ${esc(daysText(i.days_ago))}</span></li>`).join('');
    const warnHtml = data.warnings.length ? `<div class="fresh-row warn">⚠ ${esc(data.warnings[0])}${data.warnings.length > 1 ? ` +${data.warnings.length-1} more` : ''}</div>` : `<div class="fresh-row ok">✓ No obvious freshness problems</div>`;
    panel.innerHTML = `<div class="panel-title">Freshness</div>
      ${warnHtml}
      <div class="fresh-row">DB touched: <strong>${esc(daysText(data.database_mtime_days_ago))}</strong></div>
      ${sourceRows}
      <div class="fresh-row"><strong>Recent items</strong></div><ol class="recent-list">${recent}</ol>`;
  } catch(e) {
    panel.innerHTML = `<div class="panel-title">Freshness</div><div class="fresh-row bad">Could not load: ${esc(e.message)}</div>`;
  }
}

async function loadEvaluation() {
  const panel = document.getElementById('eval-panel');
  try {
    const data = await apiFetch('/api/evaluation');
    const cls = data.passed ? 'ok' : 'bad';
    const status = data.passed ? `✓ ${data.check_count}/${data.check_count} passing` : `⚠ ${data.failure_count}/${data.check_count} failing`;
    const rows = data.checks.map(c => `<div class="eval-row"><span class="${c.passed ? 'ok' : 'bad'}">${c.passed ? '✓' : '×'}</span> <strong>${esc(c.name)}</strong><br><span>${esc(c.top_source||'none')} · ${esc(c.top_title||'no result')}</span></div>`).join('');
    panel.innerHTML = `<div class="panel-title">Regression checks</div><div class="eval-row ${cls}">${status}</div>${rows}`;
  } catch(e) {
    panel.innerHTML = `<div class="panel-title">Regression checks</div><div class="eval-row bad">Could not run: ${esc(e.message)}</div>`;
  }
}

function selectSource(source) {
  currentSource = source;
  currentPage = 1;
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.source === source);
  });
  refresh();
}

async function refresh() {
  const q = currentQuery.trim();
  if (q) {
    await doSearch(q);
  } else {
    await loadDocuments();
  }
}

async function doSearch(query) {
  const resultsEl = document.getElementById('results');
  const metaEl = document.getElementById('results-meta');
  const paginationEl = document.getElementById('pagination');
  
  resultsEl.innerHTML = '<div class="status-msg"><span class="spinner"></span>Searching…</div>';
  metaEl.textContent = '';
  paginationEl.style.display = 'none';
  
  try {
    const body = {query, n_results: 20};
    if (currentSource) body.source = currentSource;
    const data = await apiFetch('/api/search', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    
    resultsEl.innerHTML = '';
    if (!data.results.length) {
      resultsEl.innerHTML = '<div class="status-msg">No results found.</div>';
      metaEl.textContent = '';
      return;
    }
    metaEl.textContent = `${data.results.length} results`;
    for (const item of data.results) {
      resultsEl.appendChild(renderCard(item, true));
    }
  } catch(e) {
    resultsEl.innerHTML = `<div class="status-msg">Search error: ${esc(e.message)}</div>`;
  }
}

async function loadDocuments() {
  const resultsEl = document.getElementById('results');
  const metaEl = document.getElementById('results-meta');
  const paginationEl = document.getElementById('pagination');
  
  resultsEl.innerHTML = '<div class="status-msg"><span class="spinner"></span>Loading…</div>';
  metaEl.textContent = '';
  
  try {
    let url = `/api/documents?page=${currentPage}&page_size=${PAGE_SIZE}`;
    if (currentSource) url += `&source=${encodeURIComponent(currentSource)}`;
    const data = await apiFetch(url);
    
    resultsEl.innerHTML = '';
    if (!data.results.length) {
      resultsEl.innerHTML = '<div class="status-msg">No documents yet.</div>';
      metaEl.textContent = '';
      paginationEl.style.display = 'none';
      return;
    }
    
    const totalPages = Math.ceil(data.total / PAGE_SIZE);
    metaEl.textContent = `${data.total.toLocaleString()} items`;
    
    for (const item of data.results) {
      resultsEl.appendChild(renderCard(item, false));
    }
    
    // Pagination
    if (totalPages > 1) {
      paginationEl.style.display = 'flex';
      paginationEl.innerHTML = `
        <button id="pg-prev" ${currentPage <= 1 ? 'disabled' : ''}>← Prev</button>
        <span class="page-info">Page ${currentPage} of ${totalPages}</span>
        <button id="pg-next" ${currentPage >= totalPages ? 'disabled' : ''}>Next →</button>`;
      document.getElementById('pg-prev').addEventListener('click', () => { currentPage--; loadDocuments(); });
      document.getElementById('pg-next').addEventListener('click', () => { currentPage++; loadDocuments(); });
    } else {
      paginationEl.style.display = 'none';
    }
  } catch(e) {
    resultsEl.innerHTML = `<div class="status-msg">Error: ${esc(e.message)}</div>`;
  }
}

// Search input
const searchInput = document.getElementById('search-input');
const searchClear = document.getElementById('search-clear');

searchInput.addEventListener('input', () => {
  currentQuery = searchInput.value;
  currentPage = 1;
  searchClear.classList.toggle('visible', !!currentQuery);
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refresh, 400);
});

searchClear.addEventListener('click', () => {
  searchInput.value = '';
  currentQuery = '';
  currentPage = 1;
  searchClear.classList.remove('visible');
  refresh();
});

// Init
loadStats();
loadFreshness();
loadEvaluation();
loadDocuments();
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
@app.get("", response_class=HTMLResponse)
def web_ui():
    return HTMLResponse(HTML)

# ── Run ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("brain_server:app", host="0.0.0.0", port=PORT, reload=False)
