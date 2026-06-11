#!/usr/bin/env python3
"""Small client for Mike's Second Brain retrieval API.

Used by Bruce automations to add personal context/citations without coupling every
script directly to ChromaDB internals.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Iterable

DEFAULT_BASE_URL = os.environ.get("BRUCE_BRAIN_BASE_URL", "https://bruce.tail9ef766.ts.net/brain").rstrip("/")


def request_json(path: str, payload: dict, *, base_url: str = DEFAULT_BASE_URL, timeout: int = 45) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url + path,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Bruce-Brain-Client/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_context(query: str, *, sources: Iterable[str] | None = None, limit: int = 6, base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    payload = {"query": query, "limit": limit}
    srcs = [s for s in (sources or []) if s]
    if srcs:
        payload["sources"] = srcs
    try:
        data = request_json("/api/ask-context", payload, base_url=base_url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        # Automations should degrade gracefully if the brain is briefly offline.
        return [{"error": f"Second Brain lookup failed: {exc}"}]
    return data.get("results", []) if isinstance(data, dict) else []


def format_context(results: list[dict], *, max_items: int = 5, max_excerpt: int = 260) -> str:
    usable = [r for r in results if not r.get("error")]
    if not usable:
        if results and results[0].get("error"):
            return f"Second Brain context unavailable: {results[0]['error']}"
        return "No related Second Brain context found."
    lines = []
    for idx, r in enumerate(usable[:max_items], 1):
        title = r.get("title") or "Untitled"
        source = r.get("source") or "unknown"
        url = r.get("url") or ""
        why = r.get("why_relevant") or "Related prior item."
        score = r.get("relevance")
        excerpt = " ".join(str(r.get("excerpt") or "").split())[:max_excerpt]
        score_text = f" relevance={score:.2f}" if isinstance(score, (int, float)) else ""
        lines.append(f"{idx}. [{source}{score_text}] {title}")
        if why:
            lines.append(f"   Why: {why}")
        if excerpt:
            lines.append(f"   Excerpt: {excerpt}")
        if url:
            lines.append(f"   URL: {url}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Query Mike's Second Brain for cited context.")
    parser.add_argument("query", nargs="+", help="Query text")
    parser.add_argument("--source", "--sources", dest="sources", default="", help="Comma-separated sources: blog,youtube,github,articles")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--json", action="store_true", help="Print raw JSON results")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    query = " ".join(args.query)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    results = get_context(query, sources=sources, limit=args.limit, base_url=args.base_url)
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(format_context(results, max_items=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
