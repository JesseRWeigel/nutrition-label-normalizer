#!/usr/bin/env python3
"""Break the tool on purpose and check that something notices.

THREE GATES. A sabotage counts only if all three hold, in order:

  1. IT APPLIES. The text being replaced is present exactly once, and the replacement
     differs from it. Three attacks in this workspace once "passed" because the attack
     itself was a no-op, and each time the honest-looking conclusion was that a check had
     a gap when the check was fine.

  2. IT CHANGES THE MEASURED OUTPUT. scripts/fingerprint.py digests every interval,
     finding, rule inverse, unit conversion, OCR read, the generated page and four real
     CLI invocations. If the digest does not move, nothing was really sabotaged and a
     green suite afterwards proves nothing about the suite.

  3. ONLY THEN, SOMETHING CATCHES IT. The unit suite, the independent recomputation, or
     the page freshness check has to fail.

NULL CONTROL, RUN FIRST. An unmodified copy of the tree in a different directory must
digest to exactly the baseline. If it does not, the measurement is a function of where
the code sits rather than of the code, gate 2 passes for free for every attack, and the
run is void. That is not hypothetical: on 2026-08-06 a harness in this fleet hashed an
absolute path inside a per-run temporary directory and scored eleven sabotages as proven
while proving nothing. The run aborts rather than reporting a score.

GUARDS INVERT GATE 2. Some code here is dormant when the input is well formed. The
inverse's contiguity check never fires on a correct rule table, so disabling it cannot
move the fingerprint, and demanding that it does would be demanding the impossible. For
those entries the requirement is the opposite and stricter: the output must be UNCHANGED
and the unit suite must FAIL anyway. An entry filed as a guard that does move the output
was never dormant, and the harness says so rather than passing it.

Usage: scripts/sabotage.py [substring], to run one entry while working on it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# (name, file, old text, new text, what the change means, optional mode)
# mode defaults to "attack": the output must move and something must catch it.
# mode "guard": the code is dormant on correct input, so the output must NOT move and the
# unit suite must fail regardless.
SABOTAGES = [
    # ---- the rounding inverse, which is where every interval comes from -------------
    (
        "inverse-half-width",
        "nutrilabel/rounding.py",
        "                    half = form.step / 2",
        "                    half = form.step / 4",
        "every interval is half as wide as the rounding rule permits, so the tool "
        "understates the uncertainty on every figure it prints while still looking "
        "like it is carrying one",
    ),
    (
        "ties-round-down",
        "nutrilabel/rounding.py",
        "    if quotient - whole >= Fraction(1, 2):",
        "    if quotient - whole > Fraction(1, 2):",
        "a true 2.5 g declares as 2 rather than 3, which disagrees with every rounding "
        "table in the guidance documents and shifts the forward rule at exactly the "
        "boundaries the inverse is derived from",
    ),
    (
        "printed-precision-never-refines",
        "nutrilabel/rounding.py",
        '    if declaration.kind != "value":\n        return rule',
        '    if True:\n        return rule',
        "an EU panel printing 91.6 g of fat is read through a whole-gram step, claiming "
        "the true value lies anywhere in [91.1, 92.1) when the printed figure rules that "
        "out",
    ),
    (
        "unprintable-declaration-accepted",
        "nutrilabel/rounding.py",
        "                    if (target.amount / form.step).denominator != 1:\n                        continue",
        "                    if False:\n                        continue",
        "a figure no true value could print, 1 kcal under a 5 kcal step, silently returns "
        "an interval instead of raising, so a mistyped panel is normalised rather than "
        "refused. Filed as an attack rather than a guard because it does move the "
        "output: the fingerprint sweeps every rule with a dense grid of declarations, so "
        "the guard is exercised there and is not dormant",
    ),
    (
        "non-contiguous-inverse-accepted",
        "nutrilabel/rounding.py",
        "            if piece.lo > merged.hi:\n                raise ValueError(",
        "            if False:\n                raise ValueError(",
        "a gap in the rule table stops being an error, so an inverse that omits a range "
        "of true values is returned as if it were complete and every figure derived from "
        "it is quietly too narrow",
        "guard",
    ),

    # ---- interval arithmetic --------------------------------------------------------
    (
        "division-forgets-the-endpoint-swap",
        "nutrilabel/interval.py",
        "        return Interval(self.lo / other.hi, self.hi / other.lo, self.hi_open or other.hi_open)",
        "        return Interval(self.lo / other.lo, self.hi / other.hi, self.hi_open or other.hi_open)",
        "dividing by the smallest serving gives the smallest per-gram figure, which is "
        "backwards; every per-100 g interval comes out too narrow and on the wrong side",
    ),
    (
        "everything-overlaps",
        "nutrilabel/interval.py",
        "        return self.lo <= other.hi and other.lo <= self.hi",
        "        return True",
        "no two intervals are ever disjoint, so no label ever contradicts itself and the "
        "checker becomes a function that returns 'fine'",
    ),
    (
        "display-uses-bankers-rounding",
        "nutrilabel/interval.py",
        "    if scaled - whole >= Fraction(1, 2):\n        whole += 1",
        "    if scaled - whole > Fraction(1, 2):\n        whole += 1",
        "every printed figure ending in a 5 rounds down, so the page and the CLI disagree "
        "with the exact values underneath them",
    ),

    # ---- the rule tables ------------------------------------------------------------
    (
        "sodium-step-boundary-moved",
        "nutrilabel/rulesets.py",
        '            ("140", (nearest("5"),)),',
        '            ("40", (nearest("5"),)),',
        "sodium above 40 mg is read as rounded to the nearest 10 rather than the nearest "
        "5, widening every sodium interval between 40 and 140 mg for no reason in the "
        "regulation",
    ),
    (
        "calorie-step-boundary-moved",
        "nutrilabel/rulesets.py",
        '            ("50", (nearest("5"),)),',
        '            ("500", (nearest("5"),)),',
        "a declared 230 kcal is read as [227.5, 232.5) instead of [225, 235), which is the "
        "single most consequential interval on a US panel",
    ),
    (
        "trans-fat-zero-threshold-moved",
        "nutrilabel/rulesets.py",
        '    ("0.5", (Zero(), less_than("0.5"))),\n    ("5", (nearest("0.5"),)),',
        '    ("0.05", (Zero(), less_than("0.05"))),\n    ("5", (nearest("0.5"),)),',
        "a declared 0 g trans fat means below 0.05 g rather than below 0.5 g, so the "
        "headline claim, that a box of 13 servings can hold 6.5 g of it, collapses by a "
        "factor of ten",
    ),
    (
        "pre-2016-sodium-daily-value",
        "nutrilabel/rulesets.py",
        '    "sodium": (Fraction(2300), "mg"),\n    "carbohydrate": (Fraction(275), "g"),',
        '    "sodium": (Fraction(2400), "mg"),\n    "carbohydrate": (Fraction(275), "g"),',
        "the daily value reverts to the pre-2016 figure, so every percent daily value the "
        "tool recomputes is measured against a reference no current label uses",
    ),
    (
        "pre-2016-fibre-daily-value",
        "nutrilabel/rulesets.py",
        '    "fiber": (Fraction(28), "g"),',
        '    "fiber": (Fraction(25), "g"),',
        "same, for fibre, where the 2016 rule moved the reference from 25 g to 28 g",
    ),
    (
        "salt-factor-wrong",
        "nutrilabel/rulesets.py",
        "SALT_PER_SODIUM = Fraction(25, 10)",
        "SALT_PER_SODIUM = Fraction(254, 100)",
        "the Annex I salt factor stops being 2.5, so a US sodium figure and an EU salt "
        "figure no longer line up and the salt cross-check drifts",
    ),
    (
        "atwater-fat-factor-wrong",
        "nutrilabel/rulesets.py",
        '    "fat": Fraction(9),\n    "alcohol": Fraction(7),',
        '    "fat": Fraction(4),\n    "alcohol": Fraction(7),',
        "fat is billed at 4 kcal per gram, so the energy cross-check predicts far too "
        "little for anything fatty and starts calling correct labels inconsistent",
    ),
    (
        "eu-fat-kcal-factor-wrong",
        "nutrilabel/rulesets.py",
        '    "fat": (Fraction(37), Fraction(9)),',
        '    "fat": (Fraction(37), Fraction(4)),',
        "the Annex XIV kcal column for fat becomes the carbohydrate figure, which is the "
        "mistake that makes an olive oil label look wrong",
    ),

    # ---- the energy cross-check -----------------------------------------------------
    (
        "fibre-subtraction-uses-the-wrong-end",
        "nutrilabel/energy.py",
        "        max(Fraction(0), carbohydrate.lo - fiber.hi),\n        max(Fraction(0), carbohydrate.hi - fiber.lo),",
        "        max(Fraction(0), carbohydrate.lo - fiber.lo),\n        max(Fraction(0), carbohydrate.hi - fiber.hi),",
        "the lowest energy a permitted method can give is computed from the wrong end of "
        "the fibre interval, narrowing the band the tolerance is taken from",
    ),
    (
        "energy-check-always-consistent",
        "nutrilabel/energy.py",
        "    consistent = predicted.overlaps(declared)",
        "    consistent = True",
        "the check that makes the tool worth having returns a pass on every label, "
        "including one whose calories contradict its own macronutrients by 75 kcal",
    ),

    # ---- the consistency checks -----------------------------------------------------
    (
        "component-sums-never-exceed",
        "nutrilabel/checks.py",
        "        if floor > whole.hi:",
        "        if floor > whole.hi * 10:",
        "saturated fat may exceed total fat by a factor of ten before anything is said",
    ),
    (
        "eu-carbohydrate-includes-fibre",
        "nutrilabel/checks.py",
        '        carbohydrate_parts = ["total_sugars", "polyols", "starch"]',
        '        carbohydrate_parts = ["total_sugars", "fiber", "sugar_alcohol"]',
        "the US definition of total carbohydrate is applied to EU panels, where fibre is "
        "declared alongside carbohydrate rather than inside it, so every high-fibre "
        "product in Europe is flagged as self-contradictory",
    ),
    (
        "mass-budget-allows-a-kilo-per-hundred-grams",
        "nutrilabel/checks.py",
        "    if floor > 100:",
        "    if floor > 1000:",
        "the macronutrients may outweigh the food by ten to one, so a transposed serving "
        "size stops being detectable",
    ),
    (
        "declared-daily-value-never-checked",
        "nutrilabel/checks.py",
        "        if not (lowest <= printed <= highest):",
        "        if False:",
        "a panel printing 25 percent where its own sodium figure gives 7 percent passes "
        "without comment",
    ),
    (
        "added-sugars-may-exceed-total",
        "nutrilabel/checks.py",
        "    if added is not None and total is not None and added.lo > total.hi:",
        "    if added is not None and total is not None and added.lo > total.hi + 100:",
        "added sugars, which are a subset of total sugars by definition, may exceed them "
        "by 100 g before anything is said",
    ),

    # ---- the basis conversions ------------------------------------------------------
    (
        "per-container-ignores-the-serving-count",
        "nutrilabel/normalize.py",
        "            servings = label.servings_interval()\n            factor = servings",
        "            servings = label.servings_interval()\n            factor = Interval.exact(1)",
        "the per-container column becomes the per-serving column, which is precisely the "
        "'servings per container: 2.5' trick the tool exists to undo",
    ),
    (
        "per-hundred-factor-inverted",
        "nutrilabel/normalize.py",
        "    factor = Interval.exact(100) / serving",
        "    factor = serving / Interval.exact(100)",
        "per 100 g figures are multiplied by the serving size instead of divided, so two "
        "products are compared on a basis that is neither of theirs",
    ),
    (
        "compounding-warning-suppressed",
        "nutrilabel/normalize.py",
        "            if servings.width > 0:",
        "            if False:",
        "the tool stops saying that a rounded per-serving figure multiplied by a rounded "
        "serving count carries both roundings, which is the one thing a reader most needs "
        "told",
    ),

    (
        "compare-drops-the-warnings",
        "nutrilabel/cli.py",
        "    for warning in seen:\n        print(f\"  note: {warning}\")",
        "    for warning in []:\n        print(f\"  note: {warning}\")",
        "the side-by-side view stops carrying the compounding note, so the one place two "
        "products are weighed against each other is the one place the reader is not told "
        "the numbers are wider than they look. This is the bug verify.sh found",
    ),

    # ---- reading a panel ------------------------------------------------------------
    (
        "ocr-confidence-gate-removed",
        "nutrilabel/ocr.py",
        "        if confidence < threshold:",
        "        if confidence < -1:",
        "a token the engine was 41 percent sure of is accepted as a fact, which is the one "
        "behaviour the task brief names: flag low confidence rather than guessing a digit",
    ),
    (
        "ambiguous-glyphs-corrected-not-refused",
        "nutrilabel/ocr.py",
        "    if any(char in AMBIGUOUS_GLYPHS for char in body):\n        return None",
        "    body = body.replace('O', '0').replace('l', '1').replace('S', '5')\n    if False:\n        return None",
        "'16Omg' is silently corrected to 160 mg rather than refused, so a guess enters "
        "the pipeline wearing the same face as a reading",
    ),
    (
        "label-unit-conversion-skipped",
        "nutrilabel/label.py",
        "                if unit != rule.unit:",
        "                if False:",
        "a figure printed in milligrams is inverted through a rule written in grams, so "
        "the interval is out by a factor of a thousand",
    ),
]

# Anything that only fails the unit suite proves less than something the recomputation
# also catches, so both are always run and both are named in the output.
UNIT_SUITE = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q"]
INDEPENDENT = [sys.executable, "scripts/check_independent.py", "--quick"]
PAGE_FRESH = [sys.executable, "scripts/build_page.py", "--check"]


def run(cmd, cwd, timeout=900):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def copy_tree(dest):
    shutil.copytree(
        ROOT, dest,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".verify-tmp"),
    )


def fingerprint(cwd):
    result = run([sys.executable, "scripts/fingerprint.py"], cwd)
    if result.returncode != 0 or not result.stdout.strip():
        # The harness itself failing is a distinct outcome from the tool failing, and
        # fingerprint.py records a tool crash as data rather than as an exit code, so
        # this branch means the harness broke.
        return "HARNESS-FAILED", result
    return result.stdout.strip(), result


def apply_patch(path, old, new):
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if old == new:
        return "the sabotage is a no-op: old and new text are identical"
    count = text.count(old)
    if count == 0:
        return f"the text to replace is not in {os.path.basename(path)}"
    if count > 1:
        return f"the text to replace appears {count} times, so the patch is ambiguous"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text.replace(old, new, 1))
    return None


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    selected = [s for s in SABOTAGES if not only or only in s[0]]
    if not selected:
        print(f"  FAIL  no sabotage name contains {only!r}")
        return 1

    work = tempfile.mkdtemp(prefix="nutrilabel-sabotage-")
    caught = 0
    problems = []
    try:
        baseline_dir = os.path.join(work, "baseline")
        copy_tree(baseline_dir)
        baseline, result = fingerprint(baseline_dir)
        if baseline == "HARNESS-FAILED":
            print("  FAIL  the unmodified tree does not even fingerprint:")
            print((result.stderr or result.stdout or "")[:800])
            return 1
        print(f"  baseline fingerprint {baseline[:24]}")

        # ---- null control, before anything is broken --------------------------------
        null_dir = os.path.join(work, "null-control")
        copy_tree(null_dir)
        null_fingerprint, _ = fingerprint(null_dir)
        if null_fingerprint != baseline:
            print("  FAIL  NULL CONTROL: an unmodified copy fingerprints differently")
            print(f"        baseline {baseline}")
            print(f"        copy     {null_fingerprint}")
            print("        The measurement depends on where the tree sits, so every "
                  "sabotage would pass")
            print("        gate 2 for free and this whole run would be meaningless. Aborting.")
            return 1
        print("  ok    null control: an untouched copy in a different directory digests "
              "identically")

        for entry in selected:
            name, rel, old, new, meaning = entry[:5]
            mode = entry[5] if len(entry) > 5 else "attack"
            work_dir = os.path.join(work, name)
            copy_tree(work_dir)

            # gate 1
            error = apply_patch(os.path.join(work_dir, rel), old, new)
            if error:
                print(f"  FAIL  {name}: DID NOT APPLY, {error}")
                problems.append(name)
                continue

            # gate 2
            moved, _ = fingerprint(work_dir)
            if moved == "HARNESS-FAILED":
                print(f"  FAIL  {name}: the fingerprint harness itself broke, so gate 2 "
                      f"could not be judged")
                problems.append(name)
                continue
            if mode == "guard":
                if moved != baseline:
                    print(f"  FAIL  {name}: filed as a guard, but disabling it moved the "
                          f"output.")
                    print("        A guard is code that does nothing when the input is "
                          "well formed. This one")
                    print("        was doing something, so the classification is wrong. "
                          "Rerun it as an attack.")
                    problems.append(name)
                    continue
                changed = "output unchanged, as a dormant guard must be"
            elif moved == baseline:
                print(f"  FAIL  {name}: applied, and the measured output is byte identical.")
                print("        Nothing was really sabotaged, so no conclusion can be drawn "
                      "about the checks.")
                print("        Either the corpus never exercises this code, in which case "
                      "the fix is a")
                print("        fixture that does, or the change has no effect and the "
                      "check it was aimed")
                print("        at is fine.")
                problems.append(name)
                continue
            else:
                changed = f"fingerprint {baseline[:12]} -> {moved[:12]}"

            # gate 3
            detectors = {
                "unit suite": run(UNIT_SUITE, work_dir).returncode,
                "independent recomputation": run(INDEPENDENT, work_dir).returncode,
                "page freshness": run(PAGE_FRESH, work_dir).returncode,
            }
            if mode == "guard":
                # For a guard the other two detectors read output that is by definition
                # unchanged, so only the unit suite can speak.
                noticed = ["unit suite"] if detectors["unit suite"] != 0 else []
            else:
                noticed = [key for key, code in detectors.items() if code != 0]

            print(f"  {'ok   ' if noticed else 'FAIL '} {name}"
                  f"{' [guard]' if mode == 'guard' else ''}")
            print(f"          {meaning}")
            print(f"          changed: {changed}")
            if noticed:
                print(f"          caught by: {', '.join(noticed)}")
                caught += 1
            elif mode == "guard":
                print("          NOTHING NOTICED. The guard was disabled and the unit "
                      "suite, which is the")
                print("          only check that can feed it the input it guards against, "
                      "stayed green.")
                problems.append(name)
            else:
                print("          NOTHING NOTICED. The output moved and every check stayed "
                      "green.")
                problems.append(name)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"{caught} of {len(selected)} sabotages passed all three gates and were caught")
    if problems:
        print(f"unproven: {', '.join(problems)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
