import os
from pathlib import Path

# --- API Keys ---
def _load_key(env_var, config_file):
    if os.environ.get(env_var):
        return os.environ[env_var]
    path = Path.home() / ".config" / config_file
    if path.exists():
        return path.read_text().strip()
    return None

OPENAI_API_KEY = _load_key("OPENAI_API_KEY", "openai_api_key")

# --- Podcast Settings ---
PODCAST_TITLE = "Mike's Weekly Briefing"
PODCAST_DESCRIPTION = "A weekly AI-generated conversational podcast covering AI, tech, crypto, and Australian news — curated to Mike's interests."
PODCAST_AUTHOR = "Mike Cann"
PODCAST_EMAIL = "mike.cann@gmail.com"
BASE_URL = "https://bruce.tail9ef766.ts.net/podcast"  # Tailscale IP — update to Funnel URL when configured

# --- Content Settings ---
INTERESTS = [
    "AI and large language models",
    "tech industry and software development",
    "crypto and web3",
    "Australian tech news",
]

BLOG_RSS_URL = "https://mikecann.blog/feed"  # pulled to discover new topics from Mike's writing

EPISODE_DAY = "Thursday"
EPISODE_TARGET_MINUTES = 15

# --- Paths ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TOPICS_DIR = DATA_DIR / "topics"
EPISODES_DIR = DATA_DIR / "episodes"
WWW_DIR = Path.home() / "www" / "podcast"
