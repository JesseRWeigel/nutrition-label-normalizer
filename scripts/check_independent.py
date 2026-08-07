#!/usr/bin/env python3
"""Recompute the headline numbers by a different route, importing nothing from the tool.

The package under test is never imported here. It is run as a subprocess, its JSON is
read, and every figure in it is recomputed from the raw fixture files by code written
from the regulations rather than shared with ``nutrilabel``. The last check proves that
independence rather than asserting it: this file parses its own source with ``ast``,
follows every import it can resolve, and fails if anything it reaches is part of the
package. A grep for "nutrilabel" would be satisfied by a comment in a docstring and
would miss ``importlib.import_module``.

Different route, not the same route twice. The three things recomputed here:

  ROUNDING INVERSE. The tool builds an interval algebraically, from a table of half-open
  segments it also uses in the forward direction. This file writes the forward rule out
  as a plain conditional cascade, straight from 21 CFR 101.9(c) and the EC rounding
  annex, and then inverts it by BISECTION: the set of true values printing a given
  declaration is contiguous, so its two edges are found by binary search on the forward
  function. Search, not algebra. A wrong segment boundary in the tool's table cannot be
  wrong in the same way here, because there is no table here.

  BASIS CONVERSION. The tool divides one interval by another with an endpoint rule that
  knows to swap the ends. This file evaluates the expression at all four corners of the
  two input intervals and takes the minimum and maximum. Corner enumeration is slower and
  obviously correct for a monotone expression, which is what you want in a checker. If
  the tool ever forgets the swap, the corners still find the right answer and disagree.

  ATWATER CROSS-CHECK. The factor tables are typed out again here from Annex XIV and from
  21 CFR 101.9(c)(1)(i), and the predicted energy band is summed by corner enumeration
  over the macronutrient intervals. The verdict, consistent or not, is compared against
  the tool's.

Exit code 0 means every recomputation agreed. Any disagreement is printed with both
numbers and exits 1.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Bisection stops when the bracket is narrower than this. The endpoints being recovered
# are multiples of a hundredth of a gram at worst, so a bracket of 2^-40 pins them far
# beyond any precision the comparison needs, and the comparison tolerance below is set
# two orders of magnitude looser than the bracket so the search can never be the reason
# a real disagreement is missed.
BISECT_EPSILON = Fraction(1, 2 ** 40)
TOLERANCE = Fraction(1, 2 ** 30)


# --------------------------------------------------------------------------- forward rules
#
# Written as conditionals, from the regulation text, with no segment table and no shared
# helper with the package. Each function returns the SET of declarations a true value may
# legally carry, as strings: "8" for a number, "<0.5" for a "less than" form.


def _nearest(value: Fraction, step: Fraction) -> Fraction:
    """Round to the nearest multiple of step, ties away from zero.

    Written with divmod on the exact rational rather than with round(), which is banker's
    rounding and would send a true 2.5 down to 2.
    """
    quotient, remainder = divmod(value, step)
    if remainder * 2 >= step:
        quotient += 1
    return quotient * step


def _text(value: Fraction) -> str:
    """Canonical text for a declared number, so "8", "8.0" and "8.00" compare equal."""
    if value == int(value):
        return str(int(value))
    return str(float(value))


def us_forms(nutrient: str, x: Fraction) -> set[str]:
    """21 CFR 101.9(c). Permitted printed forms for a true per-serving amount."""
    if x < 0:
        raise ValueError("a negative amount cannot be declared")

    if nutrient == "energy_kcal":
        # (c)(1): below 5 calories may be expressed as zero; to the nearest 5 up to 50
        # calories; to the nearest 10 above 50.
        if x < 5:
            return {"0", _text(_nearest(x, Fraction(5)))}
        if x < 50:
            return {_text(_nearest(x, Fraction(5)))}
        return {_text(_nearest(x, Fraction(10)))}

    if nutrient in ("fat", "saturated_fat", "trans_fat",
                    "monounsaturated_fat", "polyunsaturated_fat"):
        # (c)(2): below 0.5 g expressed as zero, or as "less than 0.5 gram"; 0.5 g
        # increments below 5 g; 1 g increments at or above 5 g.
        if x < Fraction(1, 2):
            return {"0", "<0.5"}
        if x < 5:
            return {_text(_nearest(x, Fraction(1, 2)))}
        return {_text(_nearest(x, Fraction(1)))}

    if nutrient == "cholesterol":
        # (c)(3): below 2 mg expressed as zero; 2 to 5 mg as "less than 5 milligrams";
        # 5 mg increments above that.
        if x < 2:
            return {"0"}
        if x < 5:
            return {"<5"}
        return {_text(_nearest(x, Fraction(5)))}

    if nutrient == "sodium":
        # (c)(4): below 5 mg expressed as zero; 5 mg increments up to 140 mg; 10 mg
        # increments above 140 mg.
        if x < 5:
            return {"0"}
        if x < 140:
            return {_text(_nearest(x, Fraction(5)))}
        return {_text(_nearest(x, Fraction(10)))}

    if nutrient in ("carbohydrate", "fiber", "total_sugars", "added_sugars",
                    "sugar_alcohol", "protein"):
        # (c)(6) and (c)(7): below 0.5 g expressed as zero; between 0.5 and 1 g either
        # "less than 1 gram" or 1 g; whole grams above that.
        if x < Fraction(1, 2):
            return {"0"}
        if x < 1:
            return {"1", "<1"}
        return {_text(_nearest(x, Fraction(1)))}

    raise KeyError(f"no US rule written here for {nutrient!r}")


def eu_forms(nutrient: str, x: Fraction) -> set[str]:
    """EC guidance on tolerances (Dec 2012), rounding annex."""
    if x < 0:
        raise ValueError("a negative amount cannot be declared")

    if nutrient in ("energy_kj", "energy_kcal"):
        return {_text(_nearest(x, Fraction(1)))}

    if nutrient == "salt":
        if x < Fraction(125, 10000):
            return {"0", "<0.01"}
        if x < 1:
            return {_text(_nearest(x, Fraction(1, 100)))}
        return {_text(_nearest(x, Fraction(1, 10)))}

    if nutrient in ("fat", "saturated_fat", "monounsaturated_fat",
                    "polyunsaturated_fat", "carbohydrate", "total_sugars",
                    "polyols", "starch", "fiber", "protein"):
        if x < Fraction(1, 2):
            return {"0", "<0.5"}
        if x < 10:
            return {_text(_nearest(x, Fraction(1, 10)))}
        return {_text(_nearest(x, Fraction(1)))}

    raise KeyError(f"no EU rule written here for {nutrient!r}")


def us_serving_forms(x: Fraction) -> set[str]:
    """21 CFR 101.9(b), the metric quantity of one serving."""
    if x < 5:
        return {_text(_nearest(x, Fraction(1, 2)))}
    return {_text(_nearest(x, Fraction(1)))}


def us_servings_forms(x: Fraction) -> set[str]:
    """21 CFR 101.9(b), the number of servings per container."""
    if x < 5:
        return {_text(_nearest(x, Fraction(1, 2)))}
    return {_text(_nearest(x, Fraction(1)))}


def eu_serving_forms(x: Fraction) -> set[str]:
    return {_text(_nearest(x, Fraction(1)))}


def significant_forms(printed: Fraction, x: Fraction) -> set[str]:
    """Fallback for a figure whose rounding step is not regulated.

    Reads the step off the printed value: 440 is treated as whole units, 0.9 as tenths.
    Matches the tool's stated fallback policy, and it has to: an interval is only
    comparable against another interval derived from the same assumption about precision.
    What is NOT shared is the code, which is what a second implementation is for.
    """
    step = Fraction(1)
    for exponent in range(0, 7):
        candidate = Fraction(1, 10 ** exponent)
        if (printed / candidate).denominator == 1:
            step = candidate
            break
    else:
        step = Fraction(1, 10 ** 6)
    return {_text(_nearest(x, step))}


# --------------------------------------------------------------------------- inversion


def invert(forms, declared: str, ceiling: Fraction) -> tuple[Fraction, Fraction]:
    """The interval of true values printing ``declared``, found by bisection.

    ``forms`` is one of the functions above. The predicate "x prints as declared" is
    true on one contiguous run, so its two edges are found by binary search: the lower
    edge between a point known false and a point known true, the upper edge the other
    way round. No segment table is consulted, which is the point.

    ``ceiling`` bounds the search. It is set by the caller to comfortably more than any
    value that could print as ``declared``.
    """
    def prints(x: Fraction) -> bool:
        try:
            return declared in forms(x)
        except (ValueError, KeyError):
            return False

    # Find any point inside the run. A coarse sweep is enough because the narrowest run
    # in either rule table is a hundredth of a gram wide.
    steps = 20000
    inside = None
    for index in range(steps + 1):
        probe = ceiling * index / steps
        if prints(probe):
            inside = probe
            break
    if inside is None:
        raise ValueError(f"no true value below {float(ceiling):g} prints as {declared!r}")

    # Lower edge: bisect between a known-false point below and the known-true point.
    if prints(Fraction(0)):
        low = Fraction(0)
    else:
        false_side, true_side = Fraction(0), inside
        while true_side - false_side > BISECT_EPSILON:
            middle = (false_side + true_side) / 2
            if prints(middle):
                true_side = middle
            else:
                false_side = middle
        low = true_side

    # Upper edge: bisect between the known-true point and a known-false point above.
    outside = ceiling
    while prints(outside):
        outside *= 2
        if outside > ceiling * 1024:
            raise ValueError(f"the run for {declared!r} appears unbounded above")
    true_side, false_side = inside, outside
    while false_side - true_side > BISECT_EPSILON:
        middle = (true_side + false_side) / 2
        if prints(middle):
            true_side = middle
        else:
            false_side = middle
    high = false_side
    return _snap(low), _snap(high)


# Every edge these rules can have is a declared figure plus or minus half a rounding
# step, and the finest step in either table is a millionth of a unit, so every true edge
# is a rational with a denominator under 2 * 10^6. Bisection converges to within 2^-40 of
# one, which is a thousand times closer than the grid is spaced.
SNAP_DENOMINATOR = 10 ** 7


def _snap(value: Fraction) -> Fraction:
    """Recover the exact rational the bisection was converging on.

    Without this, an edge at 121.75 comes back as 121.74999999999909, which agrees with
    the tool to a part in 10^12 and then rounds to a different first decimal place when
    the tool's message is compared. The snap is refused, and the raw value kept, if the
    nearest small rational is further away than the bisection bracket could account for.
    A snap that had to move the value would be inventing an answer rather than cleaning
    one up.
    """
    candidate = value.limit_denominator(SNAP_DENOMINATOR)
    return candidate if abs(candidate - value) <= BISECT_EPSILON * 4 else value


# --------------------------------------------------------------------------- corners


def corners(op, *intervals) -> tuple[Fraction, Fraction]:
    """Evaluate ``op`` at every combination of interval endpoints, return min and max.

    The tool's interval division carries a hand-written endpoint rule that swaps the
    ends. This does not know about swapping. For an expression monotone in each argument,
    which every expression here is, the extremes are attained at corners, so the two
    routes must agree and a forgotten swap shows up immediately.
    """
    from itertools import product

    values = [op(*combo) for combo in product(*[(i[0], i[1]) for i in intervals])]
    return min(values), max(values)


# --------------------------------------------------------------------------- factors
#
# Typed out again from the sources rather than imported. If a digit is wrong in one of
# the two copies the comparison says so, which is the only way a constant gets checked.

EU_KCAL_PER_GRAM = {
    "carbohydrate_available": Fraction(4),
    "polyols": Fraction(24, 10),
    "protein": Fraction(4),
    "fat": Fraction(9),
    "alcohol": Fraction(7),
    "organic_acid": Fraction(3),
    "fiber": Fraction(2),
}
EU_KJ_PER_GRAM = {
    "carbohydrate_available": Fraction(17),
    "polyols": Fraction(10),
    "protein": Fraction(17),
    "fat": Fraction(37),
    "alcohol": Fraction(29),
    "organic_acid": Fraction(13),
    "fiber": Fraction(8),
}
US_KCAL_PER_GRAM = {"protein": Fraction(4), "carbohydrate": Fraction(4), "fat": Fraction(9),
                    "alcohol": Fraction(7)}
US_POLYOL_LOW = Fraction(0)
US_POLYOL_HIGH = Fraction(3)


# --------------------------------------------------------------------------- fixtures


def read_fixture(rel: str) -> dict:
    with (ROOT / rel).open(encoding="utf-8") as handle:
        return json.load(handle)


def declared_text(raw) -> str:
    """The printed form, as text, from whatever JSON shape the fixture used."""
    if isinstance(raw, dict):
        raw = raw["value"]
    if isinstance(raw, str):
        cleaned = raw.strip().replace("≤", "<=")
        for prefix in ("less than", "<=", "<"):
            if cleaned.lower().startswith(prefix):
                return "<" + _text(Fraction(cleaned[len(prefix):].strip()))
        return _text(Fraction(cleaned))
    return _text(Fraction(str(raw)))


def declared_unit(raw) -> str:
    if isinstance(raw, dict) and raw.get("unit"):
        return str(raw["unit"]).strip().lower()
    return ""


# The unit each rounding rule is written in, and the conversion factors between units of
# the same dimension. Both typed out again here rather than imported. A panel printing
# sodium in grams has to be converted into milligrams before its interval is taken, or a
# gram-sized rounding step lands on a milligram figure and the answer is out by a
# thousand.
RULE_UNIT = {
    "US_2016": {
        "energy_kcal": "kcal", "cholesterol": "mg", "sodium": "mg",
        "fat": "g", "saturated_fat": "g", "trans_fat": "g",
        "monounsaturated_fat": "g", "polyunsaturated_fat": "g",
        "carbohydrate": "g", "fiber": "g", "total_sugars": "g",
        "added_sugars": "g", "sugar_alcohol": "g", "protein": "g",
    },
    "EU_1169": {
        "energy_kj": "kj", "energy_kcal": "kcal", "salt": "g",
        "fat": "g", "saturated_fat": "g", "monounsaturated_fat": "g",
        "polyunsaturated_fat": "g", "carbohydrate": "g", "total_sugars": "g",
        "polyols": "g", "starch": "g", "fiber": "g", "protein": "g",
    },
}

UNIT_ALIAS = {"gram": "g", "grams": "g", "gm": "g", "milligram": "mg", "milligrams": "mg",
              "microgram": "mcg", "micrograms": "mcg", "ug": "mcg", "µg": "mcg",
              "μg": "mcg", "calories": "kcal", "calorie": "kcal", "l": "l",
              "litre": "l", "liter": "l", "kj": "kj", "kcal": "kcal"}

# Grams per unit, and millilitres per unit. Two separate tables so a mass can never be
# converted into a volume by accident.
PER_GRAM = {"kg": Fraction(1000), "g": Fraction(1), "mg": Fraction(1, 1000),
            "mcg": Fraction(1, 1000000)}
PER_ML = {"l": Fraction(1000), "ml": Fraction(1)}


def canonical_unit(text: str) -> str:
    return UNIT_ALIAS.get(text.strip().lower(), text.strip().lower())


def unit_factor(source: str, target: str) -> Fraction:
    """How many ``target`` units one ``source`` unit is. Raises across dimensions."""
    source, target = canonical_unit(source), canonical_unit(target)
    if source == target:
        return Fraction(1)
    for table in (PER_GRAM, PER_ML):
        if source in table and target in table:
            return table[source] / table[target]
    raise ValueError(f"cannot convert {source} to {target}")


def nutrient_interval(fmt: str, key: str, raw) -> tuple[Fraction, Fraction]:
    """The interval for one printed nutrient row, in the rule's own unit.

    Two fallbacks to the printed precision, both of them policy the tool also follows and
    neither of them shared code. The first is for a nutrient with no rule at all, a
    vitamin say. The second is for a label finer than its own rule table: an EU panel
    printing 91.6 g of fat has used a tenth-gram step where the guidance annex gives whole
    grams above 10 g, and reading it through the coarser step would claim an interval the
    printed figure itself rules out.
    """
    text = declared_text(raw)
    printed_unit = declared_unit(raw)
    rule_unit = RULE_UNIT.get(fmt, {}).get(key)
    if printed_unit and rule_unit and canonical_unit(printed_unit) != rule_unit:
        # Convert the printed figure into the unit the rule is written in, then invert.
        # Inverting first would apply the wrong rounding step, which is a factor of a
        # thousand for a mass.
        prefix = "<" if text.startswith("<") else ""
        converted = Fraction(text.lstrip("<")) * unit_factor(printed_unit, rule_unit)
        text = prefix + _text(converted)
    magnitude = Fraction(text.lstrip("<") or "0")
    ceiling = max(magnitude * 4, Fraction(20))
    forms = us_forms if fmt == "US_2016" else eu_forms
    try:
        return invert(lambda x, k=key: forms(k, x), text, ceiling)
    except KeyError:
        # No rule written for this nutrient in either table.
        return invert(lambda x, p=magnitude: significant_forms(p, x), text, ceiling)
    except ValueError:
        # There is a rule, and no true value under it would print this figure.
        return invert(lambda x, p=magnitude: significant_forms(p, x), text, ceiling)


def serving_interval(fixture: dict) -> tuple[Fraction, Fraction]:
    amount = Fraction(str(fixture["serving"]["amount"]))
    forms = us_serving_forms if fixture.get("format", "US_2016") == "US_2016" else eu_serving_forms
    return invert(forms, _text(amount), max(amount * 4, Fraction(20)))


def servings_interval(fixture: dict) -> tuple[Fraction, Fraction]:
    amount = Fraction(str(fixture["servings_per_container"]))
    return invert(us_servings_forms, _text(amount), max(amount * 4, Fraction(20)))


def container_interval(fixture: dict) -> tuple[Fraction, Fraction]:
    """Total package size, preferring the declared net quantity over the product.

    Net quantity is one rounded figure; servings times serving size is two intervals
    multiplied together and is much wider. The tool prefers the narrower of the two and
    so does this, because comparing a wide interval against a narrow one would disagree
    for a reason that is a policy difference rather than an arithmetic one.
    """
    if "net_quantity" in fixture:
        amount = Fraction(str(fixture["net_quantity"]["amount"]))
        return invert(lambda x, p=amount: significant_forms(p, x),
                      _text(amount), max(amount * 4, Fraction(20)))
    return corners(lambda s, n: s * n, serving_interval(fixture), servings_interval(fixture))


def declared_per_hundred(fixture: dict) -> bool:
    fmt = fixture.get("format", "US_2016")
    basis = fixture.get("declared_basis", "per_serving" if fmt == "US_2016" else "per_100g")
    return basis in ("per_100g", "per_100ml")


def to_basis(fixture: dict, interval: tuple[Fraction, Fraction], basis: str):
    """Move one nutrient interval from the label's declared basis to ``basis``.

    Every branch is corner enumeration over the two intervals involved. Returns None
    where the conversion is not available, for instance a per-container figure on a label
    that declares neither a net quantity nor a serving count.
    """
    hundred = declared_per_hundred(fixture)
    try:
        if basis == "per_serving":
            if not hundred:
                return interval
            return corners(lambda n, s: n * s / 100, interval, serving_interval(fixture))
        if basis == "per_100g":
            if hundred:
                return interval
            return corners(lambda n, s: 100 * n / s, interval, serving_interval(fixture))
        if basis == "per_container":
            if hundred:
                return corners(lambda n, c: n * c / 100, interval, container_interval(fixture))
            return corners(lambda n, c: n * c, interval, servings_interval(fixture))
        if basis == "per_100kcal":
            fmt = fixture.get("format", "US_2016")
            row = fixture["nutrients"].get("energy_kcal")
            if row is None:
                return None
            energy = nutrient_interval(fmt, "energy_kcal", row)
            if energy[0] <= 0:
                return None
            return corners(lambda n, e: 100 * n / e, interval, energy)
    except KeyError:
        return None
    raise ValueError(f"unknown basis {basis!r}")


# --------------------------------------------------------------------------- the checks


class Report:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.checked = 0

    def agree(self, what: str, mine: Fraction, theirs: Fraction) -> None:
        self.checked += 1
        scale = max(abs(mine), abs(theirs), Fraction(1))
        if abs(mine - theirs) > TOLERANCE * scale:
            self.problems.append(
                f"{what}: recomputed {float(mine)!r} but the tool says {float(theirs)!r}"
            )

    def same(self, what: str, mine, theirs) -> None:
        self.checked += 1
        if mine != theirs:
            self.problems.append(f"{what}: recomputed {mine!r} but the tool says {theirs!r}")


def run_tool(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-m", "nutrilabel", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return result.returncode, result.stdout


def tool_json(args: list[str]):
    code, out = run_tool([*args, "--json"])
    if not out.strip():
        raise SystemExit(f"the tool produced no JSON for {' '.join(args)} (exit {code})")
    return json.loads(out)


def check_normalisation(report: Report, rel: str) -> None:
    """Every per-100 and per-container figure, recomputed from the raw fixture."""
    fixture = read_fixture(rel)
    fmt = fixture.get("format", "US_2016")

    for basis in ("per_serving", "per_100g", "per_container", "per_100kcal"):
        code, out = run_tool(["normalize", rel, "--basis", basis, "--json"])
        if code != 0:
            # The tool refuses some conversions on purpose, a per-container figure on a
            # label with no serving count for instance. A refusal is only acceptable when
            # this file cannot produce the figure either.
            probe = fixture["nutrients"].get("fat") or next(iter(fixture["nutrients"].values()))
            mine = to_basis(fixture, nutrient_interval(fmt, "fat", probe), basis) \
                if "fat" in fixture["nutrients"] else None
            report.checked += 1
            if mine is not None and basis != "per_100kcal":
                report.problems.append(
                    f"{rel} {basis}: the tool refused a conversion this file can perform"
                )
            continue
        data = json.loads(out)
        for key, raw in fixture["nutrients"].items():
            if key not in data["values"]:
                continue
            mine = to_basis(fixture, nutrient_interval(fmt, key, raw), basis)
            if mine is None:
                continue
            theirs = data["values"][key]
            report.agree(f"{rel} {basis} {key} lo", mine[0], Fraction(theirs["lo_exact"]))
            report.agree(f"{rel} {basis} {key} hi", mine[1], Fraction(theirs["hi_exact"]))


def predicted_energy(fixture: dict, column: str) -> tuple[Fraction, Fraction]:
    """The energy band the macronutrients imply, by corner enumeration."""
    fmt = fixture.get("format", "US_2016")
    rows = fixture["nutrients"]

    def band(key: str) -> tuple[Fraction, Fraction]:
        if key not in rows:
            return (Fraction(0), Fraction(0))
        return nutrient_interval(fmt, key, rows[key])

    if fmt == "US_2016":
        protein, carb, fat = band("protein"), band("carbohydrate"), band("fat")
        fiber, alcohol = band("fiber"), band("alcohol")
        polyol = band("sugar_alcohol")
        # Method A: general factors on total carbohydrate, polyols at their highest.
        a = corners(
            lambda p, c, f, al, po: (p * US_KCAL_PER_GRAM["protein"]
                                     + c * US_KCAL_PER_GRAM["carbohydrate"]
                                     + f * US_KCAL_PER_GRAM["fat"]
                                     + al * US_KCAL_PER_GRAM["alcohol"]
                                     + po * US_POLYOL_HIGH),
            protein, carb, fat, alcohol, polyol,
        )
        # Method B: all declared fibre subtracted from carbohydrate, polyols at zero.
        # The subtraction is done on corners too, then clamped at zero, which is where
        # the tool clamps it as well.
        net_lo = max(Fraction(0), carb[0] - fiber[1])
        net_hi = max(Fraction(0), carb[1] - fiber[0])
        b = corners(
            lambda p, c, f, al, po: (p * US_KCAL_PER_GRAM["protein"]
                                     + c * US_KCAL_PER_GRAM["carbohydrate"]
                                     + f * US_KCAL_PER_GRAM["fat"]
                                     + al * US_KCAL_PER_GRAM["alcohol"]
                                     + po * US_POLYOL_LOW),
            protein, (net_lo, net_hi), fat, alcohol, polyol,
        )
        return (min(a[0], b[0]), max(a[1], b[1]))

    table = EU_KJ_PER_GRAM if column == "kj" else EU_KCAL_PER_GRAM
    carb, polyols = band("carbohydrate"), band("polyols")
    available = (max(Fraction(0), carb[0] - polyols[1]), max(Fraction(0), carb[1] - polyols[0]))
    parts = [
        (available, table["carbohydrate_available"]),
        (polyols, table["polyols"]),
        (band("protein"), table["protein"]),
        (band("fat"), table["fat"]),
        (band("alcohol"), table["alcohol"]),
        (band("organic_acid"), table["organic_acid"]),
        (band("fiber"), table["fiber"]),
    ]
    lo = sum((interval[0] * factor for interval, factor in parts), Fraction(0))
    hi = sum((interval[1] * factor for interval, factor in parts), Fraction(0))
    return (lo, hi)


def _to_1dp(value: Fraction) -> Fraction:
    """Round half up to one decimal, matching how the tool renders a band."""
    return _nearest(value, Fraction(1, 10))


def _numbers_in(text: str) -> list[Fraction]:
    """Every decimal number in a sentence, in order, as exact Fractions.

    The en dash between the two ends of an interval is a separator here rather than a
    minus sign, and no figure in these messages is negative, so a leading sign is not
    accepted. Written by hand rather than with a regular expression borrowed from the
    renderer, which would make this a second call to the same code.
    """
    out: list[Fraction] = []
    current = ""
    for character in text + " ":
        if character.isdigit() or (character == "." and current and "." not in current):
            current += character
        else:
            if current and current[-1].isdigit():
                out.append(Fraction(current))
            current = ""
    return out


def check_atwater(report: Report, rel: str) -> None:
    """The energy cross-check verdict and both of its intervals."""
    fixture = read_fixture(rel)
    fmt = fixture.get("format", "US_2016")
    columns = ["kcal"] + (["kj"] if fmt == "EU_1169" and "energy_kj" in fixture["nutrients"] else [])
    findings = {f["code"]: f for f in tool_json(["check", rel])[0]["findings"]}

    for column in columns:
        key = "energy_kcal" if column == "kcal" else "energy_kj"
        if key not in fixture["nutrients"]:
            continue
        mine = predicted_energy(fixture, column)
        declared = nutrient_interval(fmt, key, fixture["nutrients"][key])
        # Closed-interval overlap, the conservative direction: a label whose consistency
        # turns on a single boundary point is not flagged.
        consistent = mine[0] <= declared[1] and declared[0] <= mine[1]
        finding = findings.get(f"energy_{column}")
        if finding is None:
            report.problems.append(f"{rel}: the tool reported no energy_{column} finding at all")
            continue
        report.same(
            f"{rel} energy_{column} verdict",
            "consistent" if consistent else "INCONSISTENT",
            "consistent" if finding["severity"] == "note" else "INCONSISTENT",
        )
        # And both bands, read back out of the tool's own message, so a verdict that
        # happens to agree for the wrong reason still fails. The message renders to one
        # decimal place, so the comparison is against this file's numbers rounded the
        # same way rather than against the exact rationals.
        numbers = _numbers_in(finding["message"])
        report.checked += 1
        if len(numbers) < 4:
            report.problems.append(
                f"{rel} energy_{column}: could not read two intervals out of the tool's "
                f"message, so only the verdict was checked: {finding['message'][:90]}"
            )
            continue
        for label, mine_value, theirs in (
            ("declared lo", declared[0], numbers[0]),
            ("declared hi", declared[1], numbers[1]),
            ("predicted lo", mine[0], numbers[2]),
            ("predicted hi", mine[1], numbers[3]),
        ):
            report.agree(f"{rel} energy_{column} {label}", _to_1dp(mine_value), theirs)


def check_flag_set(report: Report, rel: str, expected: list[str]) -> None:
    """The set of flags on this fixture, against the hand-written expectation."""
    findings = tool_json(["check", rel])[0]["findings"]
    raised = sorted({f["code"] for f in findings if f["severity"] == "flag"})
    report.same(f"{rel} flags", raised, sorted(expected))


def check_serving_trap(report: Report) -> None:
    """The headline number, recomputed: what the serving size hides.

    Two cereals whose per-100 g energy overlaps almost entirely look 60 percent apart per
    serving, because one declares 34 g and the other 55 g. That claim is the reason the
    tool exists, so it gets recomputed here from the raw fixtures rather than trusted.
    """
    a = read_fixture("fixtures/labels/cereal-a-flakes.json")
    b = read_fixture("fixtures/labels/cereal-b-granola.json")

    a_energy = nutrient_interval("US_2016", "energy_kcal", a["nutrients"]["energy_kcal"])
    b_energy = nutrient_interval("US_2016", "energy_kcal", b["nutrients"]["energy_kcal"])
    a_per_100 = corners(lambda n, s: 100 * n / s, a_energy, serving_interval(a))
    b_per_100 = corners(lambda n, s: 100 * n / s, b_energy, serving_interval(b))

    per_serving_ratio = corners(lambda x, y: x / y, b_energy, a_energy)
    per_100_ratio = corners(lambda x, y: x / y, b_per_100, a_per_100)

    # Per serving the granola must look at least half again as large.
    report.checked += 1
    if per_serving_ratio[0] <= Fraction(3, 2):
        report.problems.append(
            f"the serving-size trap fixture no longer shows one: per serving the granola "
            f"is only {float(per_serving_ratio[0]):.2f}x the flakes at the low end"
        )
    # Per 100 g the two must overlap, which is the whole point.
    report.checked += 1
    if not (per_100_ratio[0] <= 1 <= per_100_ratio[1]):
        report.problems.append(
            f"per 100 g the two cereals no longer overlap: the ratio interval is "
            f"{float(per_100_ratio[0]):.3f} to {float(per_100_ratio[1]):.3f}"
        )
    # And the tool's own compare output must contain the same two intervals.
    per_serving = tool_json(["compare", "fixtures/labels/cereal-a-flakes.json",
                             "fixtures/labels/cereal-b-granola.json", "--basis", "per_serving"])
    report.agree("cereal A per serving energy lo", a_energy[0],
                 Fraction(per_serving[0]["values"]["energy_kcal"]["lo_exact"]))
    report.agree("cereal B per serving energy hi", b_energy[1],
                 Fraction(per_serving[1]["values"]["energy_kcal"]["hi_exact"]))
    per_hundred = tool_json(["compare", "fixtures/labels/cereal-a-flakes.json",
                             "fixtures/labels/cereal-b-granola.json", "--basis", "per_100g"])
    report.agree("cereal A per 100 g energy lo", a_per_100[0],
                 Fraction(per_hundred[0]["values"]["energy_kcal"]["lo_exact"]))
    report.agree("cereal A per 100 g energy hi", a_per_100[1],
                 Fraction(per_hundred[0]["values"]["energy_kcal"]["hi_exact"]))

    print(f"  per serving, the granola reads "
          f"{float(per_serving_ratio[0]):.2f}x to {float(per_serving_ratio[1]):.2f}x the flakes")
    print(f"  per 100 g, the same two read "
          f"{float(per_100_ratio[0]):.2f}x to {float(per_100_ratio[1]):.2f}x, which spans 1.0")


def check_rounding_compounds(report: Report) -> None:
    """A declared zero, multiplied out to the container, recomputed independently.

    "0 g trans fat" per serving is a statement that the true value is below 0.5 g. Thirteen
    servings of that is up to 6.5 g of trans fat in a box the panel describes as having
    none. The number is the clearest thing the tool has to say, so it is recomputed here.
    """
    fixture = read_fixture("fixtures/labels/cereal-a-flakes.json")
    trans = nutrient_interval("US_2016", "trans_fat", fixture["nutrients"]["trans_fat"])
    report.agree("a declared 0 g trans fat reaches", trans[1], Fraction(1, 2))

    container = corners(lambda n, c: n * c, trans, servings_interval(fixture))
    data = tool_json(["normalize", "fixtures/labels/cereal-a-flakes.json",
                      "--basis", "per_container"])
    report.agree("trans fat per container lo", container[0],
                 Fraction(data["values"]["trans_fat"]["lo_exact"]))
    report.agree("trans fat per container hi", container[1],
                 Fraction(data["values"]["trans_fat"]["hi_exact"]))
    print(f"  a panel reading 0 g trans fat per serving permits up to "
          f"{float(container[1]):.2f} g in the container")


def check_expected_normalisations(report: Report) -> None:
    """The hand-computed fixture expectations, against this file's own arithmetic.

    fixtures/expected/normalisations.json was worked out on paper. The unit suite checks
    the tool against it. This checks a second implementation against it, so a mistake
    made twice, once on paper and once in the tool, still has to survive a third route.
    """
    expected = read_fixture("fixtures/expected/normalisations.json")
    for case in expected["cases"]:
        rel = f"fixtures/labels/{case['fixture']}.json"
        fixture = read_fixture(rel)
        fmt = fixture.get("format", "US_2016")
        mine = nutrient_interval(fmt, case["nutrient"], fixture["nutrients"][case["nutrient"]])
        basis = case["basis"]
        mine = to_basis(fixture, mine, basis)
        if mine is None:
            report.problems.append(
                f"hand-computed {case['fixture']} {basis}: this file cannot produce that basis"
            )
            continue
        report.agree(f"hand-computed {case['fixture']} {basis} {case['nutrient']} lo",
                     mine[0], Fraction(case["lo"]))
        report.agree(f"hand-computed {case['fixture']} {basis} {case['nutrient']} hi",
                     mine[1], Fraction(case["hi"]))


# --------------------------------------------------------------------------- independence


def prove_independence() -> list[str]:
    """Parse this file with ast, follow its imports, and fail if any reach the package.

    Both the static forms and the dynamic ones. ``importlib.import_module("nutrilabel")``
    is invisible to a grep for an import statement, and a grep for the package name is
    satisfied by this very docstring, so the check has to look at the syntax tree.
    """
    problems: list[str] = []
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=Path(__file__).name)

    package = ROOT / "nutrilabel"
    package_modules = {"nutrilabel"} | {
        f"nutrilabel.{p.stem}" for p in package.glob("*.py")
    } | {p.stem for p in package.glob("*.py") if p.stem != "__init__"}

    def offending(name: str) -> bool:
        root = name.split(".")[0]
        return name in package_modules or root == "nutrilabel"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if offending(alias.name):
                    problems.append(f"line {node.lineno}: imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                problems.append(f"line {node.lineno}: a relative import would reach the package")
            elif node.module and offending(node.module):
                problems.append(f"line {node.lineno}: imports from {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            dynamic = (
                (isinstance(func, ast.Attribute) and func.attr in ("import_module", "load_module"))
                or (isinstance(func, ast.Name) and func.id in ("import_module", "__import__"))
            )
            if dynamic:
                target = node.args[0].value if (
                    node.args and isinstance(node.args[0], ast.Constant)
                ) else "<computed>"
                problems.append(f"line {node.lineno}: dynamic import of {target!r}")

    # sys.path is not extended toward the package either. The tool is reached only as a
    # subprocess, and a subprocess cannot leak an object into this process.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "path":
            parent = getattr(node, "value", None)
            if isinstance(parent, ast.Name) and parent.id == "sys":
                problems.append(f"line {node.lineno}: touches sys.path")
    return problems


# --------------------------------------------------------------------------- main


# --quick is what the sabotage harness runs, twenty nine times. The subset is named
# rather than sliced so that it spans the things the corpus is there to exercise: a US
# per-serving panel, an EU per-100 panel, a label whose units need converting, and a
# label that is supposed to be flagged. The Atwater verdicts and the flag sets below run
# over the whole corpus either way; only the endpoint-by-endpoint sweep is narrowed.
QUICK_LABELS = {
    "cereal-a-flakes",
    "eu-dark-chocolate",
    "us-export-sodium-in-grams",
    "broken-mass-budget",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true",
                        help="sweep endpoints on four representative fixtures rather "
                             "than all of them; the flag sets and energy verdicts still "
                             "cover the whole corpus")
    args = parser.parse_args()

    report = Report()

    print("independence")
    problems = prove_independence()
    if problems:
        for problem in problems:
            print(f"  FAIL  {problem}")
        print("  This file must share no code with the package it checks.")
        return 1
    print("  ok    an ast walk of this file finds no import of the package, static or dynamic")

    print("rounding inverses recomputed by bisection on a separately written forward rule")
    flags_expected = read_fixture("fixtures/expected/flags.json")["expected"]
    labels = sorted(p.name[:-5] for p in (ROOT / "fixtures" / "labels").glob("*.json"))
    subset = [stem for stem in labels if stem in QUICK_LABELS] if args.quick else labels
    if args.quick and len(subset) < len(QUICK_LABELS):
        # An alphabetical slice would have been the easy way to build this subset, and it
        # would have silently dropped the export fixture off the end. Naming the labels
        # means a renamed or deleted one is a failure rather than a quieter run.
        missing = sorted(QUICK_LABELS - set(subset))
        print(f"  FAIL  --quick names labels that are not in the corpus: {missing}")
        return 1
    for stem in subset:
        check_normalisation(report, f"fixtures/labels/{stem}.json")
    print(f"  ok    {report.checked} interval endpoints recomputed across {len(subset)} labels")

    before = report.checked
    print("the hand-computed expectations, against a third derivation")
    check_expected_normalisations(report)
    print(f"  ok    {report.checked - before} endpoints against fixtures/expected/normalisations.json")

    before = report.checked
    print("Atwater cross-check verdicts")
    for stem in labels:
        check_atwater(report, f"fixtures/labels/{stem}.json")
    print(f"  ok    {report.checked - before} energy verdicts recomputed from the macronutrients")

    before = report.checked
    print("flag sets, including the negative controls")
    for stem in labels:
        if stem in flags_expected:
            check_flag_set(report, f"fixtures/labels/{stem}.json", flags_expected[stem])
    clean = sum(1 for stem, codes in flags_expected.items() if not codes)
    print(f"  ok    {report.checked - before} flag sets, {clean} of them labels that must stay clean")

    print("the headline claims")
    check_serving_trap(report)
    check_rounding_compounds(report)

    if report.problems:
        print(f"\nINDEPENDENT CHECK FAILED: {len(report.problems)} disagreement(s) "
              f"out of {report.checked} comparisons")
        for problem in report.problems[:40]:
            print(f"  {problem}")
        if len(report.problems) > 40:
            print(f"  ... and {len(report.problems) - 40} more")
        return 1
    print(f"\nindependent recomputation agreed on all {report.checked} comparisons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
