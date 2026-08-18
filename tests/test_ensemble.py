import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import ensemble


SMALL_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class EnsembleCliTests(unittest.TestCase):
    def test_claude_uses_fable_alias_and_structured_output(self):
        envelope = {"structured_output": {"ok": True}, "usage": {"input_tokens": 1}}
        completed = CompletedProcess([], 0, stdout=json.dumps(envelope), stderr="")
        with (
            patch("ensemble.shutil.which", return_value="/bin/claude"),
            patch("ensemble.subprocess.run", return_value=completed) as run,
        ):
            result, metadata = ensemble.call_claude_cli("prompt", SMALL_SCHEMA)

        command = run.call_args.args[0]
        self.assertEqual(result, {"ok": True})
        self.assertIn("fable", command)
        self.assertIn("--safe-mode", command)
        self.assertEqual(metadata["model"], "claude-fable-5")

    def test_grok_uses_46_without_memory_or_tools(self):
        envelope = {"structuredOutput": {"ok": True}, "usage": {"input_tokens": 1}}
        completed = CompletedProcess([], 0, stdout=json.dumps(envelope), stderr="")
        with (
            patch("ensemble.shutil.which", return_value="/bin/grok"),
            patch("ensemble.subprocess.run", return_value=completed) as run,
        ):
            result, metadata = ensemble.call_grok_cli("prompt", SMALL_SCHEMA)

        command = run.call_args.args[0]
        self.assertEqual(result, {"ok": True})
        self.assertIn("grok-4.6", command)
        self.assertIn("--no-memory", command)
        self.assertIn("--no-subagents", command)
        self.assertIn("--disable-web-search", command)
        max_turns_index = command.index("--max-turns") + 1
        self.assertEqual(command[max_turns_index], "12")
        self.assertEqual(metadata["client"], "Grok CLI")

    def test_openai_uses_sol_in_read_only_ephemeral_codex(self):
        def run_side_effect(command, **kwargs):
            output_index = command.index("--output-last-message") + 1
            Path(command[output_index]).write_text(json.dumps({"ok": True}))
            return CompletedProcess(command, 0, stdout="", stderr="")

        with (
            patch("ensemble.shutil.which", return_value="/bin/codex"),
            patch("ensemble.subprocess.run", side_effect=run_side_effect) as run,
        ):
            result, metadata = ensemble.call_openai_cli("prompt", SMALL_SCHEMA)

        command = run.call_args.args[0]
        self.assertEqual(result, {"ok": True})
        self.assertIn("gpt-5.6-sol", command)
        self.assertIn("read-only", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual(metadata["client"], "Codex CLI")

    def test_grok_research_enables_only_web_tools(self):
        envelope = {"structuredOutput": {"ok": True}, "usage": {"input_tokens": 1}}
        completed = CompletedProcess([], 0, stdout=json.dumps(envelope), stderr="")
        with (
            patch("ensemble.shutil.which", return_value="/bin/grok"),
            patch("ensemble.subprocess.run", return_value=completed) as run,
        ):
            result, metadata = ensemble.call_grok_research_cli(
                "search prompt", SMALL_SCHEMA
            )

        command = run.call_args.args[0]
        tools_index = command.index("--tools") + 1
        self.assertEqual(result, {"ok": True})
        self.assertEqual(command[tools_index], "web_search,web_fetch")
        self.assertNotIn("--disable-web-search", command)
        self.assertEqual(metadata["tools"], ["web_search", "web_fetch"])

    def test_topic_decision_is_recorded_with_all_panel_members(self):
        candidates = [{"id": "space", "topic": "Space"}]
        recommendations = {
            provider: {
                "ranked_topic_ids": ["space"],
                "recommended_topic_id": "space",
                "recommended_question": "Question?",
                "why_now": "Now",
                "narrative_promise": "Promise",
                "historical_context_angle": "History",
                "risks": [],
                "rejected_as_repetitive": [],
            }
            for provider in ("openai", "claude", "grok")
        }
        decision = {
            "selected_topic_id": "space",
            "selected_question": "Question?",
            "decision_rationale": "Best",
            "listener_promise": "Promise",
            "required_history_and_refutations": ["Earlier work"],
            "research_risks": [],
            "panel_agreements": ["Clear"],
            "panel_disagreements": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "panel"
            with (
                patch(
                    "ensemble.run_panel",
                    return_value=(recommendations, {"all": {"ok": True}}),
                ),
                patch(
                    "ensemble.call_openai_cli",
                    return_value=(decision, {"model": "gpt-5.6-sol"}),
                ),
            ):
                selected = ensemble.select_topic(candidates, [], output_dir=output)

            self.assertEqual(selected["selected_topic_id"], "space")
            self.assertTrue((output / "recommendations.json").exists())
            self.assertTrue((output / "decision.json").exists())


if __name__ == "__main__":
    unittest.main()
