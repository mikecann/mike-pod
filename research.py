"""
research.py — Daily topic research via RSS feeds.
Fetches recent articles and saves to data/topics/YYYY-MM-DD.json
"""
import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

import requests
from config import BLOG_RSS_URL, TOPICS_DIR

TOPICS_DIR.mkdir(parents=True, exist_ok=True)

FEEDS = {
    "AI and large language models": [
        "https://feeds.feedburner.com/AITrends",
        "https://www.artificialintelligence-news.com/feed/",
    ],
    "tech industry and software development": [
        "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "https://techcrunch.com/feed/",
        "https://news.ycombinator.com/rss",
    ],
    "crypto and web3": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ],
    "Australian tech news": [
        "https://www.abc.net.au/news/feed/51120/rss.xml",
        "https://feeds.feedburner.com/zdnet/australia",
        "https://www.itnews.com.au/rss",
    ],
}

def parse_rss_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    try:
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%a, %d %b %Y %H:%M:%S %z"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
    except Exception:
        pass
    return None

def is_recent(pub_date: datetime | None, hours: int = 48) -> bool:
    if not pub_date:
        return True  # include if we can't tell
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return pub_date >= cutoff

def fetch_feed(url: str, topic: str, max_items: int = 5) -> list[dict]:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)

        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        results = []
        for item in items:
            title = (item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link = (item.findtext("link") or item.findtext("{http://www.w3.org/2005/Atom}link") or "").strip()
            if not link:
                link_el = item.find("{http://www.w3.org/2005/Atom}link")
                if link_el is not None:
                    link = link_el.get("href", "")
            pub = (item.findtext("pubDate") or item.findtext("{http://www.w3.org/2005/Atom}published") or "")
            desc = (item.findtext("description") or item.findtext("{http://www.w3.org/2005/Atom}summary") or "")
            # Strip HTML tags from description
            import re
            desc = re.sub(r"<[^>]+>", "", desc).strip()[:200]

            pub_dt = parse_rss_date(pub)
            if not is_recent(pub_dt):
                continue
            if title and link:
                results.append({
                    "topic": topic,
                    "title": title,
                    "url": link,
                    "summary": desc,
                    "published": pub,
                    "fetched_at": datetime.utcnow().isoformat(),
                    "source": url,
                })
            if len(results) >= max_items:
                break
        return results
    except Exception as e:
        print(f"    Feed error ({url}): {e}")
        return []

def fetch_blog_topics() -> list[dict]:
    try:
        r = requests.get(BLOG_RSS_URL, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
        topics = []
        for item in root.findall(".//item")[:3]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            if title:
                topics.append({
                    "topic": "mike_blog",
                    "title": title,
                    "url": link,
                    "summary": f"Mike's recent blog post: {title}",
                    "published": pub,
                    "fetched_at": datetime.utcnow().isoformat(),
                    "source": BLOG_RSS_URL,
                })
        return topics
    except Exception as e:
        print(f"  Blog fetch failed: {e}")
        return []

def run():
    today = date.today().isoformat()
    output_file = TOPICS_DIR / f"{today}.json"
    all_results = []

    print(f"Researching topics for {today}...")
    for topic, feeds in FEEDS.items():
        print(f"  {topic}:")
        for feed_url in feeds:
            items = fetch_feed(feed_url, topic)
            print(f"    {feed_url.split('/')[2]} → {len(items)} items")
            all_results.extend(items)

    blog = fetch_blog_topics()
    all_results.extend(blog)
    print(f"  mikecann.blog → {len(blog)} posts")

    output_file.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved {len(all_results)} total items to {output_file}")

if __name__ == "__main__":
    run()
