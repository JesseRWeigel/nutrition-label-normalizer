"""Internal consistency checks on a single label.

Every check here compares the label against itself. None of them can tell you whether
a package is mislabelled, because the only evidence available is the panel, and a panel
that is wrong in a self-consistent way passes all of them.

The shared discipline is that a flag is raised only when no assignment of true values
consistent with the printed panel could satisfy the relation. That is what makes the
negative controls in the fixture corpus meaningful: a checker that flags a label
because a midpoint arithmetic came out slightly off would flag most real labels, and a
checker that flags everything is indistinguishable from one that understands nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Optional

from .energy import check_energy, kj_kcal_ratio_note
from .interval import Interval
from .label import Label
from .normalize import normalize, percent_daily_value
from .rulesets import SALT_PER_SODIUM, daily_values_for
from .rounding import round_half_up

# Severity "flag" means the label contradicts itself and something is wrong.
# Severity "note" means the finding is informative and expected on correct labels.
FLAG = "flag"
NOTE = "note"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str

    def to_json(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message}


def _interval(label: Label, key: str) -> Optional[Interval]:
    nutrient = label.nutrients.get(key)
    return None if nutrient is None else nutrient.interval


def check_component_sums(label: Label) -> list[Finding]:
    """Sub-rows cannot exceed the row they are part of."""
    findings: list[Finding] = []
    # Which rows sit inside "carbohydrate" is a genuine difference between the two
    # formats, not a naming detail. A US total carbohydrate figure includes dietary
    # fibre and sugar alcohols. An EU carbohydrate figure excludes fibre, which is
    # declared as its own row alongside it. Applying the US relation to an EU panel
    # flags every high-fibre product in Europe, which is how this was found: the dark
    # chocolate fixture, a correct label, came up red.
    if label.label_format == "US_2016":
        carbohydrate_parts = ["total_sugars", "fiber", "sugar_alcohol"]
        carbohydrate_description = "sugars, fibre and sugar alcohols"
    else:
        carbohydrate_parts = ["total_sugars", "polyols", "starch"]
        carbohydrate_description = "sugars, polyols and starch"
    relations = [
        ("fat_components", "fat",
         ["saturated_fat", "trans_fat", "monounsaturated_fat", "polyunsaturated_fat"],
         "the fatty acid rows"),
        ("carbohydrate_components", "carbohydrate",
         carbohydrate_parts, carbohydrate_description),
    ]
    for code, whole_key, part_keys, description in relations:
        whole = _interval(label, whole_key)
        parts = [(key, _interval(label, key)) for key in part_keys]
        present = [(key, value) for key, value in parts if value is not None]
        if whole is None or not present:
            continue
        floor = sum((value.lo for _, value in present), Fraction(0))
        if floor > whole.hi:
            names = ", ".join(key.replace("_", " ") for key, _ in present)
            findings.append(Finding(
                code,
                FLAG,
                f"{description} ({names}) must total at least {float(floor):g} g, which "
                f"exceeds the largest {whole_key.replace('_', ' ')} consistent with the "
                f"printed figure, {float(whole.hi):g} g.",
            ))
    added = _interval(label, "added_sugars")
    total = _interval(label, "total_sugars")
    if added is not None and total is not None and added.lo > total.hi:
        findings.append(Finding(
            "added_sugars_exceed_total",
            FLAG,
            f"added sugars are at least {float(added.lo):g} g, which is more than the "
            f"largest total sugars consistent with the panel, {float(total.hi):g} g.",
        ))
    return findings


def check_mass_budget(label: Label) -> list[Finding]:
    """The macronutrients cannot outweigh the food they are in.

    On a per-100 g basis this is a hard bound: fat plus carbohydrate plus protein plus
    ash plus water is 100 g, so the three declared macros alone cannot exceed 100. On a
    per-serving basis the same bound applies against the serving mass. This catches a
    transposed serving size, which is otherwise invisible.
    """
    if label.medium_unit != "g":
        return []
    try:
        normalized = normalize(label, "per_100g")
    except Exception:  # a missing serving size is reported by other checks
        return []
    keys = ["fat", "carbohydrate", "protein"]
    present = [normalized.values[key] for key in keys if key in normalized.values]
    if len(present) < 2:
        return []
    floor = sum((value.interval.lo for value in present), Fraction(0))
    if floor > 100:
        return [Finding(
            "mass_budget",
            FLAG,
            f"per 100 g, fat, carbohydrate and protein must total at least "
            f"{float(floor):.1f} g, which is more than 100 g of food. Either the serving "
            f"size or one of the macronutrient rows is wrong.",
        )]
    return []


def check_container_arithmetic(label: Label) -> list[Finding]:
    """Net quantity against servings multiplied by serving size.

    Both are printed, both are rounded, and they are supposed to describe the same
    package. When their intervals do not overlap the package cannot contain the number
    of servings it claims, and the per-container column of the panel is wrong.
    """
    if label.net_quantity is None or label.serving is None or label.servings_per_container is None:
        return []
    try:
        derived = label.serving_interval() * label.servings_interval()
        declared, _ = label.container_interval()
    except Exception as error:
        return [Finding("container_arithmetic", FLAG, f"could not compute: {error}")]
    if derived.overlaps(declared):
        return [Finding(
            "container_arithmetic",
            NOTE,
            f"net quantity {declared.render(1)} {label.medium_unit} is consistent with "
            f"{label.servings_per_container.render('servings')} servings of "
            f"{label.serving_interval().render(1)} {label.medium_unit}, which spans "
            f"{derived.render(1)} {label.medium_unit}.",
        )]
    return [Finding(
        "container_arithmetic",
        FLAG,
        f"servings times serving size spans {derived.render(1)} {label.medium_unit}, "
        f"which does not overlap the declared net quantity of {declared.render(1)} "
        f"{label.medium_unit}.",
    )]


def check_declared_dv(label: Label) -> list[Finding]:
    """Declared percent daily values against the amounts on the same panel.

    Percent daily value is rounded to the nearest whole percent, so the check asks
    whether any true amount inside the nutrient's own interval would round to the
    printed percentage. Skipped silently for nutrients the label does not declare a
    percentage for, since most panels print only some of them.
    """
    if not label.declared_dv_percent:
        return []
    findings: list[Finding] = []
    references = daily_values_for(label.label_format)
    basis = "per_serving" if label.basis == "per_serving" else "per_100g"
    normalized = normalize(label, basis)
    for key, printed in sorted(label.declared_dv_percent.items()):
        value = normalized.values.get(key)
        if value is None:
            findings.append(Finding(
                "declared_dv_orphan",
                FLAG,
                f"a percent daily value is declared for {key} but the panel has no "
                f"{key} amount to check it against.",
            ))
            continue
        band = percent_daily_value(label, value)
        if band is None:
            reference = references.get(key)
            reason = "no reference amount" if reference is None else "unit mismatch"
            findings.append(Finding(
                "declared_dv_unknown",
                NOTE,
                f"cannot check the {float(printed):g} percent daily value for {key}: {reason}.",
            ))
            continue
        lowest = round_half_up(band.lo, Fraction(1))
        highest = round_half_up(band.hi, Fraction(1))
        expected = (
            f"{float(lowest):g}" if lowest == highest
            else f"between {float(lowest):g} and {float(highest):g}"
        )
        if not (lowest <= printed <= highest):
            findings.append(Finding(
                "declared_dv_mismatch",
                FLAG,
                f"{key} is printed as {float(printed):g} percent of the daily value, but "
                f"{value.render(2)} against a reference of {float(references[key][0]):g} "
                f"{references[key][1]} gives {band.render(1)} percent, which rounds to "
                f"{expected} percent.",
            ))
        else:
            findings.append(Finding(
                "declared_dv_ok",
                NOTE,
                f"{key}: printed {float(printed):g} percent is within the "
                f"{band.render(1)} percent the amount implies.",
            ))
    return findings


def check_salt_sodium(label: Label) -> list[Finding]:
    """A label declaring both salt and sodium must have them 2.5 apart."""
    salt = _interval(label, "salt")
    sodium = _interval(label, "sodium")
    if salt is None or sodium is None:
        return []
    sodium_g = sodium.scale(Fraction(1, 1000)) if label.nutrients["sodium"].unit == "mg" else sodium
    implied = sodium_g.scale(SALT_PER_SODIUM)
    if implied.overlaps(salt):
        return [Finding(
            "salt_sodium",
            NOTE,
            f"salt {salt.render(3)} g is consistent with sodium at 2.5x, which implies "
            f"{implied.render(3)} g.",
        )]
    return [Finding(
        "salt_sodium",
        FLAG,
        f"salt is declared as {salt.render(3)} g but the declared sodium implies "
        f"{implied.render(3)} g at the Annex I factor of 2.5.",
    )]


def check_format_era(label: Label) -> list[Finding]:
    """Notice a panel that predates the 2016 US rule.

    A pre-2016 panel has no added sugars row and no potassium, and it does have vitamin
    A and vitamin C. Comparing it against a current one on those rows produces a
    difference that is an artefact of the format change rather than of the food.
    """
    if label.label_format != "US_2016":
        return []
    keys = set(label.nutrients)
    old_markers = keys & {"vitamin_a", "vitamin_c"}
    new_markers = keys & {"added_sugars", "potassium", "vitamin_d"}
    if old_markers and not new_markers:
        return [Finding(
            "pre_2016_panel",
            NOTE,
            f"this panel declares {', '.join(sorted(old_markers))} and none of added "
            f"sugars, potassium or vitamin D, which is the shape of a panel printed "
            f"before the 2016 rule. Its daily values may be the old ones (sodium 2400 mg, "
            f"fibre 25 g) rather than the ones used here.",
        )]
    return []


def run_all(label: Label) -> list[Finding]:
    findings: list[Finding] = []

    energy_columns = ["kcal"]
    if label.label_format == "EU_1169" and "energy_kj" in label.nutrients:
        energy_columns.append("kj")
    for column in energy_columns:
        key = "energy_kcal" if column == "kcal" else "energy_kj"
        if key not in label.nutrients:
            findings.append(Finding(
                f"energy_{column}_missing",
                FLAG,
                f"no {key} row, so the macronutrients cannot be cross-checked against "
                f"an energy figure.",
            ))
            continue
        result = check_energy(label, column)
        findings.append(Finding(
            f"energy_{column}",
            FLAG if not result.consistent else NOTE,
            result.message,
        ))

    if label.label_format == "EU_1169":
        note = kj_kcal_ratio_note(label)
        if note.ratio is not None:
            findings.append(Finding("kj_kcal_ratio", NOTE, note.message))

    findings.extend(check_component_sums(label))
    findings.extend(check_mass_budget(label))
    findings.extend(check_container_arithmetic(label))
    findings.extend(check_declared_dv(label))
    findings.extend(check_salt_sodium(label))
    findings.extend(check_format_era(label))
    return findings


def flags(findings: Iterable[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.severity == FLAG]
