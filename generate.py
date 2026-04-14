"""
generate.py — Weekly episode generator.
Reads the week's collected topics, generates a conversational MP3 via Podcastfy,
copies it to ~/www/podcast/, and updates the RSS feed.
Run on Thursdays via scheduled task.
"""
import json
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from config import (
    OPENAI_API_KEY, TOPICS_DIR, EPISODES_DIR, WWW_DIR,
    PODCAST_TITLE, EPISODE_TARGET_MINUTES
)

EPISODES_DIR.mkdir(parents=True, exist_ok=True)
WWW_DIR.mkdir(parents=True, exist_ok=True)

def get_week_topics() -> list[dict]:
    """Collect all topics from Mon–today."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    all_topics = []
    for n in range((today - monday).days + 1):
        day = monday + timedelta(days=n)
        f = TOPICS_DIR / f"{day.isoformat()}.json"
        if f.exists():
            all_topics.extend(json.loads(f.read_text()))
    return all_topics

def pick_top_stories(topics: list[dict], n: int = 10) -> list[dict]:
    """Deduplicate by URL and pick top n stories."""
    seen_urls = set()
    unique = []
    for t in topics:
        if t.get("url") not in seen_urls:
            seen_urls.add(t["url"])
            unique.append(t)
    # Prioritise non-blog topics, then blog
    non_blog = [t for t in unique if t.get("topic") != "mike_blog"]
    blog = [t for t in unique if t.get("topic") == "mike_blog"]
    return (non_blog + blog)[:n]

def build_content_string(stories: list[dict]) -> str:
    lines = [f"Weekly tech and AI briefing for the week of {date.today().strftime('%B %d, %Y')}.\n"]
    for s in stories:
        lines.append(f"- {s.get('title', 'Untitled')}: {s.get('summary', '')} ({s.get('url', '')})")
    return "\n".join(lines)

def run():
    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set. Add it to ~/.config/openai_api_key")
        sys.exit(1)

    import os
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

    today = date.today().isoformat()
    mp3_name = f"{today}-episode.mp3"
    mp3_path = EPISODES_DIR / mp3_name
    meta_path = EPISODES_DIR / f"{today}-episode.json"

    print("Collecting week's topics...")
    topics = get_week_topics()
    if not topics:
        print("No topics found for this week. Exiting.")
        sys.exit(0)

    stories = pick_top_stories(topics)
    print(f"  Selected {len(stories)} stories for the episode")

    content = build_content_string(stories)

    print("Generating podcast audio with Podcastfy...")
    try:
        from podcastfy.client import generate_podcast
        audio_file = generate_podcast(
            text=content,
            tts_model="openai",
            longform=EPISODE_TARGET_MINUTES > 10,
        )
        shutil.copy(audio_file, mp3_path)
        print(f"  Audio saved to {mp3_path}")
    except Exception as e:
        print(f"ERROR generating podcast: {e}")
        sys.exit(1)

    # Save metadata
    meta = {
        "title": f"{PODCAST_TITLE} — {date.today().strftime('%B %d, %Y')}",
        "date": today,
        "filename": mp3_name,
        "topics_covered": list({s["topic"] for s in stories}),
        "story_count": len(stories),
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    # Copy to www
    dest = WWW_DIR / mp3_name
    shutil.copy(mp3_path, dest)
    print(f"  Copied to {dest}")

    # Update RSS
    print("Updating RSS feed...")
    subprocess.run([sys.executable, "feed.py"], check=True)
    print("Done!")

if __name__ == "__main__":
    run()
