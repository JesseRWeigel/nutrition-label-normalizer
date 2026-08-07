"""Unit conversion, the salt factor, and the OCR confidence gate."""

from __future__ import annotations

import unittest
from fractions import Fraction
from pathlib import Path

from nutrilabel.ocr import DEFAULT_MIN_CONFIDENCE, read_tsv
from nutrilabel.label import Label
from nutrilabel.units import convert, kcal_to_kj, kj_to_kcal, salt_to_sodium, sodium_to_salt

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


class TestUnits(unittest.TestCase):
    def test_mass_conversions_are_exact(self):
        self.assertEqual(convert(1, "g", "mg"), Fraction(1000))
        self.assertEqual(convert(2300, "mg", "g"), Fraction(23, 10))
        self.assertEqual(convert(1, "mg", "mcg"), Fraction(1000))
        self.assertEqual(convert("0.1", "g", "mg"), Fraction(100))

    def test_micrograms_accept_every_spelling(self):
        for spelling in ("mcg", "ug", "µg", "μg", "micrograms"):
            self.assertEqual(convert(1000, spelling, "mg"), Fraction(1), spelling)

    def test_salt_is_sodium_times_two_and_a_half(self):
        self.assertEqual(sodium_to_salt(Fraction(4, 10)), Fraction(1))
        self.assertEqual(salt_to_sodium(Fraction(1)), Fraction(4, 10))

    def test_a_us_sodium_figure_becomes_the_eu_salt_figure(self):
        # 160 mg of sodium is 0.4 g of salt, which is what the EU panel prints.
        self.assertEqual(sodium_to_salt(convert(160, "mg", "g")), Fraction(4, 10))

    def test_physical_energy_conversion_uses_the_thermochemical_calorie(self):
        self.assertEqual(kcal_to_kj(1), Fraction(4184, 1000))
        self.assertEqual(kj_to_kcal(Fraction(4184, 1000)), Fraction(1))

    def test_cross_dimension_conversion_is_refused(self):
        with self.assertRaises(ValueError):
            convert(1, "g", "ml")

    def test_unknown_unit_is_refused_rather_than_assumed(self):
        with self.assertRaises(ValueError):
            convert(1, "handfuls", "g")


class TestOcrConfidenceGate(unittest.TestCase):
    def test_a_clean_panel_reads_completely(self):
        read = read_tsv(FIXTURES / "ocr" / "clean-panel.tsv")
        self.assertEqual(read.refused, [])
        keys = {row.key for row in read.accepted}
        for expected in ("energy_kcal", "fat", "sodium", "carbohydrate", "protein",
                         "serving_size", "servings_per_container"):
            self.assertIn(expected, keys)

    def test_a_clean_panel_becomes_a_usable_label(self):
        read = read_tsv(FIXTURES / "ocr" / "clean-panel.tsv")
        label = Label.from_dict(read.to_label_dict("scanned granola"))
        self.assertEqual(label.nutrients["sodium"].declaration.amount, Fraction(160))
        self.assertEqual(label.serving.amount, Fraction(55))
        self.assertEqual(label.servings_per_container.amount, Fraction(8))

    def test_low_confidence_rows_are_refused_not_guessed(self):
        read = read_tsv(FIXTURES / "ocr" / "smudged-panel.tsv")
        refused = {row.key for row in read.refused}
        self.assertIn("protein", refused)
        self.assertIn("fiber", refused)

    def test_an_ambiguous_glyph_is_refused_even_at_high_confidence(self):
        """The point of the module. The engine was 95 percent sure it saw "16Omg",
        and a capital O in a number is not a reading the tool may repair."""
        read = read_tsv(FIXTURES / "ocr" / "smudged-panel.tsv")
        sodium = next(row for row in read.rows if row.key == "sodium")
        self.assertFalse(sodium.accepted)
        self.assertGreater(sodium.confidence, DEFAULT_MIN_CONFIDENCE)
        self.assertIn("confuses with a digit", sodium.reason)

    def test_refused_rows_are_absent_rather_than_filled_in(self):
        read = read_tsv(FIXTURES / "ocr" / "smudged-panel.tsv")
        data = read.to_label_dict("smudged")
        self.assertNotIn("sodium", data["nutrients"])
        self.assertNotIn("protein", data["nutrients"])
        self.assertIn("carbohydrate", data["nutrients"])

    def test_raising_the_threshold_refuses_more(self):
        lenient = read_tsv(FIXTURES / "ocr" / "smudged-panel.tsv", min_confidence=60)
        strict = read_tsv(FIXTURES / "ocr" / "smudged-panel.tsv", min_confidence=95)
        self.assertLess(len(lenient.refused), len(strict.refused))

    def test_a_file_that_is_not_tsv_raises_with_an_actionable_message(self):
        bad = ROOT / "fixtures" / "labels" / "fda-sample-panel.json"
        with self.assertRaises(ValueError) as caught:
            read_tsv(bad)
        self.assertIn("tesseract", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
