"""Check a label's energy figure against its own macronutrients.

This is the check that makes the tool worth having, because it needs nothing but the
label itself. Protein, carbohydrate and fat carry known energy per gram, so a declared
calorie figure is a prediction the label makes about its own other rows, and a label
that contradicts itself is either a transcription error or a real error on the package.

The tolerance is the interesting part. Picking a round number, say ten percent, would
be arbitrary in both directions: too tight for a 45 kcal serving where a single gram of
rounded carbohydrate moves the answer by nine percent, and too loose for a 900 kcal one
where a ten percent band hides sixty stray calories. So there is no tolerance constant
anywhere in this module. Both sides of the comparison are intervals derived from the
rounding rules:

* the predicted band comes from multiplying each macronutrient's rounding interval by
  its energy factor and adding them up, then taking the hull across every calculation
  method the regulation permits;
* the declared band is the rounding interval of the printed calorie figure.

If those two intervals are disjoint, no assignment of true values consistent with the
printed panel can produce the printed energy, and the label is inconsistent. If they
overlap, it is consistent, and saying anything stronger would be inventing precision
the label does not contain.

Two format-specific wrinkles:

US. 21 CFR 101.9(c)(1)(i) permits more than one method of calculating calories, and
they disagree. General Atwater factors applied to total carbohydrate give one answer;
applied to total carbohydrate less insoluble fibre they give a lower one. Specific
Atwater factors and bomb calorimetry give others again. The band below spans the two
general-factor variants, which brackets most of the spread, and it widens further when
sugar alcohols are declared because those run from 0 kcal per gram for erythritol to
about 3 for maltitol.

EU. Annex XIV of Regulation (EU) No 1169/2011 gives a single table, so the EU check is
tighter. It also gives kJ and kcal in separate columns whose ratios are not equal:
17/4 for carbohydrate is 4.25, 37/9 for fat is 4.111, 8/2 for fibre is 4.0. So the two
declared energy figures are checked against separate predictions rather than against
each other through 4.184, and a fourth check reports how far the label's own kJ:kcal
ratio sits from the physical constant, which for a fat-heavy product is legitimately
about 4.11 and not evidence of anything wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from .interval import Interval, hull_all
from .label import Label
from .rulesets import EU_ENERGY_FACTORS, KJ_PER_KCAL, US_ATWATER

ZERO = Interval.exact(0)


@dataclass(frozen=True)
class EnergyBand:
    method: str
    band: Interval
    detail: str


@dataclass(frozen=True)
class EnergyCheck:
    label_name: str
    unit: str
    declared: Interval
    predicted: Interval
    methods: tuple[EnergyBand, ...]
    consistent: bool
    shortfall: Optional[Fraction]
    message: str

    def to_json(self) -> dict:
        return {
            "label": self.label_name,
            "unit": self.unit,
            "declared_lo": float(self.declared.lo),
            "declared_hi": float(self.declared.hi),
            "predicted_lo": float(self.predicted.lo),
            "predicted_hi": float(self.predicted.hi),
            "consistent": self.consistent,
            "shortfall": None if self.shortfall is None else float(self.shortfall),
            "message": self.message,
            "methods": [
                {"method": m.method, "lo": float(m.band.lo), "hi": float(m.band.hi),
                 "detail": m.detail}
                for m in self.methods
            ],
        }


def _amount(label: Label, key: str) -> Interval:
    """The interval for a nutrient, or exactly zero when the row is absent.

    An absent row is not the same as a declared zero, and treating it as zero is only
    safe here because the macronutrients this module reads are mandatory rows on both
    panels. Anything optional, sugar alcohols and alcohol, is handled explicitly below
    so that its absence widens nothing.
    """
    nutrient = label.nutrients.get(key)
    return ZERO if nutrient is None else nutrient.interval


def _weighted(parts: list[tuple[Interval, Fraction]]) -> Interval:
    total = ZERO
    for interval, factor in parts:
        total = total + interval.scale(factor)
    return total


def us_energy_bands(label: Label) -> list[EnergyBand]:
    protein = _amount(label, "protein")
    carbohydrate = _amount(label, "carbohydrate")
    fat = _amount(label, "fat")
    fiber = _amount(label, "fiber")
    alcohol = _amount(label, "alcohol")
    polyol = _amount(label, "sugar_alcohol")

    base = [
        (protein, US_ATWATER["protein"]),
        (fat, US_ATWATER["fat"]),
        (alcohol, US_ATWATER["alcohol"]),
    ]

    # Method A: general factors on total carbohydrate, sugar alcohols at their highest
    # plausible energy. This is the upper end of the permitted spread.
    method_a = _weighted(base + [
        (carbohydrate, US_ATWATER["carbohydrate"]),
        (polyol, US_ATWATER["sugar_alcohol_high"]),
    ])

    # Method B: general factors on total carbohydrate less all declared fibre, sugar
    # alcohols at zero. The regulation permits subtracting insoluble fibre; the label
    # does not say how much of the fibre is insoluble, so subtracting all of it is the
    # lowest figure any permitted method could give.
    carbohydrate_less_fiber = Interval(
        max(Fraction(0), carbohydrate.lo - fiber.hi),
        max(Fraction(0), carbohydrate.hi - fiber.lo),
        hi_open=carbohydrate.hi_open or fiber.hi_open,
    )
    method_b = _weighted(base + [
        (carbohydrate_less_fiber, US_ATWATER["carbohydrate"]),
        (polyol, US_ATWATER["sugar_alcohol_low"]),
    ])

    bands = [
        EnergyBand(
            "general Atwater, total carbohydrate",
            method_a,
            "4/4/9 on protein, total carbohydrate and fat, sugar alcohols at 3 kcal/g",
        ),
        EnergyBand(
            "general Atwater, carbohydrate less fibre",
            method_b,
            "4/4/9 with all declared fibre subtracted from carbohydrate and sugar "
            "alcohols at 0 kcal/g, the lowest figure a permitted method can give",
        ),
    ]
    return bands


def eu_energy_bands(label: Label, column: str) -> list[EnergyBand]:
    """``column`` is 'kj' or 'kcal', selecting a column of Annex XIV."""
    index = 0 if column == "kj" else 1
    factor = {name: values[index] for name, values in EU_ENERGY_FACTORS.items()}

    carbohydrate = _amount(label, "carbohydrate")
    polyols = _amount(label, "polyols")
    fiber = _amount(label, "fiber")
    # Annex XIV bills carbohydrate "except polyols" at the carbohydrate rate, and
    # polyols separately. A label that declares both has already counted the polyols
    # inside its carbohydrate figure, so they are subtracted before the rate applies.
    available = Interval(
        max(Fraction(0), carbohydrate.lo - polyols.hi),
        max(Fraction(0), carbohydrate.hi - polyols.lo),
        hi_open=carbohydrate.hi_open or polyols.hi_open,
    )
    band = _weighted([
        (available, factor["carbohydrate_available"]),
        (polyols, factor["polyols"]),
        (_amount(label, "protein"), factor["protein"]),
        (_amount(label, "fat"), factor["fat"]),
        (_amount(label, "alcohol"), factor["alcohol"]),
        (_amount(label, "organic_acid"), factor["organic_acid"]),
        (fiber, factor["fiber"]),
    ])
    unit = "kJ" if column == "kj" else "kcal"
    return [EnergyBand(
        f"Annex XIV {unit} column",
        band,
        f"Regulation (EU) No 1169/2011 Annex XIV factors, {unit} per gram",
    )]


def check_energy(label: Label, column: str = "kcal") -> EnergyCheck:
    """Compare a declared energy figure against its own macronutrient rows.

    ``column`` selects kcal or kJ and only matters for EU labels, which declare both.
    """
    key = "energy_kcal" if column == "kcal" else "energy_kj"
    unit = "kcal" if column == "kcal" else "kJ"
    declared_nutrient = label.nutrients.get(key)
    if declared_nutrient is None:
        raise ValueError(
            f"{label.name}: no {key} row, so there is nothing to check the "
            f"macronutrients against."
        )
    declared = declared_nutrient.interval

    if label.label_format == "US_2016":
        if column != "kcal":
            raise ValueError("US labels declare energy in kcal only")
        bands = us_energy_bands(label)
    else:
        bands = eu_energy_bands(label, "kcal" if column == "kcal" else "kj")

    predicted = hull_all(band.band for band in bands)
    consistent = predicted.overlaps(declared)

    if consistent:
        shortfall: Optional[Fraction] = None
        message = (
            f"consistent: the declared {declared.render(1)} {unit} overlaps the "
            f"{predicted.render(1)} {unit} its own macronutrients imply."
        )
    else:
        if declared.hi < predicted.lo:
            shortfall = predicted.lo - declared.hi
            direction = "lower than"
        else:
            shortfall = declared.lo - predicted.hi
            direction = "higher than"
        message = (
            f"INCONSISTENT: the declared {declared.render(1)} {unit} is {direction} the "
            f"{predicted.render(1)} {unit} its own macronutrients imply, by at least "
            f"{float(abs(shortfall)):.1f} {unit}. The gap is measured between the "
            f"nearest edges of the two intervals, so it already allows for every "
            f"rounding the panel is permitted."
        )

    return EnergyCheck(
        label_name=label.name,
        unit=unit,
        declared=declared,
        predicted=predicted,
        methods=tuple(bands),
        consistent=consistent,
        shortfall=shortfall,
        message=message,
    )


@dataclass(frozen=True)
class RatioNote:
    ratio: Optional[Interval]
    message: str


def kj_kcal_ratio_note(label: Label) -> RatioNote:
    """Report the label's own kJ:kcal ratio against the physical constant.

    Not a check. An EU label computes its two energy figures from two columns whose
    ratios run from 4.0 for fibre to 4.25 for carbohydrate, so a legitimate label sits
    anywhere in that range and a fat-heavy one sits near 4.11. Flagging a departure
    from 4.184 would produce a false positive on every olive oil in Europe.
    """
    kj = label.nutrients.get("energy_kj")
    kcal = label.nutrients.get("energy_kcal")
    if kj is None or kcal is None:
        return RatioNote(None, "the label declares only one energy unit, no ratio to report")
    if kcal.interval.lo <= 0:
        return RatioNote(None, "declared kcal reaches zero, so the ratio is unbounded")
    ratio = kj.interval / kcal.interval
    physical = float(KJ_PER_KCAL)
    if ratio.contains(KJ_PER_KCAL):
        verdict = f"which includes the thermochemical constant {physical}"
    else:
        verdict = (
            f"which excludes the thermochemical constant {physical}. That is normal: "
            f"Annex XIV ratios run from 4.0 for fibre to 4.25 for carbohydrate, so the "
            f"two declared figures are not related by a single factor"
        )
    return RatioNote(
        ratio,
        f"declared kJ:kcal ratio is {ratio.render(3)}, {verdict}.",
    )
