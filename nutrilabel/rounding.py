"""Declaration rounding rules, forward and inverse.

A nutrition label does not round to significant figures. It rounds by rule, and the
rule changes with the magnitude of the value and with the nutrient. Under 21 CFR 101.9
a fat content below 0.5 g is printed as ``0``, calories are printed to the nearest 5
up to 50 and the nearest 10 above that, and sodium switches from 5 mg steps to 10 mg
steps at 140 mg. So "0 g trans fat" is a statement that the true value is somewhere in
[0, 0.5), and there is no way to recover a point value from it.

This module models a rule as an ordered list of half-open segments over the true value.
Each segment lists the declaration forms that are permitted for a true value in it.
Two directions matter:

``permitted(x)``  the forms a manufacturer may legally print for a true value ``x``.
``inverse(d)``    the set of true values that could have produced the printed form
                  ``d``, which is the interval the rest of the tool carries around.

``inverse`` is derived from ``permitted`` rather than written out by hand, and
``tests/test_rounding.py`` checks the two against each other by sweeping a dense grid
of true values. That is the property that keeps the inverse honest as rules are edited:
if a segment boundary is wrong in one direction it is wrong in both, and the sweep sees
a value whose printed form falls outside the interval the inverse claims.

Segment boundaries are half-open on the right throughout. Where a regulation says "up
to and including 50 calories", the two adjacent rules agree at exactly 50 anyway, so
nothing turns on which side of the boundary the endpoint sits.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Optional, Sequence, Union

from .interval import Interval, frac


@dataclass(frozen=True)
class Nearest:
    """Print the value rounded to the nearest multiple of ``step``.

    Ties go up. No regulation states a tie-breaking direction, and half-up is what
    every rounding table in the guidance documents shows, so the inverse of a declared
    ``d`` under this form is the half-open interval [d - step/2, d + step/2).
    """

    step: Fraction

    def __post_init__(self) -> None:
        if self.step <= 0:
            raise ValueError("rounding step must be positive")


@dataclass(frozen=True)
class Zero:
    """Print a literal ``0``, regardless of the true value in this segment."""


@dataclass(frozen=True)
class LessThan:
    """Print "less than X", for example the cholesterol form "less than 5 mg"."""

    bound: Fraction


Form = Union[Nearest, Zero, LessThan]


@dataclass(frozen=True)
class Declaration:
    """What is actually printed on the panel.

    ``kind`` is ``"value"`` for a number and ``"less_than"`` for a "less than X" form.
    Keeping these distinct matters: "less than 1 gram" and "1 gram" are different
    printed forms with different inverse intervals, and collapsing them to the float
    1.0 throws away the tighter of the two.
    """

    kind: str
    amount: Fraction

    @staticmethod
    def parse(raw: Union[str, int, float, Fraction]) -> "Declaration":
        if isinstance(raw, str):
            text = raw.strip().replace("≤", "<=")
            for prefix in ("less than", "<=", "<"):
                if text.lower().startswith(prefix):
                    rest = text[len(prefix):].strip()
                    return Declaration("less_than", frac(rest))
            return Declaration("value", frac(text))
        return Declaration("value", frac(raw))

    def render(self, unit: str = "") -> str:
        from .interval import format_fraction

        number = format_fraction(self.amount, 3)
        body = f"<{number}" if self.kind == "less_than" else number
        return f"{body} {unit}".strip()


@dataclass(frozen=True)
class Segment:
    lo: Fraction
    hi: Optional[Fraction]  # exclusive; None means unbounded above
    forms: tuple[Form, ...]

    def contains(self, x: Fraction) -> bool:
        return x >= self.lo and (self.hi is None or x < self.hi)


@dataclass(frozen=True)
class RoundingRule:
    """One nutrient's declaration rule under one label format."""

    name: str
    unit: str
    segments: tuple[Segment, ...]
    citation: str
    # "cited" means the rule is written down in the source named by ``citation``.
    # "inferred" means we reconstructed it from the printed precision because we could
    # not point at a paragraph. The distinction is surfaced in the output rather than
    # hidden, because an interval that rests on a guess should not look like one that
    # rests on a regulation.
    basis: str = "cited"
    notes: str = ""

    def __post_init__(self) -> None:
        previous_hi: Optional[Fraction] = None
        for index, segment in enumerate(self.segments):
            if index == 0:
                if segment.lo != 0:
                    raise ValueError(f"{self.name}: first segment must start at 0")
            elif segment.lo != previous_hi:
                raise ValueError(
                    f"{self.name}: segment {index} starts at {segment.lo} but the "
                    f"previous one ended at {previous_hi}; segments must tile [0, inf)"
                )
            if segment.hi is not None and segment.hi <= segment.lo:
                raise ValueError(f"{self.name}: segment {index} is empty or reversed")
            previous_hi = segment.hi
        if previous_hi is not None:
            raise ValueError(f"{self.name}: the last segment must be unbounded above")

    def segment_for(self, x: Fraction) -> Segment:
        if x < 0:
            raise ValueError(f"{self.name}: a negative amount ({x}) cannot be declared")
        for segment in self.segments:
            if segment.contains(x):
                return segment
        raise AssertionError(f"{self.name}: segments do not cover {x}")

    def permitted(self, x: Union[int, float, str, Fraction]) -> set[Declaration]:
        """Every declaration form a true value of ``x`` may legally carry."""
        value = frac(x)
        out: set[Declaration] = set()
        for form in self.segment_for(value).forms:
            if isinstance(form, Zero):
                out.add(Declaration("value", Fraction(0)))
            elif isinstance(form, LessThan):
                out.add(Declaration("less_than", form.bound))
            else:
                out.add(Declaration("value", round_half_up(value, form.step)))
        return out

    def declare(self, x: Union[int, float, str, Fraction]) -> Declaration:
        """The numeric form a manufacturer would normally print.

        Where a segment permits several numeric forms, for example calories below 5
        which may be printed as 0 or rounded to the nearest 5, this returns the one
        closest to the true value, ties going to the smaller. Compliance checking uses
        ``permitted`` instead, which returns all of them.
        """
        value = frac(x)
        numeric = [d for d in self.permitted(value) if d.kind == "value"]
        if numeric:
            return min(numeric, key=lambda d: (abs(d.amount - value), d.amount))
        return min(self.permitted(value), key=lambda d: d.amount)

    def inverse(self, declared: Union[str, int, float, Fraction, Declaration]) -> Interval:
        """The interval of true values consistent with a printed declaration.

        Built by asking each segment which of its true values would print ``declared``,
        then merging. If the merged pieces are not contiguous the rule table is wrong,
        and this raises rather than silently returning the hull, because a gap means a
        true value that prints as ``declared`` but sits outside the returned interval,
        and every downstream number would then be quietly too narrow.
        """
        target = declared if isinstance(declared, Declaration) else Declaration.parse(declared)
        pieces: list[Interval] = []
        for segment in self.segments:
            hi = segment.hi
            for form in segment.forms:
                if isinstance(form, Zero):
                    if target.kind == "value" and target.amount == 0:
                        pieces.append(_clip(segment.lo, hi, segment.lo, hi))
                elif isinstance(form, LessThan):
                    if target.kind == "less_than" and target.amount == form.bound:
                        pieces.append(_clip(segment.lo, hi, segment.lo, hi))
                else:
                    if target.kind != "value":
                        continue
                    # A declaration that is not a multiple of the step is not something
                    # this segment can produce. Without this guard the arithmetic below
                    # happily returns [D - step/2, D + step/2) for, say, 1 kcal under a
                    # 5 kcal step, an interval no true value in it would ever print as.
                    # That made the inverse too wide and stopped an unprintable figure
                    # from raising at all. Found by the forward-inverse sweep.
                    if (target.amount / form.step).denominator != 1:
                        continue
                    half = form.step / 2
                    lo = max(target.amount - half, Fraction(0))
                    piece = _clip(lo, target.amount + half, segment.lo, hi)
                    if piece is not None:
                        pieces.append(piece)
        pieces = [p for p in pieces if p is not None and p.width > 0]
        if not pieces:
            raise ValueError(
                f"{self.name}: no true value would be printed as "
                f"{target.render(self.unit)!r} under {self.citation}. Either the label "
                f"does not follow this format or the figure was transcribed wrong."
            )
        pieces.sort(key=lambda p: (p.lo, p.hi))
        merged = pieces[0]
        for piece in pieces[1:]:
            if piece.lo > merged.hi:
                raise ValueError(
                    f"{self.name}: the inverse of {target.render(self.unit)!r} is not "
                    f"contiguous ({merged!r} then {piece!r}); the segment table is wrong"
                )
            merged = Interval(merged.lo, max(merged.hi, piece.hi), hi_open=True)
        return merged


def _clip(lo: Fraction, hi: Optional[Fraction], seg_lo: Fraction, seg_hi: Optional[Fraction]) -> Optional[Interval]:
    low = max(lo, seg_lo)
    if hi is None and seg_hi is None:
        raise ValueError("cannot clip two unbounded ranges into a finite interval")
    candidates = [h for h in (hi, seg_hi) if h is not None]
    high = min(candidates)
    if high <= low:
        return None
    return Interval(low, high, hi_open=True)


def round_half_up(value: Fraction, step: Fraction) -> Fraction:
    """Round ``value`` to the nearest multiple of ``step``, ties away from zero.

    Exact, because both arguments are Fractions. ``round(2.5)`` in Python is 2, which
    would make the declared value of a genuine 2.5 g disagree with every rounding table
    in the guidance documents.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    quotient = value / step
    whole = quotient.numerator // quotient.denominator
    if quotient - whole >= Fraction(1, 2):
        whole += 1
    return Fraction(whole) * step


def significant_figure_rule(
    name: str,
    unit: str,
    declared: Fraction,
    note: str,
    citation: str = "no rounding rule cited; step inferred from the printed precision",
) -> RoundingRule:
    """Fallback rule for a nutrient whose rounding step we cannot cite.

    Reads the precision off the printed figure: ``235`` and ``240`` are both treated as
    rounded to the nearest 1, ``0.9`` to the nearest 0.1. A trailing zero is therefore
    read as precision the label may not actually have, which errs toward a narrower
    interval than the truth. That is the wrong direction for a checker, so any rule
    built this way is marked ``inferred`` and the output says so next to every figure
    derived from it.
    """
    step = Fraction(1, 10 ** max(0, -_decimal_exponent(declared)))
    return RoundingRule(
        name=name,
        unit=unit,
        segments=(Segment(Fraction(0), None, (Nearest(step),)),),
        citation=citation,
        basis="inferred",
        notes=note,
    )


def refine_for_declaration(rule: RoundingRule, declaration: Declaration) -> RoundingRule:
    """Fall back to the printed precision when the panel is finer than the rule.

    A rule table says what the coarsest permitted step is. A label that prints 91.6 g of
    fat has used a finer one, whatever the table says, and reading it through a 1 g step
    would claim the true value lies anywhere in [91.1, 92.1) when the printed figure
    itself rules that out. This matters most for the EU rules, which come from a
    non-binding guidance document that real labels routinely beat.

    The refinement fires only when the printed value is not a multiple of any step the
    segment permits. A US panel, whose printed figures always sit on the regulated step,
    passes through untouched, and the returned rule keeps its citation.
    """
    if declaration.kind != "value":
        return rule
    value = declaration.amount
    try:
        segment = rule.segment_for(value)
    except (ValueError, AssertionError):
        return rule
    steps = [form.step for form in segment.forms if isinstance(form, Nearest)]
    if any((value / step).denominator == 1 for step in steps):
        return rule
    if not steps and value == 0 and any(isinstance(form, Zero) for form in segment.forms):
        return rule
    return significant_figure_rule(
        rule.name,
        rule.unit,
        value,
        note=(
            f"the panel prints {float(value):g} {rule.unit}, which is finer than the "
            f"step the rule table gives for this range, so the interval was taken from "
            f"the printed precision instead"
        ),
        citation=f"{rule.citation}, refined to the printed precision",
    )


def _decimal_exponent(value: Fraction) -> int:
    """Smallest e <= 0 such that value is a multiple of 10**e, floored at -6."""
    for exponent in range(0, -7, -1):
        scale = Fraction(10) ** exponent
        if (value / scale).denominator == 1:
            return exponent
    return -6


def make_rule(
    name: str,
    unit: str,
    citation: str,
    boundaries: Sequence[tuple[Optional[str], tuple[Form, ...]]],
    notes: str = "",
    basis: str = "cited",
) -> RoundingRule:
    """Build a rule from (upper bound, forms) pairs, lowest segment first.

    ``boundaries=[("0.5", (Zero(),)), (None, (Nearest("1"),))]`` reads as "below 0.5,
    print zero; at or above 0.5, print to the nearest gram".
    """
    segments: list[Segment] = []
    lo = Fraction(0)
    for upper, forms in boundaries:
        hi = None if upper is None else frac(upper)
        segments.append(Segment(lo, hi, tuple(forms)))
        if hi is not None:
            lo = hi
    return RoundingRule(name, unit, tuple(segments), citation, basis, notes)


def nearest(step: str) -> Nearest:
    return Nearest(frac(step))


def less_than(bound: str) -> LessThan:
    return LessThan(frac(bound))


__all__ = [
    "Declaration",
    "Interval",
    "LessThan",
    "Nearest",
    "RoundingRule",
    "Segment",
    "Zero",
    "less_than",
    "make_rule",
    "nearest",
    "refine_for_declaration",
    "round_half_up",
    "significant_figure_rule",
]
