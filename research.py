"""
research.py — Daily topic research.
Searches for recent news across Mike's interests and saves to data/topics/YYYY-MM-DD.json
Run daily Mon–Thu via scheduled task.
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from config import INTERESTS, BLOG_RSS_URL, TOPICS_DIR

TOPICS_DIR.mkdir(parents=True, exist_ok=True)

def search_ddg(query: str, max_results: int = 5) -> list[dict]:
    """Search DuckDuckGo instant answers for recent articles."""
    try:
        params = {
            "q": f"{query} after:{(date.today() - timedelta(days=1)).isoformat()}",
            "format": "json",
            "no_redirect": 1,
            "no_html": 1,
        }
        r = requests.get("https://api.duckduckgo.com/", params=params, timeout=10)
        data = r.json()
        results = []
        for item in data.get("RelatedTopics", [])[:max_results]:
            if "Text" in item and "FirstURL" in item:
                results.append({
                    "title": item["Text"][:120],
                    "url": item["FirstURL"],
                    "summary": item["Text"],
                    "source": "duckduckgo",
                })
        return results
    except Exception as e:
        print(f"  DDG search failed for '{query}': {e}")
        return []

def fetch_blog_topics() -> list[dict]:
    """Fetch recent post titles from Mike's blog RSS to surface new interests."""
    try:
        r = requests.get(BLOG_RSS_URL, timeout=10)
        # Simple XML parse without external deps
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        topics = []
        for item in root.findall(".//item")[:5]:
            title_el = item.find("title")
            link_el = item.find("link")
            if title_el is not None:
                topics.append({
                    "title": title_el.text,
                    "url": link_el.text if link_el is not None else BLOG_RSS_URL,
                    "summary": f"Mike's blog post: {title_el.text}",
                    "source": "mikecann.blog",
                })
        return topics
    except Exception as e:
        print(f"  Blog fetch failed: {e}")
        return []

def run():
    today = date.today().isoformat()
    output_file = TOPICS_DIR / f"{today}.json"

    print(f"Researching topics for {today}...")
    all_results = []

    for topic in INTERESTS:
        print(f"  Searching: {topic}")
        results = search_ddg(topic)
        for r in results:
            r["topic"] = topic
            r["fetched_at"] = datetime.utcnow().isoformat()
        all_results.extend(results)

    blog_topics = fetch_blog_topics()
    for t in blog_topics:
        t["topic"] = "mike_blog"
        t["fetched_at"] = datetime.utcnow().isoformat()
    all_results.extend(blog_topics)

    output_file.write_text(json.dumps(all_results, indent=2))
    print(f"  Saved {len(all_results)} items to {output_file}")

if __name__ == "__main__":
    run()
