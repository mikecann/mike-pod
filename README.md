# mike-pod

Personal weekly AI-generated podcast. Conversational two-host format covering AI, tech, crypto, and Australian news.

## How it works

- **Mon–Thu**: `research.py` runs daily, collecting interesting stories into `data/topics/`
- **Thursday**: `generate.py` picks the best stories, generates a ~15min MP3 via Podcastfy, and updates the RSS feed
- **Overcast**: Subscribe to `http://100.109.98.30/podcast/feed.xml` (update to Funnel URL when configured)

## Setup

```bash
pip install -r requirements.txt
echo "YOUR_OPENAI_API_KEY" > ~/.config/openai_api_key
```

## Running manually

```bash
python research.py    # collect today's topics
python generate.py    # generate this week's episode
python feed.py        # rebuild RSS feed only
```

## Config

Edit `config.py` to update interests, podcast title, base URL, etc.
