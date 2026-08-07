"""Consistency checks, positive and negative controls together.

The negative controls carry more weight than the positive ones. Any checker flags a
label built to be broken; only a checker that understands the rounding rules stays
quiet on eleven correct labels, three of which are EU panels whose conventions differ
from the US ones in ways that a careless implementation gets wrong.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from nutrilabel.checks import FLAG, NOTE, run_all
from nutrilabel.energy import check_energy, kj_kcal_ratio_note
from nutrilabel.label import Label

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected" / "flags.json").read_text("utf-8"))


def load(stem: str) -> Label:
    return Label.load(FIXTURES / "labels" / f"{stem}.json")


class TestFixtureCorpusIsWellFormed(unittest.TestCase):
    def test_every_fixture_has_an_expectation(self):
        on_disk = {path.stem for path in (FIXTURES / "labels").glob("*.json")}
        declared = set(EXPECTED["expected"])
        self.assertEqual(
            on_disk, declared,
            "every label fixture must appear in expected/flags.json. Added without an "
            "expectation: %s. Expected but missing: %s"
            % (sorted(on_disk - declared), sorted(declared - on_disk)),
        )

    def test_there_are_enough_negative_controls(self):
        clean = [name for name, codes in EXPECTED["expected"].items() if not codes]
        self.assertGreaterEqual(
            len(clean), 8,
            "a suite built mostly from broken labels cannot tell a real checker from "
            "one that flags everything",
        )

    def test_negative_controls_cover_both_formats(self):
        clean = [name for name, codes in EXPECTED["expected"].items() if not codes]
        formats = {load(name).label_format for name in clean}
        self.assertEqual(formats, {"US_2016", "EU_1169"})

    def test_every_fixture_states_its_provenance(self):
        for path in (FIXTURES / "labels").glob("*.json"):
            data = json.loads(path.read_text("utf-8"))
            with self.subTest(path.stem):
                self.assertIn(data["provenance"],
                              ("published-example", "typical", "constructed"))
                self.assertGreater(len(data.get("source_note", "")), 40,
                                   "each fixture must say where its numbers came from")


class TestExpectedFlags(unittest.TestCase):
    def test_each_fixture_raises_exactly_its_expected_flags(self):
        for stem, expected in EXPECTED["expected"].items():
            with self.subTest(stem):
                findings = run_all(load(stem))
                raised = sorted({f.code for f in findings if f.severity == FLAG})
                self.assertEqual(
                    raised, sorted(expected),
                    f"{stem}: expected flags {sorted(expected)}, got {raised}. "
                    + " | ".join(f.message for f in findings if f.severity == FLAG),
                )

    def test_expected_notes_are_produced(self):
        for stem, expected in EXPECTED["expected_notes"].items():
            if stem.startswith("_"):
                continue
            with self.subTest(stem):
                codes = {f.code for f in run_all(load(stem)) if f.severity == NOTE}
                for code in expected:
                    self.assertIn(code, codes, f"{stem} lost its {code} note")


class TestTheCheckerIsNotVacuous(unittest.TestCase):
    """A checker that never fires on a correct label is only useful if it fires."""

    def test_breaking_a_negative_control_makes_it_flag(self):
        data = json.loads((FIXTURES / "labels" / "fda-sample-panel.json").read_text("utf-8"))
        self.assertEqual([f.code for f in run_all(Label.from_dict(data)) if f.severity == FLAG], [])
        data["nutrients"]["energy_kcal"] = 90
        broken = Label.from_dict(data)
        self.assertIn("energy_kcal", [f.code for f in run_all(broken) if f.severity == FLAG])

    def test_a_one_step_perturbation_does_not_flag(self):
        """The tolerance must be tight enough to catch a real error and loose enough
        to survive one rounding step, which is the whole reason it is derived from the
        rules rather than picked."""
        data = json.loads((FIXTURES / "labels" / "fda-sample-panel.json").read_text("utf-8"))
        data["nutrients"]["energy_kcal"] = 240
        nudged = Label.from_dict(data)
        self.assertEqual(
            [f.code for f in run_all(nudged) if f.severity == FLAG], [],
            "moving the calorie figure by one 10 kcal step must not be an error",
        )


class TestEnergyBandsHaveNoMagicTolerance(unittest.TestCase):
    def test_the_band_comes_from_the_macronutrient_intervals(self):
        result = check_energy(load("fda-sample-panel"))
        # Recomputed here from the printed rows without touching the energy module:
        # protein [2.5, 3.5), carbohydrate [36.5, 37.5), fat [7.5, 8.5).
        lowest_with_full_carbohydrate = 4 * 2.5 + 4 * 36.5 + 9 * 7.5
        highest = 4 * 3.5 + 4 * 37.5 + 9 * 8.5
        self.assertAlmostEqual(float(result.predicted.hi), highest, places=6)
        self.assertLessEqual(float(result.predicted.lo), lowest_with_full_carbohydrate)

    def test_the_fibre_allowance_widens_the_us_band_downward(self):
        result = check_energy(load("fda-sample-panel"))
        methods = {band.method: band for band in result.methods}
        self.assertLess(
            methods["general Atwater, carbohydrate less fibre"].band.lo,
            methods["general Atwater, total carbohydrate"].band.lo,
            "subtracting fibre must lower the floor of the permitted band",
        )

    def test_eu_uses_the_annex_xiv_table_and_checks_both_columns(self):
        label = load("eu-olive-oil")
        for column in ("kj", "kcal"):
            with self.subTest(column):
                self.assertTrue(check_energy(label, column).consistent)

    def test_the_two_eu_energy_columns_are_not_related_by_4_point_184(self):
        note = kj_kcal_ratio_note(load("eu-olive-oil"))
        self.assertIsNotNone(note.ratio)
        self.assertLess(float(note.ratio.hi), 4.184,
                        "olive oil is nearly all fat, so its declared ratio is near 37/9")
        self.assertIn("Annex XIV", note.message)

    def test_us_labels_have_no_kj_column_to_check(self):
        with self.assertRaises(ValueError):
            check_energy(load("fda-sample-panel"), "kj")


class TestFormatSpecificRelations(unittest.TestCase):
    def test_eu_carbohydrate_excludes_fibre(self):
        """The bug this test was written for: applying the US relation, in which
        carbohydrate contains fibre, to an EU panel flags every high-fibre product."""
        findings = run_all(load("eu-dark-chocolate"))
        self.assertEqual([f.code for f in findings if f.severity == FLAG], [])

    def test_us_carbohydrate_includes_fibre(self):
        data = json.loads((FIXTURES / "labels" / "fda-sample-panel.json").read_text("utf-8"))
        data["nutrients"]["fiber"] = 30
        data["nutrients"]["total_sugars"] = 12
        # 30 g of fibre plus 12 g of sugars cannot fit inside 37 g of US carbohydrate.
        codes = [f.code for f in run_all(Label.from_dict(data)) if f.severity == FLAG]
        self.assertIn("carbohydrate_components", codes)


if __name__ == "__main__":
    unittest.main()
