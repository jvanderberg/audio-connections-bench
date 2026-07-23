import argparse
import json
import unittest
from unittest import mock

import audio_bench


PUZZLE = {
    "id": "fixture",
    "day": 7,
    "date": "2026-05-16",
    "releaseAt": "2026-05-16T00:00:00Z",
    "author": "Test",
    "themes": [
        {
            "theme": f"secret theme {group}",
            "tracks": [
                {"id": group * 10 + item, "artist": f"Artist {group}-{item}",
                 "title": f"Title {group}-{item}", "note": "secret note"}
                for item in range(4)
            ],
        }
        for group in range(4)
    ],
}


class BoardTests(unittest.TestCase):
    def test_board_is_deterministic_and_not_source_grouped(self):
        first, answers = audio_bench.board(PUZZLE)
        second, _ = audio_bench.board(PUZZLE)
        self.assertEqual(first, second)
        source_groups = [clue["artist"].split()[1].split("-")[0] for clue in first]
        self.assertNotEqual(source_groups, ["0"] * 4 + ["1"] * 4 + ["2"] * 4 + ["3"] * 4)
        self.assertEqual(sorted(map(len, answers)), [4, 4, 4, 4])

    def test_prompt_does_not_leak_themes_ids_or_notes(self):
        prompt = audio_bench.make_prompt(PUZZLE)
        self.assertNotIn("secret theme", prompt)
        self.assertNotIn("secret note", prompt)
        self.assertNotIn('"id"', prompt)
        self.assertIn("Artist 0-0 — Title 0-0", prompt)


class GradeTests(unittest.TestCase):
    def test_exact_groups_solve_regardless_of_theme_text(self):
        _, answers = audio_bench.board(PUZZLE)
        groups = [
            {"theme": "model label", "tracks": sorted(group)} for group in answers
        ]
        result = audio_bench.grade(json.dumps({"groups": groups}), answers)
        self.assertTrue(result["valid"])
        self.assertTrue(result["solved"])
        self.assertEqual(result["correct_groups"], 4)

    def test_duplicate_number_is_invalid(self):
        _, answers = audio_bench.board(PUZZLE)
        groups = [{"theme": "x", "tracks": [1, 2, 3, 4]}] * 4
        result = audio_bench.grade(json.dumps({"groups": groups}), answers)
        self.assertFalse(result["valid"])
        self.assertFalse(result["solved"])

    def test_json_extractor_handles_braces_inside_strings(self):
        value = audio_bench.extract_json('prefix {"groups": [], "theme": "a } b"} suffix')
        self.assertEqual(value["theme"], "a } b")


class SelectionTests(unittest.TestCase):
    def test_future_puzzle_requires_explicit_override(self):
        catalog = {"puzzles": [{**PUZZLE, "date": "2999-01-01",
                                 "releaseAt": "2999-01-01T00:00:00Z"}]}
        args = argparse.Namespace(day=7, date=None, start=None, end=None,
                                  allow_unreleased=False)
        with self.assertRaisesRegex(ValueError, "unreleased"):
            audio_bench.select_puzzles(catalog, args)
        args.allow_unreleased = True
        self.assertEqual(audio_bench.select_puzzles(catalog, args)[0]["day"], 7)


class AttemptTests(unittest.TestCase):
    def test_runner_receives_only_spoiler_safe_prompt(self):
        _, answers = audio_bench.board(PUZZLE)
        response = json.dumps({"groups": [
            {"theme": "x", "tracks": sorted(group)} for group in answers
        ]})
        observed = {}

        def runner(prompt, model, effort, timeout):
            observed["prompt"] = prompt
            return {
                "text": response, "tokens_in": 1, "tokens_in_cached": 0,
                "tokens_out": 1, "tokens_reasoning": 0, "cost_usd": None,
                "model_used": model,
            }

        with mock.patch.dict(audio_bench.RUNNERS, {"codex": runner}):
            run = audio_bench.attempt(PUZZLE, "codex:test@high", 5)
        self.assertTrue(run["solved"])
        self.assertNotIn("secret theme", observed["prompt"])


if __name__ == "__main__":
    unittest.main()
