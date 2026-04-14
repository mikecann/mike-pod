"""
feed.py — RSS feed generator.
Reads episode metadata and writes a valid podcast RSS 2.0 feed to ~/www/podcast/feed.xml
"""
import json
from datetime import datetime
from pathlib import Path
from email.utils import formatdate
import xml.etree.ElementTree as ET

from config import (
    PODCAST_TITLE, PODCAST_DESCRIPTION, PODCAST_AUTHOR,
    PODCAST_EMAIL, BASE_URL, EPISODES_DIR, WWW_DIR
)

WWW_DIR.mkdir(parents=True, exist_ok=True)

def build_feed():
    episodes = []
    for f in sorted(EPISODES_DIR.glob("*-episode.json"), reverse=True):
        episodes.append(json.loads(f.read_text()))

    rss = ET.Element("rss", version="2.0", attrib={
        "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        "xmlns:content": "http://purl.org/rss/1.0/modules/content/",
    })
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = PODCAST_TITLE
    ET.SubElement(channel, "link").text = BASE_URL
    ET.SubElement(channel, "description").text = PODCAST_DESCRIPTION
    ET.SubElement(channel, "language").text = "en-au"
    ET.SubElement(channel, "itunes:author").text = PODCAST_AUTHOR
    ET.SubElement(channel, "itunes:explicit").text = "false"
    owner = ET.SubElement(channel, "itunes:owner")
    ET.SubElement(owner, "itunes:name").text = PODCAST_AUTHOR
    ET.SubElement(owner, "itunes:email").text = PODCAST_EMAIL

    for ep in episodes:
        mp3_url = f"{BASE_URL}/{ep['filename']}"
        mp3_path = WWW_DIR / ep["filename"]
        size = mp3_path.stat().st_size if mp3_path.exists() else 0

        pub_date = formatdate(datetime.fromisoformat(ep["date"]).timestamp())

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = ep["title"]
        ET.SubElement(item, "description").text = f"This week: {', '.join(ep.get('topics_covered', []))}"
        ET.SubElement(item, "pubDate").text = pub_date
        ET.SubElement(item, "guid", isPermaLink="false").text = ep["filename"]
        ET.SubElement(item, "enclosure",
                       url=mp3_url, length=str(size), type="audio/mpeg")
        ET.SubElement(item, "itunes:duration").text = "00:15:00"

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    out = WWW_DIR / "feed.xml"
    tree.write(out, xml_declaration=True, encoding="utf-8")
    print(f"Feed written to {out} ({len(episodes)} episodes)")

if __name__ == "__main__":
    build_feed()
