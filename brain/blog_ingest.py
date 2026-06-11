#!/usr/bin/env python3
"""
Ingest all posts from mikecann.blog into the second brain.
The blog is a Next.js app with RSS at /rss.xml (628+ posts back to 2003).
Content is in .markdown-content div.
"""
import sys
import os
import re
import time
sys.path.insert(0, os.path.dirname(__file__))

import requests
from bs4 import BeautifulSoup
from brain_embed import upsert_doc

BASE_URL = "https://mikecann.blog"
RSS_URL = "https://mikecann.blog/rss.xml"
CHUNK_SIZE = 500  # words per chunk


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def fetch_all_urls_from_rss(session) -> list:
    """Fetch all post URLs from the RSS feed."""
    print("Fetching RSS feed from /rss.xml...")
    resp = session.get(RSS_URL, timeout=30)
    soup = BeautifulSoup(resp.text, "xml")
    
    items = []
    for item in soup.find_all("item"):
        link = item.find("link")
        title = item.find("title")
        pub_date = item.find("pubDate")
        
        url = ""
        if link:
            url = link.text.strip() if link.text else ""
        if not url and item.find("guid"):
            url = item.find("guid").text.strip()
        
        if url and "mikecann.blog" in url:
            items.append({
                "url": url,
                "title": title.text.strip() if title else "",
                "date": pub_date.text.strip() if pub_date else "",
            })
    
    print(f"Found {len(items)} posts in RSS feed")
    return items


def fetch_post_content(session, url: str) -> str | None:
    """Fetch post page and extract content via .markdown-content div."""
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Primary: .markdown-content
        mc = soup.select_one(".markdown-content")
        if mc:
            return mc.get_text(separator=" ", strip=True)
        
        # Fallback: article, main, .content, .post-content
        for sel in ["article", "main", ".content", ".post-content", ".entry-content"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator=" ", strip=True)
                if len(text.split()) > 30:
                    return text
        
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def main():
    print("=== Blog Ingest ===")
    
    session = requests.Session()
    session.headers["User-Agent"] = "MikeBrain/1.0 (personal indexer)"
    
    posts = fetch_all_urls_from_rss(session)
    
    if not posts:
        print("ERROR: No posts found in RSS feed!")
        return
    
    added = 0
    skipped = 0
    errors = 0
    total_chunks = 0
    
    for i, post_meta in enumerate(posts):
        url = post_meta["url"]
        title = post_meta["title"]
        date = post_meta["date"]
        slug = slug_from_url(url)
        
        print(f"[{i+1}/{len(posts)}] {title[:60]}", end="", flush=True)
        
        # Check if already indexed (check first chunk)
        from brain_embed import get_collection
        col = get_collection()
        existing = col.get(ids=[f"blog_{slug}_chunk0"])
        if existing["ids"]:
            print(" already indexed")
            skipped += 1
            continue
        
        content = fetch_post_content(session, url)
        if not content or len(content.split()) < 20:
            print(" SKIP (no content)")
            errors += 1
            time.sleep(0.1)
            continue
        
        chunks = chunk_text(content)
        
        post_added = 0
        for ci, chunk in enumerate(chunks):
            doc_id = f"blog_{slug}_chunk{ci}"
            metadata = {
                "source": "blog",
                "url": url,
                "title": title[:200],
                "date": date[:50] if date else "",
                "chunk_index": ci,
                "total_chunks": len(chunks),
            }
            if upsert_doc(doc_id, chunk, metadata):
                post_added += 1
                total_chunks += 1
        
        if post_added > 0:
            added += 1
            print(f" +{post_added} chunks")
        else:
            skipped += 1
            print(" already indexed")
        
        time.sleep(0.15)  # polite crawling
    
    print(f"\n=== Blog Ingest Complete ===")
    print(f"Posts in feed: {len(posts)}")
    print(f"New posts added: {added}")
    print(f"Skipped (already indexed): {skipped}")
    print(f"Errors/skipped: {errors}")
    print(f"Total chunks added: {total_chunks}")


if __name__ == "__main__":
    main()
