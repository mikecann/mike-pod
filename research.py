"""
research.py — Daily topic research via RSS feeds + StashIt.
Fetches recent articles and saves to data/topics/YYYY-MM-DD.json
"""
import json
import os
import shutil
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

import re
import requests
from config import BLOG_RSS_URL, TOPICS_DIR

TOPICS_DIR.mkdir(parents=True, exist_ok=True)

# Curated from the Daily Brief RSS pool, then narrowed for mike-pod.
# Principle: fewer generic business feeds, more primary/expert/developer sources.
# Crypto/Web3 is intentionally not a default feed category; it should only appear
# through StashIt/Mike-interest items or genuinely mainstream tech coverage.
FEEDS = {
    "AI and large language models": [
        # Primary / research / expert sources
        "https://openai.com/news/rss.xml",
        "https://blog.google/technology/ai/rss/",
        "https://huggingface.co/blog/feed.xml",
        "https://simonwillison.net/atom/everything/",
        "https://www.latent.space/feed",
        "https://jack-clark.net/feed/",
        "https://thegradient.pub/rss/",
        "https://bair.berkeley.edu/blog/feed.xml",
        # Reporting, but AI-specific rather than generic business firehose
        "https://www.technologyreview.com/topic/artificial-intelligence/feed",
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "https://the-decoder.com/feed/",
        "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",
    ],
    "tech industry and software development": [
        # Developer/platform/security sources with practical angles
        "https://feeds.arstechnica.com/arstechnica/index",
        "https://www.theregister.com/headlines.atom",
        "https://stackoverflow.blog/feed/",
        "https://github.blog/feed/",
        "https://blog.cloudflare.com/rss/",
        "https://news.ycombinator.com/rss",
        "https://lobste.rs/rss",
        "https://feeds.feedburner.com/TheHackersNews",
        "https://www.schneier.com/feed/atom/",
        "https://krebsonsecurity.com/feed/",
    ],
    "Australian tech news": [
        # Tech-specific Australian sources; avoid broad ABC general-news leakage.
        "https://www.itnews.com.au/rss/rss.ashx",
        "https://www.innovationaus.com/feed/",
        "https://www.startupdaily.net/feed/",
        "https://www.smartcompany.com.au/startupsmart/feed/",
        "https://www.smartcompany.com.au/technology/feed/",
    ],
}


def parse_rss_date(date_str: str):
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def is_recent(pub_date, hours: int = 48) -> bool:
    if not pub_date:
        return True
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return pub_date >= cutoff


def fetch_feed(url: str, topic: str, max_items: int = 5) -> list:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        results = []
        for item in items:
            title = (item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not link:
                link_el = item.find("{http://www.w3.org/2005/Atom}link")
                if link_el is not None:
                    link = link_el.get("href", "")
            pub = item.findtext("pubDate") or item.findtext("{http://www.w3.org/2005/Atom}published") or ""
            desc = item.findtext("description") or item.findtext("{http://www.w3.org/2005/Atom}summary") or ""
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
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "source": url,
                })
            if len(results) >= max_items:
                break
        return results
    except Exception as e:
        print(f"    Feed error ({url}): {e}")
        return []


def fetch_blog_topics() -> list:
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
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "source": BLOG_RSS_URL,
                })
        return topics
    except Exception as e:
        print(f"  Blog fetch failed: {e}")
        return []


def fetch_stashit_reads(days: int = 7) -> list:
    """Pull recently archived StashIt items + notes via Convex CLI."""
    import subprocess
    npx_path = shutil.which("npx")
    if not npx_path:
        print("  StashIt fetch failed: npx is not available on PATH")
        return []

    stashit_convex_dir = Path.home() / "dev" / "me" / "stashit" / "packages" / "convex"
    since = int((datetime.now(timezone.utc).timestamp() - days * 86400) * 1000)
    args_json = json.dumps({"since": since})
    try:
        result = subprocess.run(
            [npx_path, "convex", "run", "podcastFeed:getRecentReads", "--prod", args_json],
            capture_output=True, text=True, timeout=30,
            cwd=stashit_convex_dir,
            env=os.environ.copy(),
        )
        items = json.loads(result.stdout)
        results = []
        for item in items:
            note_text = " | ".join(item.get("notes", []))
            results.append({
                "topic": "stashit_read",
                "title": item.get("title") or item["url"],
                "url": item["url"],
                "summary": (item.get("description") or "")[:200]
                    + (f" [Mike note: {note_text}]" if note_text else ""),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": "stashit",
            })
        return results
    except Exception as e:
        print(f"  StashIt fetch failed: {e}")
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
            print(f"    {feed_url.split('/')[2]} -> {len(items)} items")
            all_results.extend(items)

    blog = fetch_blog_topics()
    all_results.extend(blog)
    print(f"  mikecann.blog -> {len(blog)} posts")

    stashit = fetch_stashit_reads()
    all_results.extend(stashit)
    print(f"  stashit -> {len(stashit)} recently read articles")

    output_file.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved {len(all_results)} total items to {output_file}")



    # Trigger deep research for any new StashIt items
    if stashit:
        print("\nRunning deep research on new StashIt items...")
        import subprocess, hashlib as _hash
        from pathlib import Path as _Path
        for item in stashit:
            # Use a hash of the URL as the item ID when no Convex ID is available
            item_id = _hash.md5(item["url"].encode()).hexdigest()[:16]
            research_file = _Path(__file__).parent / "data" / "deep_research" / f"{item_id}.json"
            if not research_file.exists():
                title = item.get("title", "")
                note = ""
                summary = item.get("summary", "")
                m = re.search(r"\[Mike note: (.+?)\]", summary)
                if m: note = m.group(1)
                print(f"  Deep researching: {(title or item['url'])[:60]}")
                subprocess.run([
                    str(_Path(__file__).parent / "venv" / "bin" / "python"),
                    str(_Path(__file__).parent / "deep_research.py"),
                    item_id, item["url"], "--title", title, "--note", note,
                ], timeout=120)

if __name__ == "__main__":
    run()
