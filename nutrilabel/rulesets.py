"""Rule tables for the US and EU label formats.

Everything in this file is data. It is kept in one module so that a reader can check
it against the regulation without reading any logic, and so that a correction is a
one-line edit rather than a hunt through the code.

Each rule carries a ``citation`` and a ``basis``. ``basis="cited"`` means the rule is
written down in the named source. ``basis="guidance"`` means it comes from a
non-binding guidance document rather than from the legal text. ``basis="inferred"``
means we could not point at a source and reconstructed the step from the printed
precision. The CLI prints the basis alongside every interval, because an interval that
rests on a guess must not look like one that rests on a regulation.

Sources:

* 21 CFR 101.9, Nutrition labeling of food. Rounding rules are in paragraph (c), the
  reference daily intakes for vitamins and minerals in (c)(8)(iv), and the daily
  reference values for the macronutrients in (c)(9). The values below are the ones set
  by the 2016 final rule, 81 FR 33742, which added Added Sugars and potassium to the
  mandatory panel, removed the vitamin A and vitamin C requirement, and moved sodium
  from 2400 mg to 2300 mg and fibre from 25 g to 28 g.
* Regulation (EU) No 1169/2011 on the provision of food information to consumers.
  Annex I point 11 defines salt as sodium multiplied by 2.5. Annex XIV lists the energy
  conversion factors. Annex XIII lists the reference intakes.
* European Commission guidance for competent authorities on tolerances, December 2012.
  The rounding steps for EU declarations come from its rounding annex and are guidance
  rather than statute, so they are marked ``guidance``.

Check the current eCFR and EUR-Lex text before relying on any figure here for a
regulatory purpose. This is a comparison tool, not a compliance service.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Mapping, Optional

from .rounding import RoundingRule, Zero, less_than, make_rule, nearest

CFR = "21 CFR 101.9(c)"
EU_ROUNDING = "EC guidance on tolerances (Dec 2012), rounding annex"

# ---------------------------------------------------------------------------
# United States, 21 CFR 101.9 as amended by the 2016 final rule
# ---------------------------------------------------------------------------

_US_FAT = [
    # "less than 0.5 gram shall be expressed as zero", then 0.5 g increments below
    # 5 g and 1 g increments at or above 5 g.
    ("0.5", (Zero(), less_than("0.5"))),
    ("5", (nearest("0.5"),)),
    (None, (nearest("1"),)),
]

# Total carbohydrate, fibre, sugars, added sugars, sugar alcohol and protein all share
# one rule: zero below 0.5 g, an optional "less than 1 gram" statement between 0.5 and
# 1 g, and whole grams above that.
_US_GRAM = [
    ("0.5", (Zero(),)),
    ("1", (nearest("1"), less_than("1"))),
    (None, (nearest("1"),)),
]


def _us_fat(name: str) -> RoundingRule:
    return make_rule(name, "g", f"{CFR}(2)", _US_FAT)


def _us_gram(name: str, paragraph: str) -> RoundingRule:
    return make_rule(name, "g", f"{CFR}{paragraph}", _US_GRAM)


US_RULES: Mapping[str, RoundingRule] = {
    "energy_kcal": make_rule(
        "energy",
        "kcal",
        f"{CFR}(1)",
        [
            # Below 5 calories the content may be expressed as zero. "May" rather than
            # "shall", so both 0 and the 5-increment rounding are permitted here, and
            # the inverse of a declared 0 is the whole of [0, 5).
            ("5", (Zero(), nearest("5"))),
            # Nearest 5 up to and including 50 calories.
            ("50", (nearest("5"),)),
            # Nearest 10 above 50. This is why a declared 230 kcal spans 225 to 235
            # while a declared 45 kcal spans only 42.5 to 47.5.
            (None, (nearest("10"),)),
        ],
    ),
    "fat": _us_fat("total fat"),
    "saturated_fat": _us_fat("saturated fat"),
    "trans_fat": _us_fat("trans fat"),
    "monounsaturated_fat": _us_fat("monounsaturated fat"),
    "polyunsaturated_fat": _us_fat("polyunsaturated fat"),
    "cholesterol": make_rule(
        "cholesterol",
        "mg",
        f"{CFR}(3)",
        [
            ("2", (Zero(),)),
            # Between 2 and 5 mg the panel must say "less than 5 mg" rather than a
            # number, which is a strictly more informative declaration than "5".
            ("5", (less_than("5"),)),
            (None, (nearest("5"),)),
        ],
    ),
    "sodium": make_rule(
        "sodium",
        "mg",
        f"{CFR}(4)",
        [
            ("5", (Zero(),)),
            ("140", (nearest("5"),)),
            (None, (nearest("10"),)),
        ],
    ),
    # These five share paragraph (c)(6) and its subparagraphs. The subparagraph letters
    # are not reproduced because we are not confident of them; the rule itself is the
    # same for all of them.
    "carbohydrate": _us_gram("total carbohydrate", "(6)"),
    "fiber": _us_gram("dietary fibre", "(6)"),
    "total_sugars": _us_gram("total sugars", "(6)"),
    "added_sugars": _us_gram("added sugars", "(6)"),
    "sugar_alcohol": _us_gram("sugar alcohol", "(6)"),
    "protein": _us_gram("protein", "(7)"),
}

# Serving size in grams or millilitres, and servings per container. Both are rounded,
# and both multiply into every per-container figure, so their intervals matter as much
# as the nutrient ones. 21 CFR 101.9(b) sets these out; the paragraph numbering within
# (b) is not reproduced here because we are not confident of it.
US_SERVING_SIZE_RULE = make_rule(
    "serving size",
    "g",
    "21 CFR 101.9(b), metric quantity of the serving",
    [
        # Under 5 g the quantity is given in 0.5 g increments, at or above 5 g to the
        # nearest whole gram.
        ("5", (nearest("0.5"),)),
        (None, (nearest("1"),)),
    ],
    notes="Cited at section level. The subparagraph within (b) is not reproduced "
          "because we are not confident of its number.",
)

US_SERVINGS_PER_CONTAINER_RULE = make_rule(
    "servings per container",
    "servings",
    "21 CFR 101.9(b), number of servings per container",
    [
        # Below 2 servings, half-serving precision. Between 2 and 5 servings the count
        # is given to the nearest half serving, which is where the "servings per
        # container: 2.5" of the task brief comes from. Above 5, whole servings.
        ("5", (nearest("0.5"),)),
        (None, (nearest("1"),)),
    ],
    notes="Cited at section level. The subparagraph within (b) is not reproduced "
          "because we are not confident of its number.",
)

# ---------------------------------------------------------------------------
# European Union, Regulation 1169/2011 with the 2012 rounding guidance
# ---------------------------------------------------------------------------

_EU_MACRO = [
    ("0.5", (Zero(), less_than("0.5"))),
    ("10", (nearest("0.1"),)),
    (None, (nearest("1"),)),
]


def _eu_macro(name: str) -> RoundingRule:
    return make_rule(name, "g", EU_ROUNDING, _EU_MACRO, basis="guidance")


EU_RULES: Mapping[str, RoundingRule] = {
    "energy_kj": make_rule("energy", "kJ", EU_ROUNDING, [(None, (nearest("1"),))], basis="guidance"),
    "energy_kcal": make_rule("energy", "kcal", EU_ROUNDING, [(None, (nearest("1"),))], basis="guidance"),
    "fat": _eu_macro("fat"),
    "saturated_fat": _eu_macro("of which saturates"),
    "monounsaturated_fat": _eu_macro("of which monounsaturates"),
    "polyunsaturated_fat": _eu_macro("of which polyunsaturates"),
    "carbohydrate": _eu_macro("carbohydrate"),
    "total_sugars": _eu_macro("of which sugars"),
    "polyols": _eu_macro("polyols"),
    "starch": _eu_macro("starch"),
    "fiber": _eu_macro("fibre"),
    "protein": _eu_macro("protein"),
    "salt": make_rule(
        "salt",
        "g",
        EU_ROUNDING,
        [
            ("0.0125", (Zero(), less_than("0.01"))),
            ("1", (nearest("0.01"),)),
            (None, (nearest("0.1"),)),
        ],
        basis="guidance",
    ),
}

EU_SERVING_SIZE_RULE = make_rule(
    "serving size",
    "g",
    "no EU rounding rule cited for the stated portion size",
    [(None, (nearest("1"),))],
    basis="inferred",
    notes="EU portion sizes are voluntary and their precision is not regulated.",
)

# ---------------------------------------------------------------------------
# Reference amounts
# ---------------------------------------------------------------------------

# 21 CFR 101.9(c)(9) daily reference values and (c)(8)(iv) reference daily intakes, as
# set by the 2016 final rule. Adults and children four years and older.
US_DAILY_VALUES: Mapping[str, tuple[Fraction, str]] = {
    "fat": (Fraction(78), "g"),
    "saturated_fat": (Fraction(20), "g"),
    "cholesterol": (Fraction(300), "mg"),
    "sodium": (Fraction(2300), "mg"),
    "carbohydrate": (Fraction(275), "g"),
    "fiber": (Fraction(28), "g"),
    "added_sugars": (Fraction(50), "g"),
    "protein": (Fraction(50), "g"),
    "vitamin_d": (Fraction(20), "mcg"),
    "calcium": (Fraction(1300), "mg"),
    "iron": (Fraction(18), "mg"),
    "potassium": (Fraction(4700), "mg"),
    "vitamin_a": (Fraction(900), "mcg RAE"),
    "vitamin_c": (Fraction(90), "mg"),
    "vitamin_e": (Fraction(15), "mg"),
    "vitamin_k": (Fraction(120), "mcg"),
    "thiamin": (Fraction(12, 10), "mg"),
    "riboflavin": (Fraction(13, 10), "mg"),
    "niacin": (Fraction(16), "mg NE"),
    "vitamin_b6": (Fraction(17, 10), "mg"),
    "folate": (Fraction(400), "mcg DFE"),
    "vitamin_b12": (Fraction(24, 10), "mcg"),
    "biotin": (Fraction(30), "mcg"),
    "pantothenic_acid": (Fraction(5), "mg"),
    "phosphorus": (Fraction(1250), "mg"),
    "iodine": (Fraction(150), "mcg"),
    "magnesium": (Fraction(420), "mg"),
    "zinc": (Fraction(11), "mg"),
    "selenium": (Fraction(55), "mcg"),
    "copper": (Fraction(9, 10), "mg"),
    "manganese": (Fraction(23, 10), "mg"),
    "chromium": (Fraction(35), "mcg"),
    "molybdenum": (Fraction(45), "mcg"),
    "chloride": (Fraction(2300), "mg"),
    "choline": (Fraction(550), "mg"),
}

# Nutrients whose declaration the 2016 rule made mandatory, and the two it made
# voluntary. Worth stating explicitly because a label printed before 2016 carries
# vitamin A and vitamin C and no added sugars or potassium, and a tool that silently
# treats the two panels as the same thing will compare a 2015 label against a 2020 one
# and report a difference that is an artefact of the format change.
US_MANDATORY_MICRONUTRIENTS = ("vitamin_d", "calcium", "iron", "potassium")
US_VOLUNTARY_SINCE_2016 = ("vitamin_a", "vitamin_c")
US_ADDED_BY_2016_RULE = ("added_sugars", "potassium", "vitamin_d")

# Annex XIII of Regulation (EU) No 1169/2011, reference intakes for an average adult.
EU_REFERENCE_INTAKES: Mapping[str, tuple[Fraction, str]] = {
    "energy_kj": (Fraction(8400), "kJ"),
    "energy_kcal": (Fraction(2000), "kcal"),
    "fat": (Fraction(70), "g"),
    "saturated_fat": (Fraction(20), "g"),
    "carbohydrate": (Fraction(260), "g"),
    "total_sugars": (Fraction(90), "g"),
    "protein": (Fraction(50), "g"),
    "salt": (Fraction(6), "g"),
}

# ---------------------------------------------------------------------------
# Energy conversion factors
# ---------------------------------------------------------------------------

# Annex XIV of Regulation (EU) No 1169/2011. Note that the kJ and kcal columns are not
# related by a single constant: 17/4 is 4.25 for carbohydrate, 37/9 is 4.111 for fat and
# 8/2 is 4.0 for fibre. So an EU label's kJ figure divided by 4.184 does not give its
# kcal figure, and a tool that converts one into the other will report a discrepancy
# that is an artefact of its own arithmetic. Both columns are kept and used separately.
EU_ENERGY_FACTORS: Mapping[str, tuple[Fraction, Fraction]] = {
    "carbohydrate_available": (Fraction(17), Fraction(4)),
    "polyols": (Fraction(10), Fraction(24, 10)),
    "protein": (Fraction(17), Fraction(4)),
    "fat": (Fraction(37), Fraction(9)),
    "salatrims": (Fraction(25), Fraction(6)),
    "alcohol": (Fraction(29), Fraction(7)),
    "organic_acid": (Fraction(13), Fraction(3)),
    "fiber": (Fraction(8), Fraction(2)),
    "erythritol": (Fraction(0), Fraction(0)),
}

# General Atwater factors, kcal per gram. 21 CFR 101.9(c)(1)(i) permits several methods
# of calculating the calorie figure, and they do not all give the same answer, which is
# the honest reason a US energy cross-check needs a band rather than a point.
US_ATWATER = {
    "protein": Fraction(4),
    "carbohydrate": Fraction(4),
    "fat": Fraction(9),
    "alcohol": Fraction(7),
    # Sugar alcohols vary by species, from 0 for erythritol to about 3 for maltitol.
    # The band below uses the extremes rather than a single value.
    "sugar_alcohol_low": Fraction(0),
    "sugar_alcohol_high": Fraction(3),
}

# The thermochemical calorie, for converting a physical energy quantity. Not for
# converting a label's kJ figure into its kcal figure; see EU_ENERGY_FACTORS above.
KJ_PER_KCAL = Fraction(4184, 1000)

# Annex I point 11 of Regulation (EU) No 1169/2011.
SALT_PER_SODIUM = Fraction(25, 10)


def rules_for(label_format: str) -> Mapping[str, RoundingRule]:
    if label_format == "US_2016":
        return US_RULES
    if label_format == "EU_1169":
        return EU_RULES
    raise ValueError(
        f"unknown label format {label_format!r}; expected 'US_2016' or 'EU_1169'"
    )


def serving_size_rule_for(label_format: str) -> RoundingRule:
    if label_format == "US_2016":
        return US_SERVING_SIZE_RULE
    if label_format == "EU_1169":
        return EU_SERVING_SIZE_RULE
    raise ValueError(f"unknown label format {label_format!r}")


def daily_values_for(label_format: str) -> Mapping[str, tuple[Fraction, str]]:
    if label_format == "US_2016":
        return US_DAILY_VALUES
    if label_format == "EU_1169":
        return EU_REFERENCE_INTAKES
    raise ValueError(f"unknown label format {label_format!r}")


def canonical_unit(label_format: str, nutrient: str) -> Optional[str]:
    rule = rules_for(label_format).get(nutrient)
    if rule is not None:
        return rule.unit
    reference = daily_values_for(label_format).get(nutrient)
    if reference is not None:
        return reference[1].split()[0]
    return None
