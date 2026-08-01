import sqlite3
import tempfile
import unittest
from pathlib import Path

from deep_dive import collect_source_candidates, validate_dossier, validate_plan
from personal_context import PersonalContextIndex


class PersonalContextIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "context.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY,
                embedding_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE embedding_metadata (
                id INTEGER NOT NULL,
                key TEXT NOT NULL,
                string_value TEXT,
                int_value INTEGER,
                float_value REAL,
                bool_value INTEGER
            );
            CREATE VIRTUAL TABLE embedding_fulltext_search
            USING fts5(string_value);
            """
        )
        connection.execute(
            "INSERT INTO embeddings VALUES (1, 'stash_item_1', '2026-04-01')"
        )
        connection.execute(
            """
            INSERT INTO embedding_fulltext_search(rowid, string_value)
            VALUES (1, 'Wolfram physics causal invariance and hypergraphs')
            """
        )
        connection.executemany(
            """
            INSERT INTO embedding_metadata(id, key, string_value)
            VALUES (1, ?, ?)
            """,
            [
                ("source", "stashit"),
                ("title", "Causal invariance"),
                ("url", "https://example.com/causal-invariance"),
                ("note", "One of my favourite ideas"),
            ],
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_labels_stashit_as_saved_evidence(self):
        results = PersonalContextIndex(self.database).search("Wolfram physics")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["signal_kind"], "saved_or_commented_on_by_mike")
        self.assertEqual(results[0]["mike_note"], "One of my favourite ideas")

    def test_status_is_explicitly_historical(self):
        status = PersonalContextIndex(self.database).status().as_dict()

        self.assertFalse(status["live"])
        self.assertIn("private listening history", status["warning"])


class DeepDiveValidationTests(unittest.TestCase):
    def setUp(self):
        self.personal = [{"evidence_id": "P01"}]
        self.plan = {
            "branches": [
                {
                    "branch_id": f"B{index:02d}",
                    "personal_evidence_ids": ["P01"] if index == 1 else [],
                    "search_queries": ["query one", "query two"],
                }
                for index in range(1, 5)
            ]
        }

    def test_plan_rejects_invented_personal_evidence(self):
        self.plan["branches"][0]["personal_evidence_ids"] = ["P99"]

        errors = validate_plan(self.plan, self.personal)

        self.assertTrue(any("unknown personal evidence" in error for error in errors))

    def test_dossier_rejects_unknown_source(self):
        dossier = {
            "why_mike_might_care": [
                {"statement": "Relevant", "personal_evidence_ids": ["P01"]}
            ],
            "branch_summaries": [
                {"branch_id": f"B{index:02d}", "source_ids": ["S99"]}
                for index in range(1, 5)
            ],
            "cross_branch_connections": [],
            "important_disagreements": [],
        }
        sources = [{"source_id": "S01", "usable_for_synthesis": True}]

        errors = validate_dossier(
            dossier,
            plan=self.plan,
            personal_context=self.personal,
            sources=sources,
        )

        self.assertTrue(any("unknown or unusable source" in error for error in errors))

    def test_collects_only_real_http_sources(self):
        discovery = {
            "branch_results": [
                {
                    "branch_id": "B01",
                    "candidate_sources": [
                        {
                            "title": "Paper",
                            "url": "https://example.com/paper#results",
                            "source_type": "paper",
                            "stance": "challenges",
                            "why_relevant": "Direct test",
                        },
                        {
                            "title": "Invented",
                            "url": "not a URL",
                            "source_type": "commentary",
                            "stance": "context",
                            "why_relevant": "No",
                        },
                    ],
                }
            ]
        }
        annotations = [
            {
                "type": "url_citation",
                "url_citation": {
                    "url": "https://example.com/paper#other",
                    "title": "Paper",
                    "content": "Search highlight",
                },
            }
        ]

        candidates = collect_source_candidates(discovery, annotations)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://example.com/paper")
        self.assertTrue(candidates[0]["present_in_openrouter_annotations"])


if __name__ == "__main__":
    unittest.main()
