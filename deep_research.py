"""
deep_research.py — Deep research on a single StashIt article.

Given a URL, title, and Mike's note/question, this script:
1. Fetches and extracts the article's full text
2. Parses Mike's note into a question/observation
3. Runs multiple targeted web searches for counter-arguments,
   alternatives, technical analysis, and related work
4. Uses GPT-4o to synthesise everything into a structured research brief
5. Saves the brief to data/deep_research/{item_id}.json

Usage:
  python deep_research.py <item_id> <url> [--title "..."] [--note "..."]
  python deep_research.py --from-stashit  # processes all unresearched StashIt items
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
from config import OPENAI_API_KEY, TOPICS_DIR

DEEP_RESEARCH_DIR = Path(__file__).parent / "data" / "deep_research"
DEEP_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

NODE_BIN = "/Users/bruce/.nvm/versions/node/v24.14.1/bin"
CONVEX_DIR = "/Users/bruce/stashit/packages/convex"

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch_article_text(url: str, max_chars: int = 8000) -> str:
    """Fetch and extract readable text from a URL."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text_parts = []
                self._skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self._skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip:
                    cleaned = data.strip()
                    if cleaned:
                        self.text_parts.append(cleaned)

        extractor = TextExtractor()
        extractor.feed(r.text)
        text = " ".join(extractor.text_parts)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"[Could not fetch article: {e}]"


def ddg_search(query: str, max_results: int = 5) -> list[dict]:
    """Search DuckDuckGo and return results."""
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1},
            timeout=10,
            headers=HEADERS,
        )
        data = r.json()
        results = []
        for item in data.get("RelatedTopics", [])[:max_results]:
            if "Text" in item and "FirstURL" in item:
                results.append({"title": item["Text"][:200], "url": item["FirstURL"]})
        return results
    except Exception:
        return []


def search_for_context(title: str, note: str) -> list[dict]:
    """Run multiple targeted searches to gather research context."""
    topic = title or note
    queries = [
        f"{topic} criticism drawbacks problems",
        f"{topic} vs alternatives comparison",
        f"{topic} technical analysis deep dive",
        f"{topic} real world experience",
    ]
    if note and len(note) > 10:
        queries.append(note)  # search for the exact question Mike asked

    all_results = []
    seen_urls = set()
    for q in queries:
        results = ddg_search(q, max_results=4)
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                r["query"] = q
                all_results.append(r)
        time.sleep(0.3)

    return all_results[:20]


def synthesise_with_gpt4(
    url: str,
    title: str,
    note: str,
    article_text: str,
    search_results: list[dict],
    client: OpenAI,
) -> dict:
    """Use GPT-4o to synthesise all research into a structured brief."""

    search_context = "\n".join(
        f"- [{r.get('query', '')}] {r['title']} ({r['url']})"
        for r in search_results[:15]
    )

    prompt = f"""You are a research analyst preparing a deep briefing for a podcast host.
The podcast is for a technically curious developer/maker who values depth over fluff.

ARTICLE BEING RESEARCHED:
Title: {title or 'Unknown'}
URL: {url}
Mike's note/question: "{note or 'No specific note'}"

ARTICLE CONTENT (first 8000 chars):
{article_text}

RELATED SEARCH RESULTS FOUND:
{search_context or 'No additional search results found.'}

Your task: Write a thorough, opinionated research brief that will be used by podcast hosts to have a genuinely insightful discussion about this article. The hosts should be able to go beyond the surface level.

Return a JSON object with these exact keys:
{{
  "one_sentence_summary": "What this article is actually about in plain language",
  "answer_to_mike": "Direct, substantive answer to Mike's note/question. If he asked about negatives, list specific real negatives. Be concrete and technical where relevant.",
  "key_insights": ["3-5 specific, non-obvious insights from the article or related research"],
  "counterarguments": ["2-3 genuine criticisms or alternative perspectives on the main thesis"],
  "competing_approaches": ["What are the alternatives? How do they compare?"],
  "implications_for_developers": "What does this mean practically for someone building software?",
  "interesting_questions_to_explore": ["2-3 follow-up questions the hosts could explore on air"],
  "sources_worth_citing": [
    {{"title": "...", "url": "...", "why_relevant": "..."}}
  ]
}}

Be direct, specific, and opinionated. Avoid vague statements like "it depends" or "there are pros and cons." Give actual analysis."""

    response = client.chat.completions.create(
        model="gpt-5.4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)


def research_item(item_id: str, url: str, title: str = "", note: str = "") -> dict:
    """Run full deep research on a single item. Returns the research brief."""
    out_file = DEEP_RESEARCH_DIR / f"{item_id}.json"

    # Skip if already researched recently (within 7 days)
    if out_file.exists():
        existing = json.loads(out_file.read_text())
        researched_at = existing.get("researched_at", "")
        if researched_at:
            age_days = (datetime.now(timezone.utc).timestamp() -
                        datetime.fromisoformat(researched_at).timestamp()) / 86400
            if age_days < 7:
                print(f"  Already researched {item_id} ({age_days:.1f} days ago), skipping.")
                return existing

    client = OpenAI(api_key=OPENAI_API_KEY)

    print(f"  Fetching article: {url}")
    article_text = fetch_article_text(url)

    print(f"  Searching for context ({title or url[:50]}...)")
    search_results = search_for_context(title or url, note)
    print(f"    Found {len(search_results)} related results")

    print(f"  Synthesising with GPT-4o...")
    analysis = synthesise_with_gpt4(url, title, note, article_text, search_results, client)

    brief = {
        "item_id": item_id,
        "url": url,
        "title": title,
        "mike_note": note,
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "analysis": analysis,
    }

    out_file.write_text(json.dumps(brief, indent=2))
    print(f"  Saved research brief to {out_file}")
    return brief


def get_unresearched_stashit_items(days: int = 14) -> list[dict]:
    """Fetch StashIt items that haven't been deeply researched yet."""
    import subprocess
    since = int((datetime.now(timezone.utc).timestamp() - days * 86400) * 1000)
    env = {**os.environ, "PATH": NODE_BIN + ":" + os.environ.get("PATH", "")}
    args_json = json.dumps({"since": since})
    try:
        result = subprocess.run(
            [NODE_BIN + "/npx", "convex", "run", "podcastFeed:getRecentReads", "--prod", args_json],
            capture_output=True, text=True, timeout=30, cwd=CONVEX_DIR, env=env,
        )
        items = json.loads(result.stdout)
        # Filter to items without existing research
        unresearched = []
        for item in items:
            out_file = DEEP_RESEARCH_DIR / f"{item['id']}.json"
            if not out_file.exists():
                unresearched.append(item)
            else:
                # Check if research is stale (> 7 days)
                existing = json.loads(out_file.read_text())
                researched_at = existing.get("researched_at", "")
                if researched_at:
                    age = (datetime.now(timezone.utc).timestamp() -
                           datetime.fromisoformat(researched_at).timestamp()) / 86400
                    if age > 7:
                        unresearched.append(item)
        return unresearched
    except Exception as e:
        print(f"  Could not fetch StashIt items: {e}")
        return []


def run_all():
    """Research all unresearched StashIt items."""
    print("Checking for unresearched StashIt items...")
    items = get_unresearched_stashit_items()
    if not items:
        print("  All items already researched.")
        return

    print(f"  Found {len(items)} items to research")
    for item in items:
        notes = item.get("notes", [])
        note = notes[0] if notes else ""
        print(f"\nResearching: {item.get('title') or item['url'][:60]}")
        if note:
            print(f"  Mike's note: \"{note}\"")
        try:
            research_item(
                item_id=item["id"],
                url=item["url"],
                title=item.get("title") or "",
                note=note,
            )
        except Exception as e:
            print(f"  ERROR: {e}")


if __name__ == "__main__":
    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set.")
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("item_id", nargs="?", help="Specific item ID to research")
    parser.add_argument("url", nargs="?", help="URL to research")
    parser.add_argument("--title", default="", help="Article title")
    parser.add_argument("--note", default="", help="Mike's note/question")
    parser.add_argument("--all", action="store_true", help="Research all unresearched StashIt items")
    args = parser.parse_args()

    if args.all or (not args.item_id):
        run_all()
    else:
        research_item(args.item_id, args.url, args.title, args.note)
