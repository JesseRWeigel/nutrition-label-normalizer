"""Unit handling.

Small, but it is where two of the four cross-region traps live: sodium against salt,
and kilojoules against kilocalories. Both are one multiplication, and both are wrong
often enough to be worth a named function with a citation attached.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Union

from .interval import Interval, frac
from .rulesets import KJ_PER_KCAL, SALT_PER_SODIUM

Number = Union[int, float, str, Fraction]

# Multipliers into the canonical unit of each dimension: grams for mass, millilitres
# for volume, kilocalories for energy.
MASS = {
    "kg": Fraction(1000),
    "g": Fraction(1),
    "mg": Fraction(1, 1000),
    "mcg": Fraction(1, 1000000),
    "ug": Fraction(1, 1000000),
    "µg": Fraction(1, 1000000),
    "μg": Fraction(1, 1000000),
}

VOLUME = {
    "l": Fraction(1000),
    "ml": Fraction(1),
    "cl": Fraction(10),
    "dl": Fraction(100),
}

ENERGY = {
    "kcal": Fraction(1),
    "cal": Fraction(1),  # a food "Calorie" with a capital C is a kilocalorie
    "kj": Fraction(1) / KJ_PER_KCAL,
}


def normalise_unit(unit: str) -> str:
    text = unit.strip().lower()
    if text in ("calories", "calorie"):
        return "kcal"
    if text in ("mcg", "ug", "µg", "μg", "microgram", "micrograms"):
        return "mcg"
    if text in ("gram", "grams", "gm"):
        return "g"
    if text in ("milligram", "milligrams"):
        return "mg"
    return text


def dimension_of(unit: str) -> str:
    text = normalise_unit(unit)
    if text in MASS:
        return "mass"
    if text in VOLUME:
        return "volume"
    if text in ENERGY:
        return "energy"
    raise ValueError(f"unknown unit {unit!r}")


def convert(value: Number, from_unit: str, to_unit: str) -> Fraction:
    """Convert a scalar between units of the same dimension.

    Energy conversion here uses the thermochemical calorie, 4.184 kJ per kcal. That is
    the right factor for a physical quantity of energy. It is the wrong factor for
    turning an EU label's declared kJ figure into its declared kcal figure, because
    those two figures are computed independently from the two columns of Annex XIV and
    are not related by any single constant. Use ``nutrilabel.energy`` for that.
    """
    source, target = normalise_unit(from_unit), normalise_unit(to_unit)
    if source == target:
        return frac(value)
    dimension = dimension_of(source)
    if dimension != dimension_of(target):
        raise ValueError(
            f"cannot convert {from_unit} to {to_unit}: {dimension} is not "
            f"{dimension_of(target)}"
        )
    table = {"mass": MASS, "volume": VOLUME, "energy": ENERGY}[dimension]
    return frac(value) * table[source] / table[target]


def convert_interval(interval: Interval, from_unit: str, to_unit: str) -> Interval:
    factor = convert(1, from_unit, to_unit)
    return interval.scale(factor)


def sodium_to_salt(sodium_g: Number) -> Fraction:
    """Salt equivalent from sodium. Annex I point 11 of Regulation (EU) No 1169/2011.

    The factor is 2.5 exactly, from the molar mass ratio of sodium chloride to sodium.
    An EU label declares salt and never sodium; a US label declares sodium and never
    salt. Comparing the two without this conversion overstates the EU product by 2.5x.
    """
    return frac(sodium_g) * SALT_PER_SODIUM


def salt_to_sodium(salt_g: Number) -> Fraction:
    return frac(salt_g) / SALT_PER_SODIUM


def sodium_interval_to_salt(interval: Interval) -> Interval:
    return interval.scale(SALT_PER_SODIUM)


def salt_interval_to_sodium(interval: Interval) -> Interval:
    return interval.scale(Fraction(1) / SALT_PER_SODIUM)


def kcal_to_kj(kcal: Number) -> Fraction:
    """Physical conversion only. See the note in ``convert``."""
    return frac(kcal) * KJ_PER_KCAL


def kj_to_kcal(kj: Number) -> Fraction:
    """Physical conversion only. See the note in ``convert``."""
    return frac(kj) / KJ_PER_KCAL
