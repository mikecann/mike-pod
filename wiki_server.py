"""
wiki_server.py — FastAPI wiki server with JSON API + web UI.

Run with: ~/mike-pod/venv/bin/python ~/mike-pod/wiki_server.py
Serves on port 7845. Configure FastAPI root_path="/wiki" so Caddy
can proxy /wiki/* without stripping the prefix.
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# ── Config ─────────────────────────────────────────────────────────────────────
CHROMA_PATH = str(Path("/Users/bruce/mike-pod/data/chroma_db"))
OPENAI_KEY_FILE = Path("/Users/bruce/.config/openai_api_key")
COLLECTION_NAME = "knowledge"
PORT = 7845


def load_openai_key() -> str:
    return OPENAI_KEY_FILE.read_text().strip()


# ── Chroma client (single persistent instance; ChromaDB reads fresh from disk) ─
_chroma_client: Optional[chromadb.PersistentClient] = None
_api_key: Optional[str] = None


def get_collection():
    """Return collection handle. ChromaDB PersistentClient reads fresh state from
    disk on each query, so new ingests are reflected without restarting the server."""
    global _chroma_client, _api_key
    if _chroma_client is None:
        _api_key = load_openai_key()
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef = OpenAIEmbeddingFunction(api_key=_api_key, model_name="text-embedding-3-small")
    return _chroma_client.get_or_create_collection(
        name=COLLECTION_NAME, embedding_function=ef
    )


# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(title="Mike's Wiki", root_path="/wiki")


# ── API models ─────────────────────────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    n_results: int = 10


# ── Helpers ────────────────────────────────────────────────────────────────────
def meta_to_doc(meta: dict, doc_id: str, distance: Optional[float] = None) -> dict:
    result = {
        "id": meta.get("id", doc_id),
        "url": meta.get("url", ""),
        "title": meta.get("title", "(no title)"),
        "source": meta.get("source", "unknown"),
        "note": meta.get("note", ""),
        "summary": meta.get("summary", ""),
        "ingested_at": meta.get("ingested_at", ""),
    }
    if distance is not None:
        result["score"] = round(1 - distance, 4)  # cosine similarity approx
    return result


# ── API endpoints ──────────────────────────────────────────────────────────────
@app.get("/api/stats")
def api_stats():
    col = get_collection()
    count = col.count()

    last_ingested = None
    if count > 0:
        try:
            # Grab a sample to find latest ingested_at
            result = col.get(limit=count, include=["metadatas"])
            dates = [
                m.get("ingested_at", "")
                for m in result["metadatas"]
                if m.get("ingested_at")
            ]
            if dates:
                last_ingested = max(dates)
        except Exception:
            pass

    return {
        "total_documents": count,
        "last_ingested": last_ingested,
        "collection": COLLECTION_NAME,
        "chroma_path": CHROMA_PATH,
    }


@app.get("/api/documents")
def api_list_documents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    source: Optional[str] = None,
):
    col = get_collection()
    total = col.count()
    if total == 0:
        return {"total": 0, "page": page, "per_page": per_page, "documents": []}

    # Chroma doesn't support SQL-style offset/limit + filters elegantly,
    # so fetch all metadatas and paginate in Python.
    result = col.get(limit=total, include=["metadatas"])
    metas = list(zip(result["ids"], result["metadatas"]))

    if source:
        metas = [(i, m) for i, m in metas if m.get("source") == source]

    # Sort by ingested_at desc
    metas.sort(key=lambda x: x[1].get("ingested_at", ""), reverse=True)

    total_filtered = len(metas)
    start = (page - 1) * per_page
    end = start + per_page
    page_metas = metas[start:end]

    docs = [meta_to_doc(m, doc_id) for doc_id, m in page_metas]
    return {
        "total": total_filtered,
        "page": page,
        "per_page": per_page,
        "documents": docs,
    }


@app.get("/api/documents/{doc_id:path}")
def api_get_document(doc_id: str):
    col = get_collection()
    try:
        result = col.get(ids=[doc_id], include=["metadatas", "documents"])
        if not result["ids"]:
            raise HTTPException(status_code=404, detail="Document not found")
        meta = result["metadatas"][0]
        text = result["documents"][0] if result["documents"] else ""
        doc = meta_to_doc(meta, doc_id)
        doc["content"] = text
        return doc
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search")
def api_search(req: SearchRequest):
    col = get_collection()
    if col.count() == 0:
        return {"query": req.query, "results": []}

    n = min(req.n_results, col.count())
    try:
        result = col.query(
            query_texts=[req.query],
            n_results=n,
            include=["metadatas", "distances"],
        )
        docs = []
        ids = result["ids"][0]
        metas = result["metadatas"][0]
        distances = result["distances"][0]
        for doc_id, meta, dist in zip(ids, metas, distances):
            docs.append(meta_to_doc(meta, doc_id, distance=dist))
        return {"query": req.query, "results": docs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Web UI ─────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mike's Wiki</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #22263a;
    --border: #2e3347;
    --accent: #6c8af7;
    --accent2: #a78bfa;
    --text: #e2e8f0;
    --muted: #8892a4;
    --dr-badge: #1e3a5f;
    --dr-text: #60a5fa;
    --si-badge: #1e3a2f;
    --si-text: #4ade80;
    --score-high: #4ade80;
    --score-mid: #fbbf24;
    --score-low: #f87171;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace; font-size: 14px; min-height: 100vh; }

  /* Layout */
  .container { max-width: 1100px; margin: 0 auto; padding: 24px 16px; }
  header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 16px; }
  header h1 { font-size: 1.4rem; color: var(--accent); letter-spacing: -0.5px; }
  header .subtitle { color: var(--muted); font-size: 0.8rem; }
  .stats-bar { display: flex; gap: 24px; margin-bottom: 24px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; }
  .stat { display: flex; flex-direction: column; gap: 2px; }
  .stat-label { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .stat-value { color: var(--accent); font-size: 1.1rem; font-weight: bold; }

  /* Search */
  .search-box { display: flex; gap: 8px; margin-bottom: 24px; }
  .search-box input { flex: 1; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 10px 14px; color: var(--text); font-family: inherit; font-size: 14px; outline: none; transition: border-color 0.2s; }
  .search-box input:focus { border-color: var(--accent); }
  .search-box input::placeholder { color: var(--muted); }
  .search-box button { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 10px 20px; cursor: pointer; font-family: inherit; font-size: 14px; transition: opacity 0.2s; }
  .search-box button:hover { opacity: 0.85; }
  .search-box button:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Filter tabs */
  .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
  .tab { background: var(--surface); border: 1px solid var(--border); border-radius: 20px; padding: 4px 14px; cursor: pointer; font-size: 0.8rem; color: var(--muted); transition: all 0.2s; }
  .tab.active { background: var(--accent); border-color: var(--accent); color: #fff; }

  /* Document list */
  .doc-list { display: flex; flex-direction: column; gap: 8px; }
  .doc-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; transition: border-color 0.2s; cursor: pointer; }
  .doc-card:hover { border-color: var(--accent); }
  .doc-card.expanded { border-color: var(--accent2); }
  .doc-header { display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; }
  .doc-info { flex: 1; min-width: 0; }
  .doc-title { font-size: 0.9rem; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 4px; }
  .doc-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; font-weight: 600; letter-spacing: 0.03em; }
  .badge.deep_research { background: var(--dr-badge); color: var(--dr-text); }
  .badge.stashit { background: var(--si-badge); color: var(--si-text); }
  .doc-date { font-size: 0.75rem; color: var(--muted); }
  .doc-url { font-size: 0.75rem; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 300px; }
  .score-chip { font-size: 0.75rem; padding: 2px 8px; border-radius: 10px; font-weight: bold; }
  .doc-summary { font-size: 0.8rem; color: var(--muted); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .doc-expand { color: var(--muted); font-size: 1rem; flex-shrink: 0; padding: 2px; }

  /* Expanded body */
  .doc-body { border-top: 1px solid var(--border); padding: 14px; display: none; }
  .doc-card.expanded .doc-body { display: block; }
  .doc-body section { margin-bottom: 14px; }
  .doc-body section:last-child { margin-bottom: 0; }
  .section-label { font-size: 0.7rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 4px; }
  .section-text { font-size: 0.82rem; color: var(--text); line-height: 1.6; }
  .section-text a { color: var(--accent); text-decoration: none; }
  .section-text a:hover { text-decoration: underline; }
  .tag-list { display: flex; flex-wrap: wrap; gap: 6px; }
  .tag { background: var(--surface2); border: 1px solid var(--border); border-radius: 4px; padding: 3px 8px; font-size: 0.75rem; color: var(--text); }

  /* Pagination */
  .pagination { display: flex; align-items: center; justify-content: center; gap: 8px; margin-top: 20px; }
  .page-btn { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 6px 14px; cursor: pointer; color: var(--text); font-family: inherit; font-size: 13px; transition: all 0.2s; }
  .page-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .page-info { color: var(--muted); font-size: 0.8rem; }

  /* Status / empty */
  .status { text-align: center; padding: 40px; color: var(--muted); font-size: 0.85rem; }
  .spinner { display: inline-block; width: 18px; height: 18px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.7s linear infinite; vertical-align: middle; margin-right: 8px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .mode-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 0.8rem; color: var(--muted); }
  .mode-bar strong { color: var(--accent); }
  .clear-btn { background: none; border: 1px solid var(--border); border-radius: 4px; color: var(--muted); cursor: pointer; font-size: 0.75rem; padding: 2px 8px; font-family: inherit; }
  .clear-btn:hover { border-color: var(--accent); color: var(--accent); }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>⚡ Mike's Wiki</h1>
    <span class="subtitle">personal knowledge base</span>
  </header>

  <div class="stats-bar" id="stats-bar">
    <div class="stat"><span class="stat-label">Documents</span><span class="stat-value" id="stat-count">—</span></div>
    <div class="stat"><span class="stat-label">Last Ingested</span><span class="stat-value" id="stat-date">—</span></div>
    <div class="stat"><span class="stat-label">Collection</span><span class="stat-value" id="stat-col">knowledge</span></div>
  </div>

  <div class="search-box">
    <input type="text" id="search-input" placeholder="Semantic search… e.g. 'AI infrastructure costs'" />
    <button id="search-btn">Search</button>
  </div>

  <div class="tabs">
    <div class="tab active" data-source="">All</div>
    <div class="tab" data-source="deep_research">Deep Research</div>
    <div class="tab" data-source="stashit">StashIt</div>
  </div>

  <div id="mode-bar" class="mode-bar" style="display:none"></div>
  <div class="doc-list" id="doc-list"></div>
  <div class="pagination" id="pagination"></div>
</div>

<script>
const BASE = window.location.pathname.replace(/\/(api.*|)$/, '').replace(/\/$/, '') || '';
let state = { page: 1, perPage: 20, source: '', mode: 'browse', query: '', searchResults: [] };

// ── Fetch helpers ─────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const res = await fetch(BASE + path, opts);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Stats ─────────────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const s = await apiFetch('/api/stats');
    document.getElementById('stat-count').textContent = s.total_documents.toLocaleString();
    const d = s.last_ingested ? new Date(s.last_ingested) : null;
    document.getElementById('stat-date').textContent = d
      ? d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' })
      : '—';
    document.getElementById('stat-col').textContent = s.collection || 'knowledge';
  } catch(e) { console.error(e); }
}

// ── Score chip ────────────────────────────────────────────────────────────────
function scoreChip(score) {
  if (score == null) return '';
  const color = score >= 0.7 ? '#4ade80' : score >= 0.4 ? '#fbbf24' : '#f87171';
  return `<span class="score-chip" style="background:${color}22;color:${color}">${(score*100).toFixed(0)}%</span>`;
}

// ── Render doc card ───────────────────────────────────────────────────────────
function renderCard(doc) {
  const d = document.createElement('div');
  d.className = 'doc-card';
  d.dataset.id = doc.id;
  const dateStr = doc.ingested_at
    ? new Date(doc.ingested_at).toLocaleDateString('en-AU', { day:'numeric', month:'short', year:'numeric' })
    : '';
  const scoreHtml = doc.score != null ? scoreChip(doc.score) : '';
  d.innerHTML = `
    <div class="doc-header">
      <div class="doc-info">
        <div class="doc-title" title="${escHtml(doc.title)}">${escHtml(doc.title)}</div>
        <div class="doc-meta">
          <span class="badge ${doc.source}">${doc.source === 'deep_research' ? '🔬 deep research' : '📌 stashit'}</span>
          ${scoreHtml}
          ${dateStr ? `<span class="doc-date">${dateStr}</span>` : ''}
          ${doc.url ? `<span class="doc-url" title="${escHtml(doc.url)}"><a href="${escHtml(doc.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escHtml(doc.url)}</a></span>` : ''}
        </div>
        ${doc.summary ? `<div class="doc-summary">${escHtml(doc.summary)}</div>` : ''}
      </div>
      <span class="doc-expand">▶</span>
    </div>
    <div class="doc-body">
      <div class="status"><span class="spinner"></span>Loading…</div>
    </div>`;

  d.querySelector('.doc-header').addEventListener('click', () => toggleDoc(d, doc.id));
  return d;
}

function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function toggleDoc(card, id) {
  const expanded = card.classList.toggle('expanded');
  card.querySelector('.doc-expand').textContent = expanded ? '▼' : '▶';
  if (!expanded) return;
  const body = card.querySelector('.doc-body');
  try {
    const doc = await apiFetch(`/api/documents/${encodeURIComponent(id)}`);
    body.innerHTML = renderDocBody(doc);
  } catch(e) {
    body.innerHTML = `<div class="status">Error loading document: ${e.message}</div>`;
  }
}

function renderDocBody(doc) {
  const sections = [];
  if (doc.note) {
    sections.push(`<section><div class="section-label">Mike's Note</div><div class="section-text">${escHtml(doc.note)}</div></section>`);
  }
  if (doc.summary) {
    sections.push(`<section><div class="section-label">Summary</div><div class="section-text">${escHtml(doc.summary)}</div></section>`);
  }
  if (doc.content) {
    // Parse content back into structured parts
    const lines = doc.content.split('\n').filter(l => l.trim());
    const insights = lines.filter(l => l.startsWith('Key insights:')).map(l => l.replace('Key insights:', '').trim()).join('');
    const answer = lines.filter(l => l.startsWith('Answer:')).map(l => l.replace('Answer:', '').trim()).join('');
    if (insights) {
      const insightList = insights.split(' | ').filter(Boolean);
      if (insightList.length > 1) {
        sections.push(`<section><div class="section-label">Key Insights</div><div class="tag-list">${insightList.map(i=>`<span class="tag">${escHtml(i)}</span>`).join('')}</div></section>`);
      } else if (insights) {
        sections.push(`<section><div class="section-label">Key Insights</div><div class="section-text">${escHtml(insights)}</div></section>`);
      }
    }
    if (answer) {
      sections.push(`<section><div class="section-label">Answer to Mike</div><div class="section-text">${escHtml(answer)}</div></section>`);
    }
  }
  if (doc.url) {
    sections.push(`<section><div class="section-label">Source</div><div class="section-text"><a href="${escHtml(doc.url)}" target="_blank" rel="noopener">${escHtml(doc.url)}</a></div></section>`);
  }
  return sections.length ? sections.join('') : '<div class="status">No additional details.</div>';
}

// ── Browse mode ───────────────────────────────────────────────────────────────
async function loadBrowse() {
  const list = document.getElementById('doc-list');
  const modeBar = document.getElementById('mode-bar');
  list.innerHTML = '<div class="status"><span class="spinner"></span>Loading…</div>';
  modeBar.style.display = 'none';

  const params = new URLSearchParams({ page: state.page, per_page: state.perPage });
  if (state.source) params.set('source', state.source);
  try {
    const data = await apiFetch(`/api/documents?${params}`);
    list.innerHTML = '';
    if (!data.documents.length) {
      list.innerHTML = '<div class="status">No documents yet. Run wiki_ingest.py to populate.</div>';
      renderPagination(0, 1);
      return;
    }
    data.documents.forEach(doc => list.appendChild(renderCard(doc)));
    renderPagination(data.total, data.page);
  } catch(e) {
    list.innerHTML = `<div class="status">Error: ${e.message}</div>`;
  }
}

// ── Search mode ───────────────────────────────────────────────────────────────
async function doSearch() {
  const q = document.getElementById('search-input').value.trim();
  if (!q) return;
  state.mode = 'search';
  state.query = q;
  const list = document.getElementById('doc-list');
  const modeBar = document.getElementById('mode-bar');
  list.innerHTML = '<div class="status"><span class="spinner"></span>Searching…</div>';
  document.getElementById('pagination').innerHTML = '';

  try {
    const data = await apiFetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q, n_results: 20 }),
    });
    state.searchResults = data.results;
    list.innerHTML = '';
    modeBar.style.display = 'flex';
    modeBar.innerHTML = `<strong>${data.results.length}</strong> results for "<em>${escHtml(q)}</em>" &nbsp;<button class="clear-btn" onclick="clearSearch()">✕ Clear</button>`;
    if (!data.results.length) {
      list.innerHTML = '<div class="status">No results found.</div>';
      return;
    }
    data.results.forEach(doc => list.appendChild(renderCard(doc)));
  } catch(e) {
    list.innerHTML = `<div class="status">Search error: ${e.message}</div>`;
    modeBar.style.display = 'none';
  }
}

function clearSearch() {
  state.mode = 'browse';
  state.query = '';
  document.getElementById('search-input').value = '';
  document.getElementById('mode-bar').style.display = 'none';
  loadBrowse();
}

// ── Pagination ─────────────────────────────────────────────────────────────────
function renderPagination(total, currentPage) {
  const el = document.getElementById('pagination');
  const totalPages = Math.ceil(total / state.perPage);
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <button class="page-btn" onclick="goPage(${currentPage-1})" ${currentPage<=1?'disabled':''}>← Prev</button>
    <span class="page-info">Page ${currentPage} / ${totalPages} &nbsp;(${total} docs)</span>
    <button class="page-btn" onclick="goPage(${currentPage+1})" ${currentPage>=totalPages?'disabled':''}>Next →</button>`;
}

function goPage(p) { state.page = p; loadBrowse(); window.scrollTo(0,0); }

// ── Event wiring ───────────────────────────────────────────────────────────────
document.getElementById('search-btn').addEventListener('click', doSearch);
document.getElementById('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    state.source = tab.dataset.source;
    state.page = 1;
    state.mode = 'browse';
    document.getElementById('search-input').value = '';
    document.getElementById('mode-bar').style.display = 'none';
    loadBrowse();
  });
});

// ── Init ───────────────────────────────────────────────────────────────────────
loadStats();
loadBrowse();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    return HTMLResponse(content=HTML)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "wiki_server:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
