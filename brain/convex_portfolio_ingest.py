#!/usr/bin/env python3
"""
Convex Portfolio ingest pipeline:
Fetches Mike's curated YouTube videos and Stack articles from the Convex
portfolio Convex deployment and ingests them into the second brain ChromaDB.

- Videos: processed via the existing youtube_ingest.py pipeline (Whisper, OCR, vision)
- Articles: fetched from stack.convex.dev, chunked, and embedded

Idempotent: skips already-indexed items.
"""
import sys
import os
import re
import json
import time
import subprocess
import requests
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(__file__))

# Ensure Homebrew binaries (ffmpeg, yt-dlp, tesseract) are on PATH for subprocesses
_HOMEBREW_BIN = "/opt/homebrew/bin"
if _HOMEBREW_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _HOMEBREW_BIN + ":" + os.environ.get("PATH", "")

from brain_embed import upsert_doc, get_collection
from youtube_ingest import process_video

CONVEX_DEPLOYMENT = "brave-oyster-758"
NODE_BIN = "/Users/bruce/.nvm/versions/node/v24.14.1/bin"
PORTFOLIO_DIR = Path.home() / "mikes-convex-portfolio"
CHUNK_SIZE = 500  # words per chunk for articles


# ---------------------------------------------------------------------------
# Convex CLI helpers
# ---------------------------------------------------------------------------

def run_convex(function_name: str) -> list | dict | None:
    """Run a Convex query via the CLI and return parsed JSON output.

    Uses a temp file to avoid pipe truncation of large outputs.
    """
    import tempfile
    env = os.environ.copy()
    env["PATH"] = NODE_BIN + ":" + env.get("PATH", "")
    env["CONVEX_DEPLOYMENT"] = CONVEX_DEPLOYMENT

    # Write output to a temp file to avoid subprocess pipe buffer truncation
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        tmp_path = tf.name

    try:
        with open(tmp_path, "w") as out_f:
            result = subprocess.run(
                ["npx", "convex", "run", function_name, "--prod"],
                cwd=str(PORTFOLIO_DIR),
                stdout=out_f,
                stderr=subprocess.PIPE,
                timeout=60,
                env=env,
            )
        if result.returncode != 0:
            print(f"  ERROR running convex {function_name}: {result.stderr.decode()[:300]}")
            return None
        with open(tmp_path) as f:
            raw = f.read()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ERROR parsing convex output for {function_name}: {e}")
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Article helpers
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def fetch_article_content(session: requests.Session, url: str) -> str | None:
    """Fetch article text from a stack.convex.dev article page."""
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code} for {url}")
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # Stack articles: try common content selectors
        for selector in [
            "article",
            ".prose",
            ".markdown-content",
            "main",
            ".content",
        ]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(separator=" ", strip=True)
                if len(text.split()) > 30:
                    return text

        # Fallback: body text
        body = soup.find("body")
        if body:
            return body.get_text(separator=" ", strip=True)

        return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None


def is_video_indexed(video_id: str) -> bool:
    """Check if any chunk for this video_id already exists in ChromaDB."""
    col = get_collection()
    # youtube chunks use IDs like youtube_{video_id}_{window_start}
    # We check for the first window (0)
    existing = col.get(ids=[f"youtube_{video_id}_0"])
    return bool(existing["ids"])


def is_article_indexed(slug: str) -> bool:
    """Check if the first chunk of this article already exists in ChromaDB."""
    col = get_collection()
    existing = col.get(ids=[f"convex_article_{slug}_chunk0"])
    return bool(existing["ids"])


def slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def ingest_videos(videos: list) -> dict:
    """Process each video through the full youtube pipeline."""
    added = 0
    skipped = 0
    errors = 0

    print(f"\n=== Videos ({len(videos)} found) ===")
    for i, video in enumerate(videos):
        video_id = video.get("youtubeId", "")
        title = video.get("title", "")
        url = f"https://www.youtube.com/watch?v={video_id}"

        print(f"\n[{i+1}/{len(videos)}] {title[:70]}")
        print(f"  YouTube ID: {video_id}")

        if not video_id:
            print("  SKIP: no youtubeId")
            errors += 1
            continue

        if is_video_indexed(video_id):
            print("  SKIP: already indexed")
            skipped += 1
            continue

        try:
            result = process_video(
                youtube_url=url,
                video_id=video_id,
                title=title,
                channel="Mike Cann (Convex)",
            )
            if "error" in result:
                print(f"  ERROR: {result['error']}")
                errors += 1
            else:
                chunks = result.get("chunks_added", 0)
                print(f"  OK: {chunks} chunks added")
                added += 1
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            errors += 1

    return {"found": len(videos), "added": added, "skipped": skipped, "errors": errors}


def ingest_articles(articles: list) -> dict:
    """Fetch and embed each Stack article."""
    added = 0
    skipped = 0
    errors = 0

    print(f"\n=== Articles ({len(articles)} found) ===")
    session = requests.Session()
    session.headers["User-Agent"] = "MikeBrain/1.0 (personal indexer)"

    for i, article in enumerate(articles):
        url = article.get("url", "")
        title = article.get("title", "")
        slug = article.get("slug", "") or slug_from_url(url)
        published_at = article.get("publishedAt", "")

        print(f"\n[{i+1}/{len(articles)}] {title[:70]}")
        print(f"  URL: {url}")

        if not url:
            print("  SKIP: no URL")
            errors += 1
            continue

        if is_article_indexed(slug):
            print("  SKIP: already indexed")
            skipped += 1
            continue

        content = fetch_article_content(session, url)
        if not content or len(content.split()) < 20:
            print("  SKIP: no/minimal content")
            errors += 1
            time.sleep(0.2)
            continue

        chunks = chunk_text(content)
        chunks_added = 0

        for ci, chunk in enumerate(chunks):
            doc_id = f"convex_article_{slug}_chunk{ci}"
            # Prepend title context to each chunk so retrieval is aware
            chunk_text_full = f"Article: {title}\nAuthor: Mike Cann\nURL: {url}\n\n{chunk}"
            metadata = {
                "source": "articles",
                "url": url,
                "title": title[:200],
                "author": "Mike Cann",
                "slug": slug,
                "published_at": published_at[:50] if published_at else "",
                "chunk_index": ci,
                "total_chunks": len(chunks),
            }
            if upsert_doc(doc_id, chunk_text_full, metadata):
                chunks_added += 1

        if chunks_added > 0:
            added += 1
            print(f"  OK: {chunks_added} chunks added")
        else:
            skipped += 1
            print("  already indexed (all chunks existed)")

        time.sleep(0.15)  # polite crawling

    return {"found": len(articles), "added": added, "skipped": skipped, "errors": errors}


def main():
    print("=== Convex Portfolio Ingest ===")
    print(f"Deployment: {CONVEX_DEPLOYMENT}")

    # --- Fetch from Convex ---
    print("\nFetching videos from Convex...")
    videos = run_convex("videos:list")
    if videos is None:
        print("ERROR: could not fetch videos. Aborting.")
        sys.exit(1)

    print(f"Fetching articles from Convex...")
    articles = run_convex("articles:list")
    if articles is None:
        print("ERROR: could not fetch articles. Aborting.")
        sys.exit(1)

    # videos:list already returns only "mine" videos (isMikes == "mine")
    print(f"Found {len(videos)} videos (all Mike's), {len(articles)} articles")

    # --- Ingest ---
    video_stats = ingest_videos(videos)
    article_stats = ingest_articles(articles)

    # --- Summary ---
    print("\n" + "=" * 50)
    print("=== Convex Portfolio Ingest Complete ===")
    print(f"Videos:   {video_stats['found']} found, "
          f"{video_stats['added']} added, "
          f"{video_stats['skipped']} skipped, "
          f"{video_stats['errors']} errors")
    print(f"Articles: {article_stats['found']} found, "
          f"{article_stats['added']} added, "
          f"{article_stats['skipped']} skipped, "
          f"{article_stats['errors']} errors")

    # Final stats
    from brain_embed import get_stats
    stats = get_stats()
    print(f"\nChromaDB total docs: {stats['total']}")
    print("By source:")
    for src, count in sorted(stats["by_source"].items()):
        print(f"  {src}: {count}")


if __name__ == "__main__":
    main()
