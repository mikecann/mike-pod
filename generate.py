"""
generate.py — Weekly episode generator.
Reads the week's collected topics, generates a conversational MP3 via Podcastfy,
copies it to ~/www/podcast/, and updates the RSS feed.
Run on Thursdays via scheduled task.
"""
import json
import os
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
DEEP_RESEARCH_DIR = Path(__file__).parent / "data" / "deep_research"
WWW_DIR.mkdir(parents=True, exist_ok=True)

# Topics to skip — enterprise fluff that Mike doesn't care about
SKIP_KEYWORDS = [
    "enterprise", "sap", "salesforce", "oracle", "workday", "servicenow",
    "corporate", "b2b", "procurement", "compliance", "quarterly earnings",
    "hyundai robotics", "adoption of ai", "digital transformation",
    "workforce productivity", "hr tech", "supply chain ai",
]

def get_week_topics() -> list:
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


def is_relevant(item: dict) -> bool:
    """Filter out enterprise fluff."""
    text = (item.get("title") or "" + item.get("summary") or "").lower()
    return not any(kw in text for kw in SKIP_KEYWORDS)


def pick_top_stories(topics: list, n: int = 12) -> dict:
    """
    Deduplicate, filter, and group stories by topic category.
    Returns dict of {category: [stories]}
    """
    seen_urls = set()
    by_topic = {
        "AI and large language models": [],
        "tech industry and software development": [],
        "crypto and web3": [],  # capped at 1 below
        "Australian tech news": [],
        "stashit_read": [],
        "mike_blog": [],
    }

    for item in topics:
        url = item.get("url", "")
        if url in seen_urls:
            continue
        if not is_relevant(item):
            continue
        seen_urls.add(url)
        topic = item.get("topic", "")
        if topic in by_topic:
            by_topic[topic].append(item)

    # Cap each category
    for k in by_topic:
        cap = 1 if k == "crypto and web3" else 4
        by_topic[k] = by_topic[k][:cap]

    return by_topic


def format_source(item: dict) -> str:
    """Extract a readable source name from URL."""
    try:
        from urllib.parse import urlparse
        host = urlparse(item.get("url", "")).netloc
        return host.replace("www.", "").replace("feeds.", "")
    except Exception:
        return "unknown source"


def build_content_string(by_topic: dict) -> str:
    """
    Build a richly structured content string for Podcastfy.
    Hosts are instructed to cite sources explicitly.
    """
    today_str = date.today().strftime("%B %d, %Y")
    lines = [
        f"Weekly tech and AI briefing — week of {today_str}.",
        "The following articles and sources were researched for this episode.",
        "For each story, the source publication and title are provided.",
        "",
    ]

    SECTION_LABELS = {
        "AI and large language models": "AI & LARGE LANGUAGE MODELS",
        "tech industry and software development": "TECH & SOFTWARE",
        "crypto and web3": "CRYPTO & WEB3",
        "Australian tech news": "AUSTRALIAN NEWS",
        "stashit_read": "FROM MIKE'S READING LIST THIS WEEK",
        "mike_blog": "FROM MIKE'S BLOG",
    }

    for topic_key, label in SECTION_LABELS.items():
        stories = by_topic.get(topic_key, [])
        if not stories:
            continue
        lines.append(f"== {label} ==")
        for s in stories:
            source = format_source(s)
            title = s.get("title") or "Untitled"
            summary = s.get("summary") or ""
            note = ""
            # Extract StashIt note if present
            if "[Mike note:" in summary:
                import re
                m = re.search(r"\[Mike note: (.+?)\]", summary)
                if m:
                    note = m.group(1)
                    summary = summary[:summary.index("[Mike note:")].strip()

            lines.append(f"[Source: {source}]")
            lines.append(f"Title: {title}")
            if summary:
                lines.append(f"Summary: {summary[:300]}")
            if note:
                lines.append(f"Mike's note when reading this: \"{note}\"")

            # Inject deep research brief for StashIt items
            if topic_key == "stashit_read":
                import hashlib as _h
                item_id = _h.md5(s.get("url", "").encode()).hexdigest()[:16]
                rf = DEEP_RESEARCH_DIR / f"{item_id}.json"
                if rf.exists():
                    research = json.loads(rf.read_text())
                    a = research.get("analysis", {})
                    lines.append("[DEEP RESEARCH BRIEF — use this for a substantive discussion]")
                    if a.get("one_sentence_summary"):
                        lines.append(f"What it's actually about: {a['one_sentence_summary']}")
                    if a.get("answer_to_mike"):
                        lines.append(f"Answer to Mike's question: {a['answer_to_mike']}")
                    for insight in a.get("key_insights", [])[:3]:
                        lines.append(f"- Key insight: {insight}")
                    for counter in a.get("counterarguments", [])[:2]:
                        lines.append(f"- Counterargument: {counter}")
                    if a.get("implications_for_developers"):
                        lines.append(f"For developers: {a['implications_for_developers']}")
                    for q in a.get("interesting_questions_to_explore", [])[:2]:
                        lines.append(f"- Good question to explore: {q}")
                    lines.append("[END DEEP RESEARCH]")
                else:
                    lines.append("[Note: no deep research available yet for this item]")

            lines.append(f"URL: {s.get('url', '')}")
            lines.append("")

    return "\n".join(lines)


def run():
    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set.")
        sys.exit(1)

    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
    os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")

    today = date.today().isoformat()
    mp3_name = f"{today}-episode.mp3"
    mp3_path = EPISODES_DIR / mp3_name
    meta_path = EPISODES_DIR / f"{today}-episode.json"

    print("Collecting week's topics...")
    topics = get_week_topics()
    if not topics:
        print("No topics found for this week. Exiting.")
        sys.exit(0)

    by_topic = pick_top_stories(topics)
    total = sum(len(v) for v in by_topic.values())
    print(f"  Selected {total} stories across {sum(1 for v in by_topic.values() if v)} categories")
    for k, v in by_topic.items():
        if v:
            print(f"    {k}: {len(v)} stories")

    content = build_content_string(by_topic)

    # Conversation config — better roles, source-citing instructions, sections
    conversation_config = {
        "conversation_style": ["analytical", "opinionated", "conversational", "curious"],
        "roles_person1": "sharp tech journalist who has read all the articles and has strong, specific opinions",
        "roles_person2": "developer and tinkerer who asks probing follow-up questions and pushes for concrete implications",
        "dialogue_structure": [
            "Cold Open — hook the listener with the most interesting story of the week",
            "AI & LLMs",
            "Tech & Software",
            "Crypto & Web3",
            "Australian News",
            "Mike's Reading List — personal picks from the host's own reading",
            "Wrap Up"
        ],
        "podcast_name": "Mike's Weekly Briefing",
        "podcast_tagline": "Your personal tech and AI podcast",
        "creativity": 0.9,
        "user_instructions": (
            "CRITICAL RULES FOR THIS PODCAST:\n"
            "1. ALWAYS cite the specific source when mentioning a story. Say things like "
            "\"There's a piece from TechCrunch today...\" or \"Ars Technica reported...\" or "
            "\"A new paper from...\" Never discuss a topic without naming where it came from.\n"
            "2. For stories from Mike's Reading List (StashIt), say something like "
            "\"Mike, you read an article this week from [source] titled [title] and noted [note]... "
            "that made me think...\". Make it personal and conversational.\n"
            "3. Be OPINIONATED and ANALYTICAL. Don't just summarise — critique, make predictions, "
            "disagree, ask what it means for developers or makers.\n"
            "4. Skip surface-level takes. Go deeper: what's the actual implication? Who wins, who loses?\n"
            "5. Keep each story to 1-2 minutes. Be punchy. Don't pad.\n"
            "6. Reference timing naturally — \"yesterday\", \"earlier this week\", \"just announced\".\n"
            "7. The target listener is a technically curious developer who reads Hacker News and cares "
            "about AI, indie software, and what's actually happening in tech — not enterprise fluff."
        ),
    }

    print("Generating podcast audio with Podcastfy...")
    try:
        from podcastfy.client import generate_podcast
        audio_file = generate_podcast(
            text=content,
            tts_model="openai",
            llm_model_name="gpt-5.4",
            api_key_label="OPENAI_API_KEY",
            conversation_config=conversation_config,
            longform=EPISODE_TARGET_MINUTES > 10,
        )

        # Re-encode at higher bitrate using ffmpeg
        high_quality_path = str(mp3_path) + ".hq.mp3"
        subprocess.run([
            "/opt/homebrew/bin/ffmpeg", "-y", "-i", audio_file,
            "-codec:a", "libmp3lame", "-b:a", "192k",
            high_quality_path
        ], check=True, capture_output=True)
        shutil.move(high_quality_path, mp3_path)
        print(f"  Audio saved to {mp3_path} (192kbps)")
    except Exception as e:
        print(f"ERROR generating podcast: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # Save metadata
    meta = {
        "title": f"{PODCAST_TITLE} — {date.today().strftime('%B %d, %Y')}",
        "date": today,
        "filename": mp3_name,
        "topics_covered": [k for k, v in by_topic.items() if v],
        "story_count": total,
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    # Copy to www
    dest = WWW_DIR / mp3_name
    shutil.copy(mp3_path, dest)
    print(f"  Copied to {dest}")

    print("Updating RSS feed...")
    subprocess.run([sys.executable, "feed.py"], check=True)
    print("Done!")


if __name__ == "__main__":
    run()
