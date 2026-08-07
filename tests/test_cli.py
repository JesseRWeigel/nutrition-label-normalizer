"""The command line surface, including the exit codes other tools would rely on."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

from nutrilabel.cli import main

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "fixtures" / "labels"
OCR = ROOT / "fixtures" / "ocr"


def run(argv) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = main([str(part) for part in argv])
    return code, buffer.getvalue()


class TestExitCodes(unittest.TestCase):
    def test_check_on_a_correct_label_exits_zero(self):
        code, _ = run(["check", LABELS / "fda-sample-panel.json"])
        self.assertEqual(code, 0)

    def test_check_on_a_broken_label_exits_one(self):
        code, output = run(["check", LABELS / "broken-energy-mismatch.json"])
        self.assertEqual(code, 1)
        self.assertIn("INCONSISTENT", output)

    def test_check_over_a_mixed_set_exits_one(self):
        code, _ = run(["check", LABELS / "fda-sample-panel.json",
                       LABELS / "broken-mass-budget.json"])
        self.assertEqual(code, 1)

    def test_ocr_exits_one_when_a_row_was_refused(self):
        code, output = run(["ocr", OCR / "smudged-panel.tsv"])
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", output)

    def test_ocr_exits_zero_on_a_clean_read(self):
        code, _ = run(["ocr", OCR / "clean-panel.tsv"])
        self.assertEqual(code, 0)

    def test_a_broken_label_file_exits_two_rather_than_crashing(self):
        code, output = run(["normalize", LABELS / "does-not-exist.json"])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", output)


class TestOutputAlwaysStatesTheBasis(unittest.TestCase):
    def test_normalize_prints_the_basis_banner(self):
        for basis in ("per_serving", "per_100g", "per_100kcal", "per_container"):
            code, output = run(["normalize", LABELS / "fda-sample-panel.json", "--basis", basis])
            self.assertEqual(code, 0)
            self.assertIn("BASIS IN FORCE:", output)

    def test_compare_prints_the_basis_banner(self):
        code, output = run(["compare", LABELS / "cereal-a-flakes.json",
                            LABELS / "cereal-b-granola.json", "--basis", "per_100g"])
        self.assertEqual(code, 0)
        self.assertIn("BASIS IN FORCE:", output)
        self.assertIn("per 100 g", output)

    def test_json_output_carries_the_basis_on_every_value(self):
        code, output = run(["normalize", LABELS / "fda-sample-panel.json",
                            "--basis", "per_container", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["basis"], "per_container")
        for key, value in payload["values"].items():
            self.assertEqual(value["basis"], "per_container", key)
            self.assertIn("citation", value)


class TestRulesCommand(unittest.TestCase):
    def test_rules_prints_citations_for_both_formats(self):
        for label_format, marker in (("US_2016", "21 CFR"), ("EU_1169", "EC guidance")):
            code, output = run(["rules", "--format", label_format])
            self.assertEqual(code, 0)
            self.assertIn(marker, output)


if __name__ == "__main__":
    unittest.main()
