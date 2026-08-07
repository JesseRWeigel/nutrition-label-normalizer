"""Restate a label on a chosen basis.

This is the part that makes two labels comparable. A US panel states everything per
serving and an EU panel states everything per 100 g, so the two are not on speaking
terms until one of them moves. Moving one means dividing by a serving size that is
itself a rounded figure, so the result is an interval wider than the one you started
with, and the widening is real information rather than pedantry: it is exactly how much
of the difference between two products you are entitled to believe.

Four bases are supported.

per_serving   what the US panel prints. Useful only against another label with the
              same serving size, which is the trap the whole tool exists to point at.
per_100g      what the EU panel prints, and the only basis on which two products of
              different serving size can be compared directly. Reads per_100ml for a
              liquid.
per_100kcal   comparison by energy density rather than by mass. Divides by the energy
              interval, so it is the widest of the four.
per_container what you get if you eat the package, which is what the "servings per
              container: 2.5" convention exists to obscure.

Every returned figure carries the basis it is on, the interval, and whether the rule
behind that interval was cited or inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from .interval import Interval, format_fraction
from .label import Label, LabelError
from .rulesets import daily_values_for

BASIS_CHOICES = ("per_serving", "per_100g", "per_100kcal", "per_container")


@dataclass(frozen=True)
class NormalizedValue:
    key: str
    unit: str
    interval: Interval
    basis: str
    rule_basis: str
    citation: str
    derivation: str

    @property
    def point(self) -> Fraction:
        """The midpoint. Offered for sorting and charting only.

        Never present this as the value. The whole point of the interval is that the
        label does not contain a point value, and a midpoint is a fiction with a
        plausible number of decimal places on it.
        """
        return self.interval.midpoint

    def render(self, places: int = 2) -> str:
        return f"{self.interval.render(places)} {self.unit}".strip()

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "unit": self.unit,
            "lo": float(self.interval.lo),
            "hi": float(self.interval.hi),
            "lo_exact": str(self.interval.lo),
            "hi_exact": str(self.interval.hi),
            "hi_open": self.interval.hi_open,
            "basis": self.basis,
            "rule_basis": self.rule_basis,
            "citation": self.citation,
            "derivation": self.derivation,
            "display": self.render(),
        }


@dataclass(frozen=True)
class NormalizedLabel:
    label_name: str
    label_format: str
    basis: str
    basis_label: str
    values: dict[str, NormalizedValue]
    serving_note: str
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {
            "name": self.label_name,
            "format": self.label_format,
            "basis": self.basis,
            "basis_label": self.basis_label,
            "serving_note": self.serving_note,
            "warnings": list(self.warnings),
            "values": {key: value.to_json() for key, value in self.values.items()},
        }


def basis_label(label: Label, basis: str) -> str:
    """The human-readable name of the basis in force, for display next to every figure.

    The tool must never show a number without saying what it is per. A column of
    figures with no basis stated is how the comparison goes wrong in the first place.
    """
    hundred = "100 g" if label.medium_unit == "g" else "100 ml"
    if basis == "per_100g":
        return f"per {hundred}"
    if basis == "per_100kcal":
        return "per 100 kcal"
    if basis == "per_container":
        return "per container"
    if label.serving is None:
        return "per serving"
    amount = format_fraction(label.serving.amount, 2)
    household = f", {label.serving_household}" if label.serving_household else ""
    return f"per {amount} {label.serving.unit} serving{household}"


def _per_hundred_factor(label: Label) -> tuple[Interval, str]:
    """Multiplier taking a per-serving figure to a per-100 figure, as an interval."""
    serving = label.serving_interval()
    factor = Interval.exact(100) / serving
    return factor, (
        f"per serving divided by a serving of {serving.render(2)} {label.medium_unit} "
        f"and multiplied by 100"
    )


def normalize(label: Label, basis: str) -> NormalizedLabel:
    if basis not in BASIS_CHOICES:
        raise ValueError(f"basis must be one of {BASIS_CHOICES}, got {basis!r}")

    warnings: list[str] = []
    declared_per_hundred = label.basis in ("per_100g", "per_100ml")

    # Step one: get everything onto the label's declared basis as intervals, then find
    # the single multiplier that carries that basis to the requested one. Energy is the
    # exception, because per_100kcal divides by a figure that is itself on the moving
    # basis; that ratio is basis-independent so it is handled separately.
    if basis == "per_100kcal":
        energy = label.nutrients.get("energy_kcal")
        if energy is None:
            energy_kj = label.nutrients.get("energy_kj")
            if energy_kj is None:
                raise LabelError(
                    f"{label.name}: per_100kcal needs an energy figure and the label "
                    f"declares neither energy_kcal nor energy_kj."
                )
            raise LabelError(
                f"{label.name}: this label declares energy only in kJ. Converting kJ to "
                f"kcal with 4.184 would not reproduce the kcal figure the manufacturer "
                f"would have printed, because the EU computes the two columns from "
                f"different factor tables. Add the declared kcal figure."
            )
        divisor = energy.interval
        if divisor.lo <= 0:
            raise LabelError(
                f"{label.name}: energy is declared as {energy.declaration.render('kcal')}, "
                f"whose lower bound is zero, so per-100-kcal figures would be unbounded."
            )
        factor = Interval.exact(100) / divisor
        derivation_suffix = (
            f"divided by an energy of {divisor.render(1)} kcal on the same basis, "
            f"multiplied by 100"
        )
        serving_note = f"energy on the declared basis: {divisor.render(1)} kcal"
    elif basis == "per_container":
        if declared_per_hundred:
            container, how = label.container_interval()
            factor = container / Interval.exact(100)
            derivation_suffix = (
                f"per 100 {label.medium_unit} scaled to a container of "
                f"{container.render(1)} {label.medium_unit} ({how})"
            )
            serving_note = f"container {container.render(1)} {label.medium_unit}, from {how}"
        else:
            servings = label.servings_interval()
            factor = servings
            derivation_suffix = (
                f"per serving multiplied by {servings.render(2)} servings per container"
            )
            serving_note = f"{servings.render(2)} servings per container"
            if servings.width > 0:
                warnings.append(
                    f"servings per container is itself rounded to {servings.render(2)}, "
                    f"so every per-container figure below carries that width on top of "
                    f"the per-serving rounding. Multiplying a rounded per-serving figure "
                    f"by a rounded serving count compounds two roundings."
                )
    elif basis == "per_100g":
        if declared_per_hundred:
            factor = Interval.exact(1)
            derivation_suffix = "as declared"
            serving_note = "declared per 100, no conversion applied"
        else:
            factor, derivation_suffix = _per_hundred_factor(label)
            serving_note = (
                f"serving {label.serving_interval().render(2)} {label.medium_unit} "
                f"after rounding"
            )
    else:  # per_serving
        if declared_per_hundred:
            serving = label.serving_interval()
            factor = serving / Interval.exact(100)
            derivation_suffix = (
                f"per 100 {label.medium_unit} scaled to a serving of "
                f"{serving.render(2)} {label.medium_unit}"
            )
            serving_note = f"serving {serving.render(2)} {label.medium_unit} after rounding"
        else:
            factor = Interval.exact(1)
            derivation_suffix = "as declared"
            serving_note = "declared per serving, no conversion applied"

    values: dict[str, NormalizedValue] = {}
    for key, nutrient in label.nutrients.items():
        if basis == "per_100kcal" and key in ("energy_kcal", "energy_kj"):
            # 100 kcal per 100 kcal is not information. Skipping it is clearer than
            # printing a tautology, and the energy figure is reported in serving_note.
            continue
        scaled = nutrient.interval * factor
        values[key] = NormalizedValue(
            key=key,
            unit=nutrient.unit,
            interval=scaled,
            basis=basis,
            rule_basis=nutrient.rule.basis,
            citation=nutrient.rule.citation,
            derivation=(
                f"declared {nutrient.declaration.render(nutrient.unit)} "
                f"({nutrient.rule.citation}) gives {nutrient.interval.render(3)} "
                f"{nutrient.unit}, then {derivation_suffix}"
                if factor != Interval.exact(1)
                else f"declared {nutrient.declaration.render(nutrient.unit)} "
                     f"({nutrient.rule.citation}) gives {nutrient.interval.render(3)} "
                     f"{nutrient.unit}"
            ),
        )

    if not declared_per_hundred and basis in ("per_100g", "per_100kcal", "per_container"):
        if label.serving is not None and basis == "per_100g":
            widen = label.serving_interval()
            spread = float(widen.width / widen.lo) * 100
            if spread > 0:
                warnings.append(
                    f"the serving size itself is rounded, so every figure on this basis "
                    f"is at least {spread:.1f} percent wide before the nutrient rounding "
                    f"is counted."
                )

    return NormalizedLabel(
        label_name=label.name,
        label_format=label.label_format,
        basis=basis,
        basis_label=basis_label(label, basis),
        values=values,
        serving_note=serving_note,
        warnings=tuple(warnings),
    )


def percent_daily_value(label: Label, value: NormalizedValue) -> Optional[Interval]:
    """Recompute the percent daily value from a normalized amount.

    Only meaningful on the per-serving and per-container bases; a percent daily value
    per 100 kcal is not a thing any regulation defines. Returns None where the nutrient
    has no reference amount in the label's format.
    """
    references = daily_values_for(label.label_format)
    reference = references.get(value.key)
    if reference is None:
        return None
    amount, unit = reference
    if unit.split()[0] != value.unit:
        return None
    return value.interval.scale(Fraction(100) / amount)
