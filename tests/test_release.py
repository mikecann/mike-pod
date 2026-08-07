import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import feed
from episode import episode_names, validate_audit, validate_package, writer_prompt
from publish import cache_control, content_type


class EpisodeGateTests(unittest.TestCase):
    def test_writer_prompt_assumes_no_physics_training(self):
        prompt = writer_prompt({}, {}, [])

        self.assertIn("not a physicist", prompt)
        self.assertIn("where each analogy breaks", prompt)
        self.assertIn("three or four most useful ideas", prompt)

    def test_later_episode_names_do_not_reuse_episode_one(self):
        names = episode_names(2, "quantum-reality")

        self.assertEqual(names["guid"], "mike-pod-episode-002")
        self.assertEqual(names["audio"], "episode-002-quantum-reality.mp3")
        self.assertEqual(names["public_audio"], "mike-pod-episode-002.mp3")

    def test_rejects_internal_source_ids_in_spoken_script(self):
        package = {
            "script": " ".join(["A supported point from S09."] * 400),
            "sections": [{"source_ids": ["S09"]}],
            "featured_source_ids": ["S09"],
        }

        errors = validate_package(package, {"S09"})

        self.assertTrue(any("speaks an internal source ID" in error for error in errors))

    def test_audit_must_have_empty_issue_arrays(self):
        audit = {
            "approved": True,
            "factual_issues": ["One problem"],
            "calibration_issues": [],
            "personalisation_issues": [],
            "accessibility_issues": [],
            "required_edits": [],
        }

        errors = validate_audit(audit)

        self.assertTrue(any("factual_issues" in error for error in errors))

    def test_audit_rejects_accessibility_issues(self):
        audit = {
            "approved": True,
            "factual_issues": [],
            "calibration_issues": [],
            "personalisation_issues": [],
            "accessibility_issues": ["Uses unexplained quantum jargon"],
            "required_edits": [],
        }

        errors = validate_audit(audit)

        self.assertTrue(any("accessibility_issues" in error for error in errors))


class FeedTests(unittest.TestCase):
    def test_feed_uses_stable_guid_and_exact_enclosure_length(self):
        with tempfile.TemporaryDirectory() as temporary:
            release_dir = Path(temporary)
            (release_dir / "show_notes.html").write_text("<p>Notes</p>")
            episode = {
                "title": "Episode title",
                "summary": "Summary",
                "published_at": "2026-07-31T08:00:00+00:00",
                "guid": "mike-pod-episode-001",
                "public_audio_filename": "mike-pod-episode-001.mp3",
                "audio_bytes": 12345,
                "author": "Mike Cann",
                "subtitle": "Subtitle",
                "explicit": False,
                "duration": "00:10:00",
                "episode": 1,
                "season": 1,
                "episode_type": "full",
                "public_artwork_filename": "mike-pod-episode-001.jpg",
                "show_notes_html_filename": "show_notes.html",
            }

            tree = feed.build_feed_xml([(release_dir, episode)])
            xml = ET.tostring(tree.getroot(), encoding="unicode")

            self.assertIn("mike-pod-episode-001", xml)
            self.assertIn('length="12345"', xml)
            self.assertIn("https://podcast.mikecann.app/episodes/", xml)
            self.assertIn("https://podcast.mikecann.app/feed.xml", xml)


class PublisherTests(unittest.TestCase):
    def test_podcast_media_is_immutable_but_feed_is_short_lived(self):
        self.assertIn("immutable", cache_control("episodes/episode.mp3"))
        self.assertIn("max-age=300", cache_control("feed.xml"))

    def test_rss_content_type_is_explicit(self):
        self.assertEqual(
            content_type(Path("feed.xml")),
            "application/rss+xml; charset=utf-8",
        )


if __name__ == "__main__":
    unittest.main()
