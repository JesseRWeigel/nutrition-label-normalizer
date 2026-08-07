"""Rounding rules, both directions.

The sweep in ``test_inverse_agrees_with_forward_rule`` is the load-bearing test here.
Every other test in this file checks a specific number; the sweep checks the property
that makes the whole tool sound, which is that ``inverse(declared)`` contains every
true value that would print as ``declared`` and nothing else. A rule table edited
wrongly passes the specific cases it was edited for and fails the sweep.
"""

from __future__ import annotations

import json
import unittest
from fractions import Fraction
from pathlib import Path

from nutrilabel.rounding import Declaration, round_half_up
from nutrilabel.rulesets import EU_RULES, US_RULES, rules_for

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class TestRoundHalfUp(unittest.TestCase):
    def test_ties_go_up(self):
        self.assertEqual(round_half_up(Fraction(5, 2), Fraction(1)), Fraction(3))
        self.assertEqual(round_half_up(Fraction(7, 2), Fraction(1)), Fraction(4))

    def test_python_round_would_disagree(self):
        # Guard against someone replacing this with the builtin. round(2.5) is 2.
        self.assertNotEqual(round_half_up(Fraction(5, 2), Fraction(1)), Fraction(round(2.5)))

    def test_steps_other_than_one(self):
        self.assertEqual(round_half_up(Fraction(47, 1), Fraction(5)), Fraction(45))
        self.assertEqual(round_half_up(Fraction(475, 10), Fraction(5)), Fraction(50))
        self.assertEqual(round_half_up(Fraction(2251, 10), Fraction(10)), Fraction(230))


class TestHandComputedInverses(unittest.TestCase):
    def test_cases_from_fixture_file(self):
        data = json.loads((FIXTURES / "expected" / "rounding-inverses.json").read_text("utf-8"))
        cases = data["cases"]
        self.assertGreaterEqual(len(cases), 15, "the hand-computed corpus has shrunk")
        for case in cases:
            with self.subTest(case["format"], nutrient=case["nutrient"], declared=case["declared"]):
                rule = rules_for(case["format"])[case["nutrient"]]
                interval = rule.inverse(case["declared"])
                self.assertEqual(
                    interval.lo, Fraction(case["lo"]),
                    f"lower bound. {case['why']}",
                )
                self.assertEqual(
                    interval.hi, Fraction(case["hi"]),
                    f"upper bound. {case['why']}",
                )


class TestInverseAgreesWithForwardRule(unittest.TestCase):
    """The property that makes every downstream interval trustworthy."""

    def _sweep(self, rule, limit: Fraction, step: Fraction):
        value = Fraction(0)
        checked = 0
        while value <= limit:
            for declaration in rule.permitted(value):
                interval = rule.inverse(declaration)
                self.assertTrue(
                    interval.contains(value),
                    f"{rule.name}: a true value of {float(value)} prints as "
                    f"{declaration.render(rule.unit)}, but the inverse of that "
                    f"declaration is {interval!r} and does not contain it. The rule "
                    f"table and its inverse disagree.",
                )
                checked += 1
            value += step
        return checked

    def test_us_rules(self):
        total = 0
        for key, rule in US_RULES.items():
            with self.subTest(nutrient=key):
                limit = Fraction(400) if rule.unit in ("kcal", "mg") else Fraction(60)
                step = Fraction(1, 20) if rule.unit == "g" else Fraction(1, 4)
                total += self._sweep(rule, limit, step)
        self.assertGreater(total, 10000, "the sweep covered suspiciously few values")

    def test_eu_rules(self):
        for key, rule in EU_RULES.items():
            with self.subTest(nutrient=key):
                limit = Fraction(300) if rule.unit in ("kJ", "kcal") else Fraction(40)
                step = Fraction(1, 100) if rule.unit == "g" else Fraction(1, 4)
                self._sweep(rule, limit, step)

    def test_no_value_prints_as_something_outside_its_inverse(self):
        """The converse: nothing inside an inverse prints as anything else.

        Sweeps the interval returned for a declaration and requires every point in it
        to permit that declaration. Catches an inverse that is too wide, which the
        containment sweep above cannot see.
        """
        for key, rule in list(US_RULES.items()) + list(EU_RULES.items()):
            for printed in ("0", "1", "5", "10", "45", "230"):
                declaration = Declaration.parse(printed)
                try:
                    interval = rule.inverse(declaration)
                except ValueError:
                    continue
                width = interval.hi - interval.lo
                probes = [interval.lo + width * Fraction(n, 8) for n in range(8)]
                for probe in probes:
                    with self.subTest(nutrient=key, printed=printed, probe=float(probe)):
                        self.assertIn(
                            declaration, rule.permitted(probe),
                            f"{rule.name}: inverse of {printed} includes {float(probe)}, "
                            f"but that value does not print as {printed}. The inverse is "
                            f"too wide, so every derived interval is too pessimistic.",
                        )


class TestRuleTableShape(unittest.TestCase):
    def test_segments_tile_the_non_negative_reals(self):
        # RoundingRule enforces this at construction. This test proves the enforcement
        # is real by building a broken rule and requiring the constructor to refuse.
        from nutrilabel.rounding import Nearest, RoundingRule, Segment

        with self.assertRaises(ValueError):
            RoundingRule(
                "gappy", "g",
                (Segment(Fraction(0), Fraction(1), (Nearest(Fraction(1)),)),
                 Segment(Fraction(2), None, (Nearest(Fraction(1)),))),
                "test",
            )

    def test_every_rule_declares_its_basis(self):
        for key, rule in list(US_RULES.items()) + list(EU_RULES.items()):
            with self.subTest(nutrient=key):
                self.assertIn(rule.basis, ("cited", "guidance", "inferred"))
                self.assertTrue(rule.citation.strip(), f"{key} has no citation")

    def test_impossible_declaration_raises(self):
        # 3 mg of cholesterol must print as "less than 5 mg", so a printed 3 is not a
        # value any true amount could produce and the tool must say so rather than
        # inventing an interval around it.
        with self.assertRaises(ValueError):
            US_RULES["cholesterol"].inverse(3)


if __name__ == "__main__":
    unittest.main()
