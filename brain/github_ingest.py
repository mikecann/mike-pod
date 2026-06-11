#!/usr/bin/env python3
"""
Ingest Mike's public GitHub repos (READMEs + metadata) into the second brain.
Large READMEs are chunked to stay within embedding context limits.
"""
import sys
import os
import base64
import time
sys.path.insert(0, os.path.dirname(__file__))

import requests
from brain_embed import upsert_doc

GITHUB_USER = "mikecann"
GITHUB_API = "https://api.github.com"
CHUNK_SIZE = 400  # words per chunk


def get_repos(session) -> list:
    repos = []
    page = 1
    while True:
        resp = session.get(
            f"{GITHUB_API}/users/{GITHUB_USER}/repos",
            params={"per_page": 100, "page": page, "type": "public"},
            timeout=30
        )
        data = resp.json()
        if not isinstance(data, list) or len(data) == 0:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.3)
    return repos


def get_readme(session, repo_name: str) -> str | None:
    resp = session.get(
        f"{GITHUB_API}/repos/{GITHUB_USER}/{repo_name}/readme",
        timeout=30
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    content = data.get("content", "")
    if not content:
        return None
    try:
        return base64.b64decode(content).decode("utf-8", errors="replace")
    except Exception:
        return None


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def main():
    print("=== GitHub Ingest ===")
    
    session = requests.Session()
    session.headers["User-Agent"] = "MikeBrain/1.0 (personal indexer)"
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        session.headers["Authorization"] = f"token {token}"
    
    print(f"Fetching repos for {GITHUB_USER}...")
    repos = get_repos(session)
    print(f"Found {len(repos)} public repos")
    
    added = 0
    skipped = 0
    no_readme = 0
    total_chunks = 0
    
    for i, repo in enumerate(repos):
        name = repo["name"]
        
        if repo.get("archived", False):
            print(f"[{i+1}/{len(repos)}] {name} - SKIP (archived)")
            skipped += 1
            continue
        
        print(f"[{i+1}/{len(repos)}] {name}...", end="", flush=True)
        
        readme = get_readme(session, name)
        if not readme:
            print(" SKIP (no README)")
            no_readme += 1
            continue
        
        description = repo.get("description") or ""
        topics = repo.get("topics") or []
        language = repo.get("language") or ""
        url = repo.get("html_url", f"https://github.com/{GITHUB_USER}/{name}")
        
        # Build header (always included in each chunk)
        header_parts = [f"Repo: {name}"]
        if description:
            header_parts.append(f"Description: {description}")
        if topics:
            header_parts.append(f"Topics: {', '.join(topics)}")
        if language:
            header_parts.append(f"Language: {language}")
        header = "\n".join(header_parts) + "\n\n"
        
        # Chunk the README
        readme_chunks = chunk_text(readme, CHUNK_SIZE)
        if not readme_chunks:
            readme_chunks = ["(no content)"]
        
        repo_added = 0
        for ci, chunk in enumerate(readme_chunks):
            doc_id = f"github_{name}_chunk{ci}" if len(readme_chunks) > 1 else f"github_{name}"
            doc_text = header + chunk
            
            metadata = {
                "source": "github",
                "repo_name": name,
                "url": url,
                "language": language,
                "description": description[:300] if description else "",
                "stars": str(repo.get("stargazers_count", 0)),
                "topics": ", ".join(topics) if topics else "",
                "chunk_index": ci,
                "total_chunks": len(readme_chunks),
            }
            
            if upsert_doc(doc_id, doc_text, metadata):
                repo_added += 1
                total_chunks += 1
        
        if repo_added > 0:
            added += 1
            print(f" +{repo_added} chunks")
        else:
            skipped += 1
            print(" already indexed")
        
        time.sleep(0.1)
    
    print(f"\n=== GitHub Ingest Complete ===")
    print(f"Total repos: {len(repos)}")
    print(f"Added: {added}")
    print(f"Skipped (already indexed or archived): {skipped}")
    print(f"No README: {no_readme}")
    print(f"Total chunks added: {total_chunks}")


if __name__ == "__main__":
    main()
