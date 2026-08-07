"""The label model: what was printed, and on what basis.

A label is a set of printed declarations, a basis those declarations are stated on,
a serving size, and a format. Nothing here interprets the numbers. Interpretation
happens in ``normalize`` and ``energy``, and both of them work from the intervals this
module produces rather than from the printed figures, so no code path downstream ever
sees a rounded number as if it were exact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Optional

from .interval import Interval, frac
from .rounding import (
    Declaration,
    RoundingRule,
    refine_for_declaration,
    significant_figure_rule,
)
from .rulesets import (
    US_SERVINGS_PER_CONTAINER_RULE,
    daily_values_for,
    rules_for,
    serving_size_rule_for,
)
from .units import convert, dimension_of, normalise_unit

FORMATS = ("US_2016", "EU_1169")
BASES = ("per_serving", "per_100g", "per_100ml")

PROVENANCE_KINDS = {
    # A published example label. The FDA's own sample panel is the only one of these.
    "published-example",
    # Values representative of a product category, not transcribed from one package.
    "typical",
    # Built to exercise a specific rule or boundary.
    "constructed",
}


class LabelError(ValueError):
    """Raised when a label cannot be read. Never swallowed into a default."""


@dataclass(frozen=True)
class Quantity:
    amount: Fraction
    unit: str

    @staticmethod
    def parse(raw: Any, what: str) -> "Quantity":
        if isinstance(raw, Mapping):
            if "amount" not in raw or "unit" not in raw:
                raise LabelError(f"{what}: needs both 'amount' and 'unit'")
            return Quantity(frac(raw["amount"]), normalise_unit(str(raw["unit"])))
        raise LabelError(f"{what}: expected an object with 'amount' and 'unit'")


@dataclass(frozen=True)
class DeclaredNutrient:
    key: str
    declaration: Declaration
    unit: str
    rule: RoundingRule

    @property
    def interval(self) -> Interval:
        """True values consistent with what was printed, in ``unit``."""
        return self.rule.inverse(self.declaration)

    @property
    def rule_basis(self) -> str:
        return self.rule.basis


@dataclass
class Label:
    name: str
    label_format: str
    basis: str
    serving: Optional[Quantity]
    serving_household: str
    servings_per_container: Optional[Declaration]
    net_quantity: Optional[Quantity]
    nutrients: dict[str, DeclaredNutrient]
    provenance: str
    source_note: str
    declared_dv_percent: dict[str, Fraction] = field(default_factory=dict)

    # -- construction --------------------------------------------------------

    @staticmethod
    def from_dict(data: Mapping[str, Any], name_hint: str = "") -> "Label":
        name = str(data.get("name") or name_hint or "unnamed label")
        label_format = str(data.get("format", "US_2016"))
        if label_format not in FORMATS:
            raise LabelError(f"{name}: format must be one of {FORMATS}, got {label_format!r}")

        provenance = str(data.get("provenance", "constructed"))
        if provenance not in PROVENANCE_KINDS:
            raise LabelError(
                f"{name}: provenance must be one of {sorted(PROVENANCE_KINDS)}, "
                f"got {provenance!r}. Every fixture has to say where its numbers came "
                f"from; a corpus of unattributed numbers proves nothing."
            )

        default_basis = "per_serving" if label_format == "US_2016" else "per_100g"
        basis = str(data.get("declared_basis", default_basis))
        if basis not in BASES:
            raise LabelError(f"{name}: declared_basis must be one of {BASES}, got {basis!r}")

        serving = None
        household = ""
        if "serving" in data:
            block = data["serving"]
            if not isinstance(block, Mapping):
                raise LabelError(f"{name}: 'serving' must be an object")
            household = str(block.get("household", ""))
            if "amount" in block:
                serving = Quantity.parse(block, f"{name}: serving")
                if dimension_of(serving.unit) not in ("mass", "volume"):
                    raise LabelError(
                        f"{name}: serving unit {serving.unit!r} is neither a mass nor a volume"
                    )

        spc_raw = data.get("servings_per_container")
        spc = None if spc_raw is None else Declaration.parse(spc_raw)

        net = None
        if "net_quantity" in data:
            net = Quantity.parse(data["net_quantity"], f"{name}: net_quantity")

        rules = rules_for(label_format)
        references = daily_values_for(label_format)
        nutrients: dict[str, DeclaredNutrient] = {}
        raw_nutrients = data.get("nutrients")
        if not isinstance(raw_nutrients, Mapping) or not raw_nutrients:
            raise LabelError(f"{name}: 'nutrients' must be a non-empty object")

        for key, raw in raw_nutrients.items():
            unit_override = None
            value: Any = raw
            if isinstance(raw, Mapping):
                if "value" not in raw:
                    raise LabelError(f"{name}: nutrient {key!r} object needs a 'value'")
                value = raw["value"]
                unit_override = raw.get("unit")
            declaration = Declaration.parse(value)

            rule = rules.get(key)
            if rule is not None:
                unit = normalise_unit(unit_override) if unit_override else rule.unit
                if unit != rule.unit:
                    # Convert the printed figure into the unit the rule is written in,
                    # then invert. Inverting first would apply gram-sized rounding steps
                    # to a milligram figure.
                    declaration = Declaration(
                        declaration.kind, convert(declaration.amount, unit, rule.unit)
                    )
                    unit = rule.unit
            else:
                reference = references.get(key)
                if reference is None and unit_override is None:
                    raise LabelError(
                        f"{name}: nutrient {key!r} is not in the {label_format} rule table "
                        f"and has no reference value, so its unit is unknown. Give it as "
                        f'{{"value": ..., "unit": "..."}} or correct the key.'
                    )
                unit = normalise_unit(unit_override) if unit_override else reference[1].split()[0]
                rule = significant_figure_rule(
                    key.replace("_", " "),
                    unit,
                    declaration.amount if declaration.kind == "value" else declaration.amount,
                    note=f"{key} has no rounding rule in the {label_format} table.",
                )
            rule = refine_for_declaration(rule, declaration)
            nutrients[key] = DeclaredNutrient(key, declaration, unit, rule)

        declared_dv: dict[str, Fraction] = {}
        for key, percent in (data.get("declared_dv_percent") or {}).items():
            declared_dv[key] = frac(percent)

        return Label(
            name=name,
            label_format=label_format,
            basis=basis,
            serving=serving,
            serving_household=household,
            servings_per_container=spc,
            net_quantity=net,
            nutrients=nutrients,
            provenance=provenance,
            source_note=str(data.get("source_note", "")),
            declared_dv_percent=declared_dv,
        )

    @staticmethod
    def load(path: Path) -> "Label":
        with Path(path).open(encoding="utf-8") as handle:
            data = json.load(handle)
        return Label.from_dict(data, name_hint=Path(path).stem)

    # -- derived quantities --------------------------------------------------

    @property
    def medium_unit(self) -> str:
        """'g' for a solid, 'ml' for a liquid. Determines whether the hundred-unit
        basis is per 100 g or per 100 ml."""
        if self.serving is not None:
            return "ml" if dimension_of(self.serving.unit) == "volume" else "g"
        if self.net_quantity is not None:
            return "ml" if dimension_of(self.net_quantity.unit) == "volume" else "g"
        return "g"

    def serving_interval(self) -> Interval:
        """The serving size as an interval, in the label's medium unit.

        The serving size is printed rounded like everything else, and it divides into
        every per-100 figure, so its width propagates. A 30 g serving is really
        [29.5, 30.5), which is 3.3 percent wide, and that lands on every per-100 g
        number derived from a per-serving panel.
        """
        if self.serving is None:
            raise LabelError(
                f"{self.name}: no serving size is declared, so per-serving and per-100 "
                f"figures cannot be related. Add serving.amount and serving.unit."
            )
        rule = serving_size_rule_for(self.label_format)
        # The rule's steps are stated in grams and apply unchanged to millilitres, so
        # the conversion is into the label's own medium unit rather than into grams.
        amount = convert(self.serving.amount, self.serving.unit, self.medium_unit)
        return rule.inverse(Declaration("value", amount))

    def servings_interval(self) -> Interval:
        if self.servings_per_container is None:
            raise LabelError(
                f"{self.name}: no servings per container is declared, so a per-container "
                f"figure cannot be derived."
            )
        return US_SERVINGS_PER_CONTAINER_RULE.inverse(self.servings_per_container)

    def container_interval(self) -> tuple[Interval, str]:
        """Total container size in the medium unit, and how it was obtained.

        Prefers the declared net quantity, which is a single rounded figure, over
        servings multiplied by serving size, which multiplies two intervals and is
        therefore much wider. Both are reported by the container consistency check.
        """
        if self.net_quantity is not None:
            amount = convert(self.net_quantity.amount, self.net_quantity.unit, self.medium_unit)
            # Net quantity precision is not regulated the way the panel is, so infer
            # the step from the printed figure.
            rule = significant_figure_rule(
                "net quantity", self.medium_unit, amount, "net quantity of contents"
            )
            return rule.inverse(Declaration("value", amount)), "declared net quantity"
        return self.serving_interval() * self.servings_interval(), "servings x serving size"

    def keys(self) -> list[str]:
        return list(self.nutrients)

    def summary(self) -> str:
        serving = "no serving declared"
        if self.serving is not None:
            household = f" ({self.serving_household})" if self.serving_household else ""
            serving = f"{float(self.serving.amount):g} {self.serving.unit}{household}"
        return f"{self.name} [{self.label_format}, declared {self.basis}, serving {serving}]"
