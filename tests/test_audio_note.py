import unittest

from audio_note import (
    ReadableHTMLParser,
    sample_excerpt,
    slugify,
    validate_claim_audit,
    validate_package,
)


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
