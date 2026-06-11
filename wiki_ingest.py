"""
wiki_ingest.py — Ingest deep_research files and StashIt items into ChromaDB.

Run with: ~/mike-pod/venv/bin/python ~/mike-pod/wiki_ingest.py
         ~/mike-pod/venv/bin/python ~/mike-pod/wiki_ingest.py --all

Flags:
  --all   Fetch ALL archived StashIt items (since=0) instead of the default
          7-day rolling window.

Idempotent: skips docs already present in the DB (checked by ID).
"""

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

# ── Paths & config ────────────────────────────────────────────────────────────
BASE_DIR = Path("/Users/bruce/mike-pod")
DEEP_RESEARCH_DIR = BASE_DIR / "data" / "deep_research"
CHROMA_PATH = str(BASE_DIR / "data" / "chroma_db")
OPENAI_KEY_FILE = Path("/Users/bruce/.config/openai_api_key")
NODE_BIN = "/Users/bruce/.nvm/versions/node/v24.14.1/bin"
NPXPATH = f"{NODE_BIN}/npx"
CONVEX_CWD = "/Users/bruce/stashit/packages/convex"
CONVEX_URL = "https://festive-sparrow-314.convex.cloud"
COLLECTION_NAME = "knowledge"


def load_openai_key() -> str:
    key = OPENAI_KEY_FILE.read_text().strip()
    if not key:
        raise ValueError(f"OpenAI key empty at {OPENAI_KEY_FILE}")
    return key


def get_chroma_collection(api_key: str):
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef = OpenAIEmbeddingFunction(api_key=api_key, model_name="text-embedding-3-small")
    return client.get_or_create_collection(name=COLLECTION_NAME, embedding_function=ef)


def build_document_text(doc: dict) -> str:
    """Concatenate the meaty fields into a single string for embedding."""
    parts = []
    if doc.get("title"):
        parts.append(f"Title: {doc['title']}")
    if doc.get("note"):
        parts.append(f"Note: {doc['note']}")
    if doc.get("summary"):
        parts.append(f"Summary: {doc['summary']}")
    if doc.get("key_insights"):
        insights = doc["key_insights"]
        if isinstance(insights, list):
            insights = " | ".join(insights)
        parts.append(f"Key insights: {insights}")
    if doc.get("answer_to_mike"):
        parts.append(f"Answer: {doc['answer_to_mike']}")
    return "\n".join(parts)


# ── Deep-research loader ───────────────────────────────────────────────────────
def load_deep_research_docs() -> list[dict]:
    docs = []
    if not DEEP_RESEARCH_DIR.exists():
        print(f"  [deep_research] directory not found: {DEEP_RESEARCH_DIR}")
        return docs

    for json_file in sorted(DEEP_RESEARCH_DIR.glob("*.json")):
        try:
            raw = json.loads(json_file.read_text())
        except Exception as e:
            print(f"  [deep_research] skipping {json_file.name}: {e}")
            continue

        item_id = raw.get("item_id") or json_file.stem
        analysis = raw.get("analysis") or {}

        doc = {
            "id": f"dr_{item_id}",
            "url": raw.get("url", ""),
            "title": raw.get("title", ""),
            "note": raw.get("mike_note", ""),
            "summary": analysis.get("one_sentence_summary", ""),
            "key_insights": analysis.get("key_insights", []),
            "answer_to_mike": analysis.get("answer_to_mike", ""),
            "source": "deep_research",
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        docs.append(doc)

    return docs


# ── StashIt loader ─────────────────────────────────────────────────────────────
def _fetch_stashit_items(since: int | None = None) -> list[dict]:
    """
    Core fetcher for StashIt archived items via Convex CLI.

    Args:
        since: Unix timestamp in milliseconds.  Pass 0 (or any epoch value)
               to retrieve ALL archived items ever saved.  Pass None to use
               the server-side default (7-day rolling window).
    """
    try:
        env = {**os.environ, "PATH": NODE_BIN + ":" + os.environ.get("PATH", "")}

        cmd = [NPXPATH, "convex", "run", "podcastFeed:getRecentReads"]
        if since is not None:
            cmd.append(json.dumps({"since": since}))
        cmd += ["--prod", "--url", CONVEX_URL]

        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=120,
            cwd=CONVEX_CWD,
            env=env,
        )
        if result.returncode != 0:
            print(f"  [stashit] Convex CLI error: {result.stderr[:300]}")
            return []

        items = json.loads(result.stdout)
        docs = []
        for item in items:
            item_id = item.get("_id") or item.get("id") or item.get("url", "")
            note_parts = item.get("notes", [])
            note_text = " | ".join(note_parts) if isinstance(note_parts, list) else str(note_parts)

            doc = {
                "id": f"si_{item_id}",
                "url": item.get("url", ""),
                "title": item.get("title") or item.get("url", ""),
                "note": note_text,
                "summary": (item.get("description") or item.get("summary") or "")[:500],
                "key_insights": [],
                "answer_to_mike": "",
                "source": "stashit",
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }
            docs.append(doc)
        return docs

    except subprocess.TimeoutExpired:
        print("  [stashit] Convex CLI timed out")
        return []
    except Exception as e:
        print(f"  [stashit] fetch failed: {e}")
        return []


def load_stashit_docs() -> list[dict]:
    """Fetch recent (last 7 days) archived StashIt items — for incremental runs."""
    return _fetch_stashit_items(since=None)


def fetch_all_stashit_items() -> list[dict]:
    """Fetch ALL archived StashIt items ever saved (no time filter)."""
    return _fetch_stashit_items(since=0)


# ── Main ingest ────────────────────────────────────────────────────────────────
def ingest(docs: list[dict], collection) -> tuple[int, int]:
    """Upsert docs into ChromaDB. Returns (new_count, skipped_count)."""
    if not docs:
        return 0, 0

    existing_ids = set()
    try:
        # Fetch all IDs in batches (Chroma may limit large gets)
        all_ids = collection.get(include=[])["ids"]
        existing_ids = set(all_ids)
    except Exception:
        pass

    new_docs = [d for d in docs if d["id"] not in existing_ids]
    skipped = len(docs) - len(new_docs)

    if not new_docs:
        return 0, skipped

    # Batch in groups of 50 to avoid payload limits
    BATCH = 50
    for i in range(0, len(new_docs), BATCH):
        batch = new_docs[i : i + BATCH]
        ids = [d["id"] for d in batch]
        documents = [build_document_text(d) for d in batch]
        metadatas = [
            {
                "id": d["id"],
                "url": d["url"],
                "title": d["title"],
                "note": d["note"][:500],
                "summary": d["summary"][:500],
                "source": d["source"],
                "ingested_at": d["ingested_at"],
            }
            for d in batch
        ]
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        print(f"    added {len(batch)} docs (batch {i//BATCH + 1})")

    return len(new_docs), skipped


def run(all_items: bool = False):
    mode = "ALL items (full history)" if all_items else "recent items (7-day window)"
    print(f"=== Wiki Ingest [{mode}] ===")
    api_key = load_openai_key()
    collection = get_chroma_collection(api_key)

    # Record start count
    start_count = collection.count()
    print(f"  DB currently has {start_count} documents")

    # Load sources
    print("\nLoading deep_research docs...")
    dr_docs = load_deep_research_docs()
    print(f"  Found {len(dr_docs)} deep_research files")

    print("\nLoading StashIt docs...")
    if all_items:
        si_docs = fetch_all_stashit_items()
    else:
        si_docs = load_stashit_docs()
    print(f"  Found {len(si_docs)} StashIt items")

    all_docs = dr_docs + si_docs
    print(f"\nTotal to process: {len(all_docs)}")

    # Ingest
    print("\nIngesting...")
    new_count, skipped = ingest(all_docs, collection)

    end_count = collection.count()
    print(f"\n=== Done ===")
    print(f"  New docs added : {new_count}")
    print(f"  Skipped (exist): {skipped}")
    print(f"  Total in DB    : {end_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest docs into the wiki ChromaDB.")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_items",
        help="Fetch ALL archived StashIt items (full history) instead of the 7-day default.",
    )
    args = parser.parse_args()
    run(all_items=args.all_items)
