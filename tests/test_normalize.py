"""Normalisation against hand-computed expectations."""

from __future__ import annotations

import json
import unittest
from fractions import Fraction
from pathlib import Path

from nutrilabel.interval import Interval
from nutrilabel.label import Label, LabelError
from nutrilabel.normalize import normalize

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


def load(stem: str) -> Label:
    return Label.load(FIXTURES / "labels" / f"{stem}.json")


class TestHandComputedNormalisations(unittest.TestCase):
    def test_cases(self):
        data = json.loads((FIXTURES / "expected" / "normalisations.json").read_text("utf-8"))
        cases = data["cases"]
        self.assertGreaterEqual(len(cases), 12, "the hand-computed corpus has shrunk")
        for case in cases:
            with self.subTest(case["fixture"], basis=case["basis"], nutrient=case["nutrient"]):
                label = load(case["fixture"])
                result = normalize(label, case["basis"])
                value = result.values[case["nutrient"]]
                self.assertEqual(value.interval.lo, Fraction(case["lo"]), case["why"])
                self.assertEqual(value.interval.hi, Fraction(case["hi"]), case["why"])


class TestBasisIsAlwaysStated(unittest.TestCase):
    """Every figure the tool emits must carry the basis it is on."""

    def test_every_value_records_its_basis(self):
        for basis in ("per_serving", "per_100g", "per_100kcal", "per_container"):
            label = load("fda-sample-panel")
            result = normalize(label, basis)
            self.assertTrue(result.basis_label)
            for key, value in result.values.items():
                self.assertEqual(value.basis, basis, f"{key} lost its basis")

    def test_liquid_reads_per_100_millilitres(self):
        result = normalize(load("us-whole-milk"), "per_100g")
        self.assertEqual(result.basis_label, "per 100 ml")

    def test_solid_reads_per_100_grams(self):
        result = normalize(load("fda-sample-panel"), "per_100g")
        self.assertEqual(result.basis_label, "per 100 g")

    def test_per_serving_names_the_serving(self):
        result = normalize(load("fda-sample-panel"), "per_serving")
        self.assertIn("55 g", result.basis_label)
        self.assertIn("2/3 cup", result.basis_label)


class TestTheServingSizeTrap(unittest.TestCase):
    """The comparison the whole tool exists for."""

    def test_per_serving_makes_the_cereals_look_far_apart(self):
        a = normalize(load("cereal-a-flakes"), "per_serving").values["energy_kcal"]
        b = normalize(load("cereal-b-granola"), "per_serving").values["energy_kcal"]
        ratio = b.interval / a.interval
        self.assertGreater(float(ratio.lo), 1.5,
                           "per serving, granola should look at least 50 percent higher")

    def test_per_100g_makes_them_nearly_identical(self):
        a = normalize(load("cereal-a-flakes"), "per_100g").values["energy_kcal"]
        b = normalize(load("cereal-b-granola"), "per_100g").values["energy_kcal"]
        self.assertTrue(
            a.interval.overlaps(b.interval),
            f"on a per 100 g basis the two cereals should be indistinguishable, "
            f"got {a.interval!r} and {b.interval!r}",
        )
        ratio = b.interval / a.interval
        self.assertLess(float(ratio.lo), 1.15)


class TestRoundingCompounds(unittest.TestCase):
    def test_zero_trans_fat_becomes_over_a_gram_per_container(self):
        result = normalize(load("us-crackers-2point5-servings"), "per_container")
        trans = result.values["trans_fat"]
        self.assertEqual(trans.interval.lo, Fraction(0))
        self.assertEqual(trans.interval.hi, Fraction(11, 8))

    def test_per_container_warns_that_two_roundings_multiplied(self):
        result = normalize(load("us-crackers-2point5-servings"), "per_container")
        self.assertTrue(
            any("compounds" in warning for warning in result.warnings),
            "the per-container basis must say that it multiplied two rounded figures",
        )

    def test_serving_size_rounding_widens_every_per_100g_figure(self):
        per_serving = normalize(load("cereal-a-flakes"), "per_serving").values["protein"]
        per_hundred = normalize(load("cereal-a-flakes"), "per_100g").values["protein"]
        relative_before = per_serving.interval.width / per_serving.interval.midpoint
        relative_after = per_hundred.interval.width / per_hundred.interval.midpoint
        self.assertGreater(relative_after, relative_before,
                           "dividing by a rounded serving size must widen the interval")


class TestFailuresAreLoud(unittest.TestCase):
    def test_no_serving_size_refuses_per_100g(self):
        label = Label.from_dict({
            "name": "no serving", "format": "US_2016", "provenance": "constructed",
            "nutrients": {"energy_kcal": 100, "fat": 5, "carbohydrate": 10, "protein": 2},
        })
        with self.assertRaises(LabelError) as caught:
            normalize(label, "per_100g")
        self.assertIn("serving", str(caught.exception))

    def test_kj_only_label_refuses_per_100kcal_rather_than_converting(self):
        label = Label.from_dict({
            "name": "kJ only", "format": "EU_1169", "provenance": "constructed",
            "declared_basis": "per_100g",
            "serving": {"amount": 100, "unit": "g"},
            "nutrients": {"energy_kj": 1000, "fat": 5, "carbohydrate": 30, "protein": 5},
        })
        with self.assertRaises(LabelError) as caught:
            normalize(label, "per_100kcal")
        self.assertIn("4.184", str(caught.exception))

    def test_unknown_nutrient_without_a_unit_is_refused(self):
        with self.assertRaises(LabelError):
            Label.from_dict({
                "name": "mystery", "format": "US_2016", "provenance": "constructed",
                "nutrients": {"unobtainium": 5},
            })

    def test_provenance_is_mandatory(self):
        with self.assertRaises(LabelError):
            Label.from_dict({
                "name": "anonymous", "format": "US_2016", "provenance": "from the internet",
                "nutrients": {"energy_kcal": 100},
            })

    def test_divide_by_an_interval_touching_zero_raises(self):
        with self.assertRaises(ValueError):
            Interval.of(1, 2) / Interval.of(0, 5)


class TestRulesetProvenanceSurvives(unittest.TestCase):
    def test_inferred_rules_are_labelled_as_such(self):
        result = normalize(load("eu-olive-oil"), "per_100g")
        fat = result.values["fat"]
        self.assertEqual(
            fat.rule_basis, "inferred",
            "91.6 g is finer than the guidance step, so the interval comes from the "
            "printed precision and must be labelled inferred rather than cited",
        )
        self.assertIn("refined", fat.citation)

    def test_us_panels_keep_their_cited_rules(self):
        result = normalize(load("fda-sample-panel"), "per_serving")
        for key in ("energy_kcal", "fat", "sodium", "carbohydrate", "protein"):
            self.assertEqual(result.values[key].rule_basis, "cited", key)
            self.assertIn("21 CFR", result.values[key].citation)


if __name__ == "__main__":
    unittest.main()
