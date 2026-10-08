import unittest
from unittest.mock import patch

from audio_note import (
    ReadableHTMLParser,
    fetch_live_article,
    sample_excerpt,
    slugify,
    validate_claim_audit,
    validate_package,
)


class _Headers:
    def get_content_charset(self):
        return "utf-8"

    def get_content_type(self):
        return "application/xml"


class _Response:
    headers = _Headers()

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return self.payload


class ReadableHTMLParserTests(unittest.TestCase):
    def test_skips_navigation_and_scripts(self):
        parser = ReadableHTMLParser()
        parser.feed(
            """
            <html>
              <header>Site chrome</header>
              <main><h1>Useful title</h1><p>Useful body.</p></main>
              <script>ignoreMe()</script>
              <footer>Copyright</footer>
            </html>
            """
        )

        self.assertIn("Useful title", parser.text())
        self.assertIn("Useful body.", parser.text())
        self.assertNotIn("Site chrome", parser.text())
        self.assertNotIn("ignoreMe", parser.text())
        self.assertNotIn("Copyright", parser.text())

    @patch("audio_note.urlopen")
    def test_pmc_articles_use_europe_pmc_full_text_xml(self, mock_urlopen):
        body = " ".join(["Primary result and controls."] * 30)
        mock_urlopen.return_value = _Response(
            f"<?xml version='1.0'?><article><body><p>{body}</p></body></article>".encode()
        )

        text = fetch_live_article(
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC8670470/"
        )

        requested = mock_urlopen.call_args.args[0]
        self.assertEqual(
            requested.full_url,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC8670470/fullTextXML",
        )
        self.assertIn("Primary result and controls.", text)
        self.assertNotIn("<article>", text)


class PackageValidationTests(unittest.TestCase):
    def valid_package(self):
        sentence = "This sentence is grounded in the source."
        script = " ".join([sentence] * 80)
        return {
            "episode_title": "A useful title",
            "source_summary": "Summary",
            "answer_to_note": "Answer",
            "why_it_matters": "Why",
            "what_source_does_not_prove": "Caveat",
            "what_to_try": "Try",
            "claims": [
                {
                    "claim": "The source contains a grounded sentence.",
                    "evidence": sentence,
                    "confidence": "high",
                    "attribution": "the source",
                }
            ],
            "script": script,
        }

    def test_accepts_grounded_evidence(self):
        package = self.valid_package()
        errors = validate_package(package, package["claims"][0]["evidence"])
        self.assertEqual(errors, [])

    def test_rejects_invented_evidence(self):
        package = self.valid_package()
        errors = validate_package(package, "A completely different source.")
        self.assertTrue(any("not an exact source excerpt" in error for error in errors))

    def test_rejects_fake_host_format(self):
        package = self.valid_package()
        package["script"] += " <Person1> Now over to you."
        errors = validate_package(package, package["claims"][0]["evidence"])
        self.assertTrue(any("banned marker" in error for error in errors))


class HelpersTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Bun in Rust: Why?"), "bun-in-rust-why")

    def test_sample_excerpt_stops_on_sentence_boundary(self):
        script = "First sentence has five words. Second sentence has another five. Third ends."
        excerpt = sample_excerpt(script, target_words=10)
        self.assertTrue(excerpt.endswith("."))
        self.assertNotIn("Third", excerpt)


class ClaimAuditValidationTests(unittest.TestCase):
    def test_accepts_supported_audit(self):
        audit = {
            "approved": True,
            "supported_claims": [
                {
                    "script_claim": "The source says the result was tested.",
                    "evidence": "the result was tested",
                    "attribution": "the source",
                }
            ],
            "unsupported_claims": [],
            "attribution_issues": [],
        }
        self.assertEqual(
            validate_claim_audit(audit, "The source says the result was tested."),
            [],
        )

    def test_rejects_unsupported_claims(self):
        audit = {
            "approved": False,
            "supported_claims": [],
            "unsupported_claims": ["An invented number"],
            "attribution_issues": [],
        }
        errors = validate_claim_audit(audit, "Source")
        self.assertTrue(any("unsupported script claim" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
