"""Command line interface.

    python3 -m nutrilabel normalize FIXTURE [--basis per_100g]
    python3 -m nutrilabel compare FIXTURE FIXTURE [...] [--basis per_100g]
    python3 -m nutrilabel check FIXTURE [...]
    python3 -m nutrilabel ocr TSV [--min-confidence 80]
    python3 -m nutrilabel rules [--format US_2016]

Exit codes carry the result. ``check`` exits 1 when a label contradicts itself and
``ocr`` exits 1 when any row was refused, so both can be used in a pipeline without
parsing the text output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .checks import FLAG, run_all
from .interval import format_fraction
from .label import Label, LabelError
from .normalize import BASIS_CHOICES, normalize, percent_daily_value
from .ocr import DEFAULT_MIN_CONFIDENCE, read_tsv, summarise
from .rounding import Declaration
from .rulesets import EU_RULES, US_RULES

BASIS_HELP = ", ".join(BASIS_CHOICES)


def _load(paths: Sequence[str]) -> list[Label]:
    labels = []
    for path in paths:
        try:
            labels.append(Label.load(Path(path)))
        except (OSError, json.JSONDecodeError) as error:
            # Re-raised as a LabelError so main() reports it with the same exit code as
            # every other bad input. A SystemExit here would bypass main's handler and
            # give a different code for what is the same class of problem.
            raise LabelError(f"cannot read {path}: {error}") from error
    return labels


def _basis_banner(normalized) -> str:
    return f"BASIS IN FORCE: {normalized.basis_label}  ({normalized.basis})"


def cmd_normalize(args) -> int:
    label = _load([args.file])[0]
    normalized = normalize(label, args.basis)
    if args.json:
        print(json.dumps(normalized.to_json(), indent=2))
        return 0
    print(label.summary())
    print(_basis_banner(normalized))
    print(f"  {normalized.serving_note}")
    for warning in normalized.warnings:
        print(f"  note: {warning}")
    print()
    width = max(len(key) for key in normalized.values) if normalized.values else 10
    print(f"  {'nutrient'.ljust(width)}  {'low':>10}  {'high':>10}  unit   rule")
    for key, value in normalized.values.items():
        marker = "" if value.rule_basis == "cited" else f"  <- {value.rule_basis}"
        print(
            f"  {key.ljust(width)}  {format_fraction(value.interval.lo, 3):>10}  "
            f"{format_fraction(value.interval.hi, 3):>10}  {value.unit:<5}"
            f"  {value.citation}{marker}"
        )
    if args.explain:
        print("\nderivations:")
        for key, value in normalized.values.items():
            print(f"  {key}: {value.derivation}")
    if args.dv and args.basis in ("per_serving", "per_container"):
        print("\npercent of daily value on this basis:")
        for key, value in normalized.values.items():
            band = percent_daily_value(label, value)
            if band is not None:
                print(f"  {key.ljust(width)}  {band.render(1)} %")
    return 0


def cmd_compare(args) -> int:
    labels = _load(args.files)
    if len(labels) < 2:
        raise SystemExit("nutrilabel: compare needs at least two labels")
    normalized = [normalize(label, args.basis) for label in labels]
    if args.json:
        print(json.dumps([item.to_json() for item in normalized], indent=2))
        return 0

    bases = {item.basis_label for item in normalized}
    print(f"BASIS IN FORCE: {' / '.join(sorted(bases))}  ({args.basis})")
    for label, item in zip(labels, normalized):
        print(f"  {label.name}: {item.serving_note}")
    print()

    keys: list[str] = []
    for item in normalized:
        for key in item.values:
            if key not in keys:
                keys.append(key)
    name_width = max(len(item.label_name) for item in normalized)
    key_width = max(len(key) for key in keys)
    header = "  " + "nutrient".ljust(key_width)
    for item in normalized:
        header += "  " + item.label_name.rjust(max(17, name_width))
    print(header)
    for key in keys:
        row = "  " + key.ljust(key_width)
        for item in normalized:
            value = item.values.get(key)
            cell = "-" if value is None else value.interval.render(2)
            row += "  " + cell.rjust(max(17, name_width))
        unit = next((item.values[key].unit for item in normalized if key in item.values), "")
        print(f"{row}  {unit}")

    print("\nratios between the first label and each of the others, as intervals:")
    first = normalized[0]
    for item in normalized[1:]:
        print(f"  {first.label_name} vs {item.label_name}")
        for key in keys:
            a, b = first.values.get(key), item.values.get(key)
            if a is None or b is None or b.interval.lo <= 0:
                continue
            ratio = a.interval / b.interval
            print(f"    {key.ljust(key_width)}  {ratio.render(2)} x")
    return 0


def cmd_check(args) -> int:
    labels = _load(args.files)
    payload = []
    worst = 0
    for label in labels:
        findings = run_all(label)
        payload.append({"label": label.name, "findings": [f.to_json() for f in findings]})
        if not args.json:
            print(label.summary())
            for finding in findings:
                marker = "FLAG" if finding.severity == FLAG else "note"
                print(f"  [{marker}] {finding.code}: {finding.message}")
            print()
        if any(finding.severity == FLAG for finding in findings):
            worst = 1
    if args.json:
        print(json.dumps(payload, indent=2))
    if worst and not args.json:
        print("check: at least one label contradicts itself, exiting 1")
    return worst


def cmd_ocr(args) -> int:
    try:
        read = read_tsv(Path(args.file), min_confidence=args.min_confidence)
    except (OSError, ValueError) as error:
        raise SystemExit(f"nutrilabel: {error}")
    if args.json:
        print(json.dumps(read.to_label_dict(Path(args.file).stem), indent=2))
    else:
        print(summarise(read))
    return 1 if read.refused else 0


def cmd_rules(args) -> int:
    table = US_RULES if args.format == "US_2016" else EU_RULES
    for key, rule in table.items():
        print(f"{key} ({rule.unit})  [{rule.basis}]  {rule.citation}")
        for segment in rule.segments:
            upper = "and above" if segment.hi is None else f"to {float(segment.hi):g}"
            forms = ", ".join(_render_form(form) for form in segment.forms)
            print(f"    {float(segment.lo):g} {upper}: {forms}")
        if args.examples:
            for probe in ("0", "0.4", "1", "2.6", "8", "37", "160", "230"):
                try:
                    declared = rule.declare(probe)
                    back = rule.inverse(declared)
                except ValueError:
                    continue
                print(f"      true {probe} -> prints {declared.render(rule.unit)} "
                      f"-> reads back as {back.render(3)} {rule.unit}")
        if rule.notes:
            print(f"    note: {rule.notes}")
    return 0


def _render_form(form) -> str:
    name = type(form).__name__
    if name == "Nearest":
        return f"nearest {float(form.step):g}"
    if name == "LessThan":
        return f'print "less than {float(form.bound):g}"'
    return "print 0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nutrilabel",
        description="Restate nutrition labels on a common basis, carrying their own "
                    "rounding as an interval. Not medical or dietary advice.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_norm = sub.add_parser("normalize", help="restate one label on a chosen basis")
    p_norm.add_argument("file")
    p_norm.add_argument("--basis", default="per_100g", choices=BASIS_CHOICES, help=BASIS_HELP)
    p_norm.add_argument("--json", action="store_true")
    p_norm.add_argument("--explain", action="store_true", help="show how each figure was derived")
    p_norm.add_argument("--dv", action="store_true", help="also show percent of daily value")
    p_norm.set_defaults(func=cmd_normalize)

    p_cmp = sub.add_parser("compare", help="put two or more labels on one basis side by side")
    p_cmp.add_argument("files", nargs="+")
    p_cmp.add_argument("--basis", default="per_100g", choices=BASIS_CHOICES, help=BASIS_HELP)
    p_cmp.add_argument("--json", action="store_true")
    p_cmp.set_defaults(func=cmd_compare)

    p_chk = sub.add_parser("check", help="cross-check each label against itself, exit 1 on a flag")
    p_chk.add_argument("files", nargs="+")
    p_chk.add_argument("--json", action="store_true")
    p_chk.set_defaults(func=cmd_check)

    p_ocr = sub.add_parser("ocr", help="read a panel from Tesseract TSV, refusing low confidence")
    p_ocr.add_argument("file")
    p_ocr.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    p_ocr.add_argument("--json", action="store_true")
    p_ocr.set_defaults(func=cmd_ocr)

    p_rules = sub.add_parser("rules", help="print the rounding rule tables with their citations")
    p_rules.add_argument("--format", default="US_2016", choices=("US_2016", "EU_1169"))
    p_rules.add_argument("--examples", action="store_true")
    p_rules.set_defaults(func=cmd_rules)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LabelError as error:
        print(f"nutrilabel: {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"nutrilabel: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
