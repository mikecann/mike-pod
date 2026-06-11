import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from brain_config import CHROMA_PATH, COLLECTION_NAME, OLLAMA_EMBED_MODEL
import ollama
import chromadb

# nomic-embed-text context window is 8192 tokens ~ ~6000 words / ~32k chars
# Stay safe with 1500 words (~8000 chars)
MAX_EMBED_WORDS = 1500


def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )


def truncate_for_embed(text: str) -> str:
    """Truncate text to fit within nomic-embed-text context window."""
    words = text.split()
    if len(words) > MAX_EMBED_WORDS:
        return " ".join(words[:MAX_EMBED_WORDS])
    return text


def embed_text(text: str) -> list:
    safe_text = truncate_for_embed(text)
    resp = ollama.embeddings(model=OLLAMA_EMBED_MODEL, prompt=safe_text)
    return resp["embedding"]


def upsert_doc(id: str, text: str, metadata: dict) -> bool:
    """Returns True if newly added, False if already existed."""
    col = get_collection()
    existing = col.get(ids=[id])
    if existing["ids"]:
        return False  # already indexed
    embedding = embed_text(text)
    col.add(
        ids=[id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[metadata]
    )
    return True


def search(query: str, n_results: int = 10, source_filter: str = None) -> dict:
    col = get_collection()
    query_embedding = embed_text(query)
    where = {"source": source_filter} if source_filter else None
    results = col.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where,
        include=["documents", "metadatas", "distances"]
    )
    return results


def get_stats() -> dict:
    col = get_collection()
    total = col.count()
    results = col.get(include=["metadatas"])
    source_counts = {}
    for meta in results["metadatas"]:
        src = meta.get("source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
    return {"total": total, "by_source": source_counts}
