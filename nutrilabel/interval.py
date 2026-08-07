"""Exact intervals over the rationals.

Every number that comes off a nutrition label is a rounded number, so the honest
representation of "8 g of fat" is not 8 but the set of true values that a
manufacturer would have been required to print as 8. That set is an interval, and
once you multiply it by a serving size that is itself an interval, the width is the
whole point of the exercise.

Endpoints are ``fractions.Fraction`` so that repeated scaling never drifts. Floating
point would put 0.30000000000000004 on a boundary and make a rounding inverse
occasionally return the wrong bucket.

Interval bounds are stored as ``lo`` (always attained) and ``hi``. ``hi_open`` records
whether the upper endpoint is attainable, which for a rounding inverse it is not: a
declared 230 kcal means the true value is at least 225 and strictly below 235, because
235 itself would have been printed as 240. That flag is carried for display and for
containment tests. Overlap tests deliberately treat both intervals as closed, which is
the conservative direction for a checker: it will not raise a flag on a label whose
consistency turns on a single boundary point.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Union

Number = Union[int, float, str, Fraction]


def frac(value: Number) -> Fraction:
    """Convert to an exact Fraction, going through str for floats.

    ``Fraction(0.1)`` is 3602879701896397/36028797018963968, which is correct and
    useless here. ``Fraction("0.1")`` is 1/10, which is what the label meant.
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, float):
        return Fraction(str(value))
    return Fraction(value)


@dataclass(frozen=True)
class Interval:
    lo: Fraction
    hi: Fraction
    hi_open: bool = True

    def __post_init__(self) -> None:
        if self.lo > self.hi:
            raise ValueError(f"interval lower bound {self.lo} exceeds upper bound {self.hi}")

    @staticmethod
    def exact(value: Number) -> "Interval":
        """A value known without rounding, for example a serving count of exactly 1."""
        v = frac(value)
        return Interval(v, v, hi_open=False)

    @staticmethod
    def of(lo: Number, hi: Number, hi_open: bool = True) -> "Interval":
        return Interval(frac(lo), frac(hi), hi_open)

    @property
    def width(self) -> Fraction:
        return self.hi - self.lo

    @property
    def midpoint(self) -> Fraction:
        return (self.lo + self.hi) / 2

    @property
    def is_point(self) -> bool:
        return self.lo == self.hi

    def contains(self, value: Number) -> bool:
        v = frac(value)
        if v < self.lo:
            return False
        if v > self.hi:
            return False
        return not (v == self.hi and self.hi_open and not self.is_point)

    def overlaps(self, other: "Interval") -> bool:
        """Closed-interval overlap. Conservative on purpose, see the module docstring."""
        return self.lo <= other.hi and other.lo <= self.hi

    def __add__(self, other: "Interval") -> "Interval":
        return Interval(self.lo + other.lo, self.hi + other.hi, self.hi_open or other.hi_open)

    def scale(self, factor: Number) -> "Interval":
        f = frac(factor)
        if f < 0:
            raise ValueError("negative scaling is not meaningful for a nutrient amount")
        return Interval(self.lo * f, self.hi * f, self.hi_open)

    def __mul__(self, other: "Interval") -> "Interval":
        if self.lo < 0 or other.lo < 0:
            raise ValueError("interval multiplication here assumes non-negative amounts")
        return Interval(self.lo * other.lo, self.hi * other.hi, self.hi_open or other.hi_open)

    def __truediv__(self, other: "Interval") -> "Interval":
        """Divide by a strictly positive interval.

        A divisor that can be zero would make the result unbounded, and a serving size
        of zero is a broken label rather than a very large per-gram figure, so this
        raises instead of returning infinity. Note the endpoint swap: dividing by the
        largest possible serving gives the smallest possible per-gram amount.
        """
        if other.lo <= 0:
            raise ValueError(
                "cannot divide by an interval that reaches zero; the divisor here is a "
                "serving size or an energy figure, and zero means the label is unusable"
            )
        return Interval(self.lo / other.hi, self.hi / other.lo, self.hi_open or other.hi_open)

    def hull(self, other: "Interval") -> "Interval":
        """Smallest interval containing both. Used to union the bands of several
        permitted calculation methods into one tolerance."""
        lo = min(self.lo, other.lo)
        if self.hi > other.hi:
            hi, hi_open = self.hi, self.hi_open
        elif other.hi > self.hi:
            hi, hi_open = other.hi, other.hi_open
        else:
            hi, hi_open = self.hi, self.hi_open and other.hi_open
        return Interval(lo, hi, hi_open)

    def as_floats(self) -> tuple[float, float]:
        return (float(self.lo), float(self.hi))

    def render(self, places: int = 2) -> str:
        lo = format_fraction(self.lo, places)
        hi = format_fraction(self.hi, places)
        if lo == hi:
            return lo
        return f"{lo}–{hi}"

    def __repr__(self) -> str:
        bracket = ")" if self.hi_open else "]"
        return f"[{float(self.lo):g}, {float(self.hi):g}{bracket}"


def hull_all(intervals: Iterable[Interval]) -> Interval:
    items = list(intervals)
    if not items:
        raise ValueError("hull of no intervals is undefined")
    out = items[0]
    for item in items[1:]:
        out = out.hull(item)
    return out


def format_fraction(value: Fraction, places: int = 2) -> str:
    """Round half up to ``places`` decimals and strip trailing zeros.

    Python's round() is banker's rounding, which would print 0.5 as 0 and make the
    fixture expectations look wrong for a reason that has nothing to do with nutrition.
    """
    if places < 0:
        raise ValueError("places must be non-negative")
    scale = Fraction(10) ** places
    scaled = value * scale
    whole = scaled.numerator // scaled.denominator
    if scaled - whole >= Fraction(1, 2):
        whole += 1
    sign = "-" if whole < 0 else ""
    whole = abs(whole)
    if places:
        units, decimals = divmod(whole, 10 ** places)
        text = f"{sign}{units}.{decimals:0{places}d}".rstrip("0").rstrip(".")
    else:
        text = f"{sign}{whole}"
    return text if text not in ("", "-") else "0"
