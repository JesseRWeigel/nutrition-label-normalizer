#!/usr/bin/env python3
"""Digest everything the tool computes from the fixture corpus, into one hash.

This is the measuring instrument for ``scripts/sabotage.py``. Gate 2 of the three-gate
rule asks whether a sabotage actually changed what the tool produces, and that question
needs a number that moves when the answers move and stays still when they do not.

Two properties matter more than coverage, and both are here for the same reason. On
2026-08-06 a harness elsewhere in this fleet hashed a CLI line containing an absolute
path inside a per-run temporary directory, so every fingerprint differed from the
baseline for a reason having nothing to do with the code, gate 2 passed for free, and
eleven sabotages were scored as proven while proving nothing.

  NO ABSOLUTE PATHS ANYWHERE IN THE DIGESTED TEXT. Fixtures are named by the path
  relative to the repository root, and the two CLI invocations below are given relative
  arguments. ``scripts/sabotage.py`` copies the tree into a temporary directory and
  requires this file to produce the identical hash there, which is the null control that
  would catch a violation.

  EVERY FAILURE IS RECORDED RATHER THAN RAISED. A sabotage that makes the tool throw has
  changed the output and must register as such. An exception that escaped here would
  instead abort the measurement and look like a harness bug.

Run it with ``--dump`` to see the digested text rather than the hash, which is how you
find out what a sabotage actually moved.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import subprocess
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASES = ("per_serving", "per_100g", "per_100kcal", "per_container")


def emit(lines: list[str], text: str) -> None:
    lines.append(text)


def guarded(lines: list[str], tag: str, thunk) -> None:
    """Run ``thunk`` and append its result, recording an exception as data.

    The exception type and message go into the digest. A sabotage that turns a correct
    answer into a crash has changed the output, and that has to be visible to gate 2.
    """
    try:
        value = thunk()
    except Exception as error:  # deliberately broad: see the module docstring
        emit(lines, f"{tag}\tERROR\t{type(error).__name__}\t{error}")
        return
    emit(lines, f"{tag}\t{value}")


def label_section(lines: list[str], rel: str) -> None:
    from nutrilabel.checks import run_all
    from nutrilabel.energy import check_energy, kj_kcal_ratio_note
    from nutrilabel.label import Label
    from nutrilabel.normalize import normalize, percent_daily_value

    try:
        label = Label.load(ROOT / rel)
    except Exception as error:
        emit(lines, f"{rel}\tLOAD-ERROR\t{type(error).__name__}\t{error}")
        return

    emit(lines, f"{rel}\tsummary\t{label.summary()}")
    emit(lines, f"{rel}\tprovenance\t{label.provenance}")
    guarded(lines, f"{rel}\tserving_interval", lambda: repr(label.serving_interval()))
    guarded(lines, f"{rel}\tservings_interval", lambda: repr(label.servings_interval()))
    guarded(lines, f"{rel}\tcontainer_interval", lambda: repr(label.container_interval()))

    for basis in BASES:
        try:
            result = normalize(label, basis)
        except Exception as error:
            emit(lines, f"{rel}\t{basis}\tERROR\t{type(error).__name__}\t{error}")
            continue
        emit(lines, f"{rel}\t{basis}\tbasis_label\t{result.basis_label}")
        emit(lines, f"{rel}\t{basis}\tserving_note\t{result.serving_note}")
        for warning in result.warnings:
            emit(lines, f"{rel}\t{basis}\twarning\t{warning}")
        for key in sorted(result.values):
            value = result.values[key]
            # Exact rationals, not floats. A sabotage that shifts an endpoint by one
            # part in 10^15 is still a wrong answer, and str(Fraction) shows it.
            emit(
                lines,
                f"{rel}\t{basis}\t{key}\t{value.interval.lo}\t{value.interval.hi}\t"
                f"{value.interval.hi_open}\t{value.unit}\t{value.rule_basis}\t"
                f"{value.citation}\t{value.derivation}",
            )
            if basis in ("per_serving", "per_container"):
                band = percent_daily_value(label, value)
                if band is not None:
                    emit(lines, f"{rel}\t{basis}\t{key}\tdv\t{band.lo}\t{band.hi}")

    for column in ("kcal", "kj"):
        guarded(
            lines,
            f"{rel}\tenergy\t{column}",
            lambda column=column: repr(check_energy(label, column).to_json()),
        )
    guarded(lines, f"{rel}\tkj_kcal_ratio", lambda: kj_kcal_ratio_note(label).message)

    try:
        findings = run_all(label)
    except Exception as error:
        emit(lines, f"{rel}\tfindings\tERROR\t{type(error).__name__}\t{error}")
        return
    for finding in sorted(findings, key=lambda f: (f.code, f.message)):
        emit(lines, f"{rel}\tfinding\t{finding.code}\t{finding.severity}\t{finding.message}")


def rules_section(lines: list[str]) -> None:
    from nutrilabel.rounding import Declaration, round_half_up
    from nutrilabel.rulesets import (
        EU_ENERGY_FACTORS,
        EU_REFERENCE_INTAKES,
        EU_SERVING_SIZE_RULE,
        KJ_PER_KCAL,
        SALT_PER_SODIUM,
        US_ATWATER,
        US_DAILY_VALUES,
        US_SERVING_SIZE_RULE,
        US_SERVINGS_PER_CONTAINER_RULE,
        rules_for,
    )
    from fractions import Fraction

    # A dense sweep of true values through the forward rule, and the inverse of every
    # form it produces. This is what makes a one-boundary edit in the rule tables move
    # the hash even when no fixture happens to sit near that boundary.
    probes = [Fraction(n, 4) for n in range(0, 4 * 60)] + [
        Fraction(n) for n in (60, 75, 100, 139, 140, 141, 200, 230, 500, 1000, 2400)
    ]
    for fmt in ("US_2016", "EU_1169"):
        for name, rule in sorted(rules_for(fmt).items()):
            emit(
                lines,
                f"rule\t{fmt}\t{name}\t{rule.unit}\t{rule.basis}\t{rule.citation}\t"
                f"{rule.notes}",
            )
            for segment in rule.segments:
                emit(lines, f"rule\t{fmt}\t{name}\tseg\t{segment.lo}\t{segment.hi}\t{segment.forms}")
            for probe in probes:
                forms = sorted(
                    (d.kind, str(d.amount)) for d in rule.permitted(probe)
                )
                emit(lines, f"rule\t{fmt}\t{name}\tfwd\t{probe}\t{forms}")
                for kind, amount in forms:
                    guarded(
                        lines,
                        f"rule\t{fmt}\t{name}\tinv\t{kind}\t{amount}",
                        lambda kind=kind, amount=amount: repr(
                            rule.inverse(Declaration(kind, Fraction(amount)))
                        ),
                    )

    for name, rule in (
        ("us_serving", US_SERVING_SIZE_RULE),
        ("us_servings_per_container", US_SERVINGS_PER_CONTAINER_RULE),
        ("eu_serving", EU_SERVING_SIZE_RULE),
    ):
        for probe in probes:
            guarded(
                lines,
                f"srule\t{name}\t{probe}",
                lambda rule=rule, probe=probe: repr(rule.inverse(rule.declare(probe))),
            )

    for key, (amount, unit) in sorted(US_DAILY_VALUES.items()):
        emit(lines, f"dv\tUS_2016\t{key}\t{amount}\t{unit}")
    for key, (amount, unit) in sorted(EU_REFERENCE_INTAKES.items()):
        emit(lines, f"dv\tEU_1169\t{key}\t{amount}\t{unit}")
    for key, value in sorted(US_ATWATER.items()):
        emit(lines, f"atwater\tUS\t{key}\t{value}")
    for key, (kj, kcal) in sorted(EU_ENERGY_FACTORS.items()):
        emit(lines, f"atwater\tEU\t{key}\t{kj}\t{kcal}")
    emit(lines, f"constant\tkj_per_kcal\t{KJ_PER_KCAL}")
    emit(lines, f"constant\tsalt_per_sodium\t{SALT_PER_SODIUM}")
    emit(lines, f"constant\thalf_up_2.5\t{round_half_up(Fraction(5, 2), Fraction(1))}")


def units_section(lines: list[str]) -> None:
    from fractions import Fraction

    from nutrilabel.units import (
        convert,
        dimension_of,
        kcal_to_kj,
        kj_to_kcal,
        normalise_unit,
        salt_to_sodium,
        sodium_to_salt,
    )

    for spelling in ("g", "G", "gram", "grams", "mg", "MG", "mcg", "ug", "µg", "μg",
                     "microgram", "kg", "ml", "mL", "l", "litre", "kcal", "kJ", "kj"):
        guarded(lines, f"unit\tnormalise\t{spelling}", lambda s=spelling: normalise_unit(s))
        guarded(lines, f"unit\tdimension\t{spelling}",
                lambda s=spelling: dimension_of(normalise_unit(s)))
    for value, src, dst in (
        (1, "g", "mg"), (1, "mg", "g"), (1000, "mcg", "mg"), (1, "kg", "g"),
        (1, "l", "ml"), (355, "ml", "l"), (1, "g", "kg"),
    ):
        guarded(lines, f"unit\tconvert\t{value}\t{src}\t{dst}",
                lambda v=value, s=src, d=dst: str(convert(Fraction(v), s, d)))
    guarded(lines, "unit\tcross_dimension", lambda: str(convert(Fraction(1), "g", "ml")))
    for grams in ("0.12", "1", "2.3"):
        guarded(lines, f"unit\tsodium_to_salt\t{grams}",
                lambda g=grams: str(sodium_to_salt(Fraction(g))))
        guarded(lines, f"unit\tsalt_to_sodium\t{grams}",
                lambda g=grams: str(salt_to_sodium(Fraction(g))))
    for energy in ("100", "230", "2362"):
        guarded(lines, f"unit\tkcal_to_kj\t{energy}",
                lambda e=energy: str(kcal_to_kj(Fraction(e))))
        guarded(lines, f"unit\tkj_to_kcal\t{energy}",
                lambda e=energy: str(kj_to_kcal(Fraction(e))))


def ocr_section(lines: list[str]) -> None:
    from nutrilabel.ocr import read_tsv, summarise

    for rel in sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "fixtures" / "ocr").glob("*.tsv")):
        for threshold in (60, 80, 95):
            try:
                read = read_tsv(ROOT / rel, min_confidence=threshold)
            except Exception as error:
                emit(lines, f"{rel}\tocr\t{threshold}\tERROR\t{type(error).__name__}\t{error}")
                continue
            emit(lines, f"{rel}\tocr\t{threshold}\tsummary\t{summarise(read)}")
            for row in read.rows:
                emit(lines, f"{rel}\tocr\t{threshold}\trow\t{row}")
            for line in read.unmatched_lines:
                emit(lines, f"{rel}\tocr\t{threshold}\tunmatched\t{line}")
            emit(lines, f"{rel}\tocr\t{threshold}\taccepted\t{len(read.accepted)}"
                        f"\trefused\t{len(read.refused)}")
            guarded(lines, f"{rel}\tocr\t{threshold}\tlabel_dict",
                    lambda r=read: repr(sorted(r.to_label_dict("probe").items())))


def page_section(lines: list[str]) -> None:
    """The generated page, as data rather than as a file on disk.

    ``build_page.py --check`` compares the committed file against a fresh build, so a
    sabotage that changes the page without changing the committed copy is caught by
    verify. Here the page is only hashed, and the build is run in process so nothing is
    written into the tree being measured.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_page
    except Exception as error:
        emit(lines, f"page\tIMPORT-ERROR\t{type(error).__name__}\t{error}")
        return
    try:
        html = build_page.render_page()
    except Exception as error:
        emit(lines, f"page\tBUILD-ERROR\t{type(error).__name__}\t{error}")
        return
    emit(lines, f"page\tsha256\t{hashlib.sha256(html.encode('utf-8')).hexdigest()}")
    emit(lines, f"page\tbytes\t{len(html.encode('utf-8'))}")


def cli_section(lines: list[str]) -> None:
    """Two real command line invocations, so cli.py is not sabotage-proof by omission.

    Relative arguments only, and ``cwd`` is the repository root, so no absolute path can
    reach the digested text. ``check`` exits 1 on the broken fixtures by design, so the
    exit code is recorded rather than treated as a failure of the harness.
    """
    invocations = [
        ["compare", "fixtures/labels/cereal-a-flakes.json",
         "fixtures/labels/cereal-b-granola.json", "--basis", "per_100g"],
        ["check", "fixtures/labels/broken-energy-mismatch.json",
         "fixtures/labels/fda-sample-panel.json"],
        ["rules", "--format", "US_2016", "--examples"],
        ["ocr", "fixtures/ocr/smudged-panel.tsv"],
    ]
    for argv in invocations:
        tag = "cli\t" + " ".join(argv)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "nutrilabel", *argv],
                cwd=ROOT, capture_output=True, text=True, timeout=180,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except Exception as error:
            emit(lines, f"{tag}\tERROR\t{type(error).__name__}\t{error}")
            continue
        emit(lines, f"{tag}\texit\t{result.returncode}")
        emit(lines, f"{tag}\tstdout\t{result.stdout}")
        # stderr can carry a traceback with absolute paths in it, so only the exception
        # type on the final line is kept. The path would make every copy of the tree
        # fingerprint differently and hand gate 2 a free pass.
        tail = [line for line in result.stderr.splitlines() if line.strip()]
        emit(lines, f"{tag}\tstderr_last\t{tail[-1] if tail else ''}")


def build() -> str:
    lines: list[str] = []
    labels = sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "fixtures" / "labels").glob("*.json")
    )
    if not labels:
        # A fingerprint over an empty corpus is stable and meaningless, which is the
        # silent-failure shape. Make it loud instead.
        raise SystemExit("fingerprint: no label fixtures found, so there is nothing to measure")
    for rel in labels:
        label_section(lines, rel)
    rules_section(lines)
    units_section(lines)
    ocr_section(lines)
    page_section(lines)
    cli_section(lines)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump", action="store_true",
                        help="print the digested text instead of the hash")
    args = parser.parse_args()

    # Anything the tool prints to stdout while being measured would land in the hash by
    # accident. Capture it and fold it in deliberately, in one place.
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            text = build()
    except SystemExit:
        raise
    except Exception:
        text = "FINGERPRINT-CRASHED\n" + traceback.format_exc(limit=0)
    noise = buffer.getvalue()
    if noise:
        text += "stray-output\t" + hashlib.sha256(noise.encode()).hexdigest() + "\n"

    if args.dump:
        sys.stdout.write(text)
        return 0
    print(hashlib.sha256(text.encode("utf-8")).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
