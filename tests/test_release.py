import json
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

import artwork
import episode
import feed
from audio_note import AudioNoteError
from deep_dive import rejected_review_details
from episode import (
    IDENTITY_FILENAME,
    audit_prompt,
    correction_prompt,
    correction_versions,
    episode_names,
    incomplete_correction_version,
    make_episode_identity,
    resume_audit_path,
    validate_audit,
    validate_episode_artwork,
    validate_episode_identity,
    validate_package,
    writer_prompt,
)
from publish import cache_control, content_type, upload_and_verify, wrangler


class EpisodeGateTests(unittest.TestCase):
    def write_dossier(self, root: Path, label: str) -> Path:
        dossier_dir = root / label
        dossier_dir.mkdir()
        (dossier_dir / "dossier.json").write_text(json.dumps({"label": label}))
        (dossier_dir / "review.json").write_text(
            json.dumps({"approved_for_script": True})
        )
        (dossier_dir / "source_manifest.json").write_text("[]")
        return dossier_dir

    def test_writer_prompt_starts_at_a_generalist_altitude(self):
        prompt = writer_prompt({}, {}, [])

        self.assertIn("no prior knowledge", prompt)
        self.assertIn("why anyone should care", prompt)
        self.assertIn('strict "so what?" test', prompt)
        self.assertIn("first 200 spoken words", prompt)
        self.assertIn("before any analogy", prompt)
        self.assertIn("does not count as motivation", prompt)
        self.assertIn("without narrating its lab notebook", prompt)
        self.assertIn("before specialist vocabulary", prompt)
        self.assertIn("where each analogy breaks", prompt)
        self.assertIn("three most useful ideas", prompt)
        self.assertIn("two exact measurements", prompt)
        self.assertIn("one bounded analogy", prompt)
        self.assertIn("does not catalogue secondary caveats", audit_prompt({}, {}, [], {}))

    def test_auditor_rejects_weeds_first_scripts(self):
        prompt = audit_prompt({}, {}, [], {})
        compact_prompt = " ".join(prompt.split())

        self.assertIn("problem, stakes and real-world significance", compact_prompt)
        self.assertIn("zero prior subject-matter knowledge", compact_prompt)
        self.assertIn("weeds-first opening", compact_prompt)
        self.assertIn("individually understandable", compact_prompt)
        self.assertIn("first 200 spoken words", compact_prompt)
        self.assertIn("Reject an opening analogy", compact_prompt)
        self.assertIn("more time on lab implementation", compact_prompt)

    def test_correction_prompt_preserves_high_level_framing(self):
        prompt = correction_prompt({}, {}, [], {}, {})
        compact_prompt = " ".join(prompt.split())

        self.assertIn(
            "establish the problem and stakes before the mechanism",
            compact_prompt,
        )
        self.assertIn('apply the "so what?" test', compact_prompt)
        self.assertIn("four opening questions", compact_prompt)
        self.assertIn("prefer qualitative consequence", compact_prompt)
        self.assertIn("rewrite it from a clean high-level outline", compact_prompt)
        self.assertIn("Delete technical material aggressively", compact_prompt)
        self.assertIn("apply only the audit's targeted", compact_prompt)
        self.assertIn("Do not restructure the episode", compact_prompt)

    def test_later_episode_names_do_not_reuse_episode_one(self):
        names = episode_names(2, "quantum-reality")

        self.assertEqual(names["guid"], "mike-pod-episode-002")
        self.assertEqual(names["audio"], "episode-002-quantum-reality.mp3")
        self.assertEqual(names["public_audio"], "mike-pod-episode-002.mp3")

    def test_revision_uses_a_new_immutable_identity(self):
        names = episode_names(2, "quantum-reality", revision=2)

        self.assertEqual(names["guid"], "mike-pod-episode-002-r2")
        self.assertEqual(names["audio"], "episode-002-r2-quantum-reality.mp3")
        self.assertEqual(names["public_audio"], "mike-pod-episode-002-r2.mp3")
        self.assertEqual(names["public_artwork"], "mike-pod-episode-002-r2.jpg")

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

    def test_draft_identity_blocks_duplicate_episode_number(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            releases = root / "releases"
            releases.mkdir()
            dossier = self.write_dossier(root, "dossier")
            first_release = releases / "episode-002-first"
            first_release.mkdir()
            first_identity = make_episode_identity(2, "first", dossier)
            (first_release / IDENTITY_FILENAME).write_text(json.dumps(first_identity))
            second_identity = make_episode_identity(2, "second", dossier)

            with patch.object(episode, "RELEASES_DIR", releases):
                with self.assertRaisesRegex(AudioNoteError, "conflicts"):
                    validate_episode_identity(
                        releases / "episode-002-second",
                        second_identity,
                        resume=False,
                    )

    def test_revision_requires_the_previous_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            releases = root / "releases"
            releases.mkdir()
            dossier = self.write_dossier(root, "dossier")
            second_revision = make_episode_identity(2, "second", dossier, revision=2)

            with (
                patch.object(episode, "RELEASES_DIR", releases),
                self.assertRaisesRegex(AudioNoteError, "requires revision 1"),
            ):
                validate_episode_identity(
                    releases / "episode-002-r2-second",
                    second_revision,
                    resume=False,
                )

    def test_output_directory_rejects_a_different_episode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            releases = root / "releases"
            releases.mkdir()
            dossier = self.write_dossier(root, "dossier")
            release = releases / "shared-output"
            release.mkdir()
            first_identity = make_episode_identity(1, "first", dossier)
            (release / IDENTITY_FILENAME).write_text(json.dumps(first_identity))
            second_identity = make_episode_identity(2, "second", dossier)

            with patch.object(episode, "RELEASES_DIR", releases):
                with self.assertRaisesRegex(AudioNoteError, "different episode"):
                    validate_episode_identity(release, second_identity, resume=True)

    def test_resume_rejects_a_different_dossier_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            releases = root / "releases"
            releases.mkdir()
            first_dossier = self.write_dossier(root, "first-dossier")
            second_dossier = self.write_dossier(root, "second-dossier")
            release = releases / "episode-002"
            release.mkdir()
            first_identity = make_episode_identity(2, "quantum", first_dossier)
            (release / IDENTITY_FILENAME).write_text(json.dumps(first_identity))
            second_identity = make_episode_identity(2, "quantum", second_dossier)

            with patch.object(episode, "RELEASES_DIR", releases):
                with self.assertRaisesRegex(
                    AudioNoteError,
                    "different episode or dossier",
                ):
                    validate_episode_identity(release, second_identity, resume=True)

    def test_marker_only_release_can_restart_the_first_draft(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            releases = root / "releases"
            releases.mkdir()
            dossier = self.write_dossier(root, "dossier")
            release = releases / "episode-002"
            release.mkdir()
            identity = make_episode_identity(2, "quantum", dossier)
            (release / IDENTITY_FILENAME).write_text(json.dumps(identity))

            with patch.object(episode, "RELEASES_DIR", releases):
                validate_episode_identity(release, identity, resume=False)

    def test_episode_artwork_must_be_3000_square_jpeg(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / "valid.jpg"
            wrong_format = root / "wrong.png"
            wrong_size = root / "small.jpg"
            Image.new("RGB", (3000, 3000)).save(valid, format="JPEG")
            Image.new("RGB", (3000, 3000)).save(wrong_format, format="PNG")
            Image.new("RGB", (100, 100)).save(wrong_size, format="JPEG")

            validate_episode_artwork(valid)
            with self.assertRaisesRegex(AudioNoteError, "must be JPEG"):
                validate_episode_artwork(wrong_format)
            with self.assertRaisesRegex(AudioNoteError, "must be 3000 by 3000"):
                validate_episode_artwork(wrong_size)

    def test_resumed_correction_versions_continue_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            (release / "correction_usage_v1.json").write_text("{}")
            (release / "correction_usage_v3.json").write_text("{}")
            (release / "correction_usage_latest.json").write_text("{}")

            self.assertEqual(correction_versions(release), [1, 3])

    def test_interrupted_correction_keeps_the_missing_audit_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            (release / "correction_usage_v2.json").write_text("{}")

            self.assertEqual(incomplete_correction_version(release), 2)
            self.assertFalse((release / "audit_v3.json").exists())

    def test_first_interrupted_correction_resumes_from_versioned_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            first_audit = release / "audit_v1.json"
            first_audit.write_text("{}")

            self.assertEqual(resume_audit_path(release), first_audit)

    def test_rejected_review_reports_every_severity(self):
        details = rejected_review_details(
            {
                "issues": [
                    {"severity": "important", "detail": "Missing comparison"},
                    {"severity": "minor", "detail": "Clarify wording"},
                ]
            }
        )

        self.assertIn("important: Missing comparison", details)
        self.assertIn("minor: Clarify wording", details)


class ArtworkTests(unittest.TestCase):
    def test_custom_options_require_an_episode_base(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(
                sys,
                "argv",
                ["artwork.py", "--output-dir", temporary, "--episode-number", "2"],
            ):
                with self.assertRaises(SystemExit):
                    artwork.main()

    def test_custom_render_errors_are_reported_as_cli_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            argv = [
                "artwork.py",
                "--output-dir",
                temporary,
                "--episode-base",
                "base.png",
                "--episode-number",
                "2",
                "--episode-title-line",
                "TITLE",
                "--episode-question",
                "QUESTION",
                "--episode-slug",
                "episode",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    artwork,
                    "finish_episode_art",
                    side_effect=RuntimeError("does not fit"),
                ),
            ):
                with self.assertRaises(SystemExit):
                    artwork.main()

    def test_fitted_font_rejects_text_that_cannot_fit(self):
        image = Image.new("RGB", (100, 100))
        draw = ImageDraw.Draw(image)

        with (
            patch.object(artwork, "font", return_value=ImageFont.load_default()),
            self.assertRaisesRegex(RuntimeError, "does not fit"),
        ):
            artwork.fitted_font(
                draw,
                "THIS CANNOT FIT",
                Path("portable-test-font"),
                max_size=42,
                min_size=42,
                max_width=1,
            )


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

    def test_feed_loads_only_the_highest_published_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            releases = Path(temporary)
            for revision in (1, 2):
                release = releases / f"episode-002-r{revision}"
                release.mkdir()
                (release / "episode.json").write_text(
                    json.dumps(
                        {
                            "episode": 2,
                            "revision": revision,
                            "published": True,
                            "published_at": f"2026-08-{revision:02d}T00:00:00+00:00",
                        }
                    )
                )

            with patch.object(feed, "RELEASES_DIR", releases):
                loaded = feed.load_releases()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0][1]["revision"], 2)

    def test_public_bundle_prunes_superseded_revision_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            release = root / "release"
            output.joinpath("artwork").mkdir(parents=True)
            output.joinpath("episodes").mkdir()
            release.mkdir()
            output.joinpath("artwork", "old.jpg").write_bytes(b"old")
            output.joinpath("episodes", "old.mp3").write_bytes(b"old")
            release.joinpath("audio.mp3").write_bytes(b"new audio")
            release.joinpath("art.jpg").write_bytes(b"new art")
            episode_metadata = {
                "guid": "mike-pod-episode-002-r2",
                "audio_filename": "audio.mp3",
                "episode_artwork_filename": "art.jpg",
                "public_audio_filename": "new.mp3",
                "public_artwork_filename": "new.jpg",
                "audio_bytes": 9,
            }

            with patch.object(feed, "SHOW_ARTWORK_SOURCE", release / "art.jpg"):
                feed.prepare_public_files(output, [(release, episode_metadata)])

            self.assertFalse(output.joinpath("artwork", "old.jpg").exists())
            self.assertFalse(output.joinpath("episodes", "old.mp3").exists())
            self.assertTrue(output.joinpath("artwork", "new.jpg").exists())
            self.assertTrue(output.joinpath("episodes", "new.mp3").exists())


class PublisherTests(unittest.TestCase):
    def test_publisher_uses_installed_authenticated_wrangler(self):
        with patch("publish.subprocess.run") as run:
            wrangler(["r2", "object", "get", "bucket/key"])

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["wrangler", "r2", "object", "get"])

    def test_podcast_media_is_immutable_but_feed_is_short_lived(self):
        self.assertIn("immutable", cache_control("episodes/episode.mp3"))
        self.assertIn("max-age=300", cache_control("feed.xml"))

    def test_rss_content_type_is_explicit(self):
        self.assertEqual(
            content_type(Path("feed.xml")),
            "application/rss+xml; charset=utf-8",
        )

    def test_new_episode_is_not_publicly_probed_before_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "episode.mp3"
            source.write_bytes(b"approved audio")
            remote_calls = 0

            def remote_copy_side_effect(bucket, key, destination):
                nonlocal remote_calls
                remote_calls += 1
                if remote_calls == 1:
                    return False
                shutil.copy2(source, destination)
                return True

            with (
                patch("publish.remote_copy", side_effect=remote_copy_side_effect),
                patch("publish.upload") as upload,
                patch("publish.public_copy") as public_copy,
            ):
                upload_and_verify(
                    "bucket",
                    "episodes/new.mp3",
                    source,
                    root,
                    "https://podcast.example",
                )

            upload.assert_called_once()
            public_copy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
