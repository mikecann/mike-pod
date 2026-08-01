#!/usr/bin/env python3
"""Build the public Mike Pod RSS bundle from approved local release packages."""

from __future__ import annotations

import argparse
import json
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
RELEASES_DIR = BASE_DIR / "data" / "releases"
DEFAULT_OUTPUT_DIR = BASE_DIR / "dist" / "podcast"

PODCAST_TITLE = "Mike Pod"
PODCAST_DESCRIPTION = (
    "Deep research for a curious mind. Mike Pod follows interesting questions "
    "through science, computation, technology, biology, history and space, "
    "testing exciting ideas against the strongest evidence we can find."
)
PODCAST_AUTHOR = "Mike Cann"
PODCAST_EMAIL = "mike.cann@gmail.com"
PODCAST_SITE = "https://mikecann.blog"
PUBLIC_BASE_URL = "https://podcast.mikecann.app"
SHOW_ARTWORK_SOURCE = (
    BASE_DIR / "assets" / "artwork" / "final" / "mike-pod-show-artwork-3000.jpg"
)

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("itunes", ITUNES_NS)
ET.register_namespace("content", CONTENT_NS)
ET.register_namespace("atom", ATOM_NS)


class FeedError(RuntimeError):
    """A concise feed build failure."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise FeedError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FeedError(f"{path} is not a JSON object")
    return value


def qname(namespace: str, name: str) -> ET.QName:
    return ET.QName(namespace, name)


def load_releases() -> list[tuple[Path, dict[str, Any]]]:
    releases: list[tuple[Path, dict[str, Any]]] = []
    for metadata_path in RELEASES_DIR.glob("*/episode.json"):
        metadata = read_json(metadata_path)
        if metadata.get("published") is True:
            releases.append((metadata_path.parent, metadata))
    releases.sort(key=lambda item: item[1]["published_at"], reverse=True)
    return releases


def prepare_public_files(
    output_dir: Path,
    releases: list[tuple[Path, dict[str, Any]]],
) -> None:
    artwork_dir = output_dir / "artwork"
    episode_dir = output_dir / "episodes"
    artwork_dir.mkdir(parents=True, exist_ok=True)
    episode_dir.mkdir(parents=True, exist_ok=True)

    if not SHOW_ARTWORK_SOURCE.exists():
        raise FeedError(f"Show artwork is missing: {SHOW_ARTWORK_SOURCE}")
    shutil.copy2(
        SHOW_ARTWORK_SOURCE,
        artwork_dir / "mike-pod-show-artwork.jpg",
    )

    for release_dir, episode in releases:
        audio_source = release_dir / episode["audio_filename"]
        artwork_source = release_dir / episode["episode_artwork_filename"]
        if not audio_source.exists():
            raise FeedError(f"Episode audio is missing: {audio_source}")
        if not artwork_source.exists():
            raise FeedError(f"Episode artwork is missing: {artwork_source}")

        public_audio = episode["public_audio_filename"]
        public_artwork = episode["public_artwork_filename"]
        shutil.copy2(audio_source, episode_dir / public_audio)
        shutil.copy2(artwork_source, artwork_dir / public_artwork)

        expected_bytes = episode.get("audio_bytes")
        actual_bytes = (episode_dir / public_audio).stat().st_size
        if expected_bytes != actual_bytes:
            raise FeedError(
                f"{episode['guid']} audio size changed: "
                f"metadata={expected_bytes}, actual={actual_bytes}"
            )


def build_feed_xml(
    releases: list[tuple[Path, dict[str, Any]]],
) -> ET.ElementTree:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = PODCAST_TITLE
    ET.SubElement(channel, "link").text = PODCAST_SITE
    ET.SubElement(channel, "description").text = PODCAST_DESCRIPTION
    ET.SubElement(channel, "language").text = "en-au"
    ET.SubElement(channel, "copyright").text = f"Copyright {PODCAST_AUTHOR}"
    ET.SubElement(
        channel,
        qname(ATOM_NS, "link"),
        {
            "href": f"{PUBLIC_BASE_URL}/feed.xml",
            "rel": "self",
            "type": "application/rss+xml",
        },
    )
    ET.SubElement(channel, qname(ITUNES_NS, "author")).text = PODCAST_AUTHOR
    ET.SubElement(channel, qname(ITUNES_NS, "summary")).text = PODCAST_DESCRIPTION
    ET.SubElement(channel, qname(ITUNES_NS, "explicit")).text = "false"
    ET.SubElement(channel, qname(ITUNES_NS, "type")).text = "episodic"
    # Keep this migration marker while the former .blog custom domain remains
    # attached to the same bucket. Podcast clients that followed the old feed
    # can then learn the new canonical address without Bruce becoming the host.
    ET.SubElement(channel, qname(ITUNES_NS, "new-feed-url")).text = (
        f"{PUBLIC_BASE_URL}/feed.xml"
    )
    ET.SubElement(
        channel,
        qname(ITUNES_NS, "image"),
        {"href": f"{PUBLIC_BASE_URL}/artwork/mike-pod-show-artwork.jpg"},
    )
    ET.SubElement(channel, qname(ITUNES_NS, "category"), {"text": "Science"})
    ET.SubElement(channel, qname(ITUNES_NS, "category"), {"text": "Technology"})

    owner = ET.SubElement(channel, qname(ITUNES_NS, "owner"))
    ET.SubElement(owner, qname(ITUNES_NS, "name")).text = PODCAST_AUTHOR
    ET.SubElement(owner, qname(ITUNES_NS, "email")).text = PODCAST_EMAIL

    image = ET.SubElement(channel, "image")
    ET.SubElement(image, "url").text = (
        f"{PUBLIC_BASE_URL}/artwork/mike-pod-show-artwork.jpg"
    )
    ET.SubElement(image, "title").text = PODCAST_TITLE
    ET.SubElement(image, "link").text = PODCAST_SITE

    for release_dir, episode in releases:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = episode["title"]
        ET.SubElement(item, "description").text = episode["summary"]
        ET.SubElement(item, qname(CONTENT_NS, "encoded")).text = (
            release_dir / episode["show_notes_html_filename"]
        ).read_text()
        published_at = datetime.fromisoformat(episode["published_at"])
        ET.SubElement(item, "pubDate").text = format_datetime(
            published_at, usegmt=True
        )
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = episode["guid"]
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": (
                    f"{PUBLIC_BASE_URL}/episodes/"
                    f"{episode['public_audio_filename']}"
                ),
                "length": str(episode["audio_bytes"]),
                "type": "audio/mpeg",
            },
        )
        ET.SubElement(item, qname(ITUNES_NS, "author")).text = episode["author"]
        ET.SubElement(item, qname(ITUNES_NS, "summary")).text = episode["summary"]
        ET.SubElement(item, qname(ITUNES_NS, "subtitle")).text = episode["subtitle"]
        ET.SubElement(item, qname(ITUNES_NS, "explicit")).text = (
            "true" if episode["explicit"] else "false"
        )
        ET.SubElement(item, qname(ITUNES_NS, "duration")).text = episode["duration"]
        ET.SubElement(item, qname(ITUNES_NS, "episode")).text = str(
            episode["episode"]
        )
        ET.SubElement(item, qname(ITUNES_NS, "season")).text = str(episode["season"])
        ET.SubElement(item, qname(ITUNES_NS, "episodeType")).text = episode[
            "episode_type"
        ]
        ET.SubElement(
            item,
            qname(ITUNES_NS, "image"),
            {
                "href": (
                    f"{PUBLIC_BASE_URL}/artwork/"
                    f"{episode['public_artwork_filename']}"
                )
            },
        )

    return ET.ElementTree(rss)


def build_feed(output_dir: Path) -> Path:
    releases = load_releases()
    if not releases:
        raise FeedError("No approved published release packages were found")
    prepare_public_files(output_dir, releases)
    feed = build_feed_xml(releases)
    ET.indent(feed, space="  ")
    output_path = output_dir / "feed.xml"
    feed.write(output_path, encoding="utf-8", xml_declaration=True)
    # Read it back so a malformed namespace or write failure never reaches R2.
    ET.parse(output_path)
    print(f"Feed bundle ready: {output_dir} ({len(releases)} episode(s))")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    try:
        build_feed(parse_args().output_dir.resolve())
        return 0
    except FeedError as exc:
        print(f"Feed build failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
