"""Read a nutrition panel out of OCR output, and refuse to guess.

Scope, stated plainly because the boundary matters. This module does not run OCR. It
consumes the output of an OCR engine, specifically Tesseract's TSV format, which
carries a per-word confidence alongside each token. Shipping an OCR engine would add a
large binary dependency and would not make the hard part any easier, because the hard
part is not reading the digits. It is knowing which digits you did not read.

The failure mode this module exists to prevent: an engine returns "16O mg" with a
confidence of 41, a naive parser strips the non-digit and reports 160 mg, and the rest
of the tool computes an interval to three decimal places on top of a character that was
never read. Every number here therefore carries the confidence of the least confident
token that produced it, and anything below the threshold is refused rather than
repaired. A refused field is absent from the parsed label, so the normalizer reports a
missing row instead of a wrong one.

Ambiguous glyphs get a second, stricter test. O against 0, l and I against 1, S against
5, B against 8 and Z against 2 are the substitutions that turn a plausible number into
a different plausible number, so a token containing one of them is refused at a higher
confidence threshold than a clean token.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# Tesseract reports confidence as 0 to 100, with -1 for structural rows that carry no
# text. These two thresholds are the module's only tunable numbers and both are
# arguments with defaults rather than constants buried in the parser.
DEFAULT_MIN_CONFIDENCE = 80.0
AMBIGUOUS_MIN_CONFIDENCE = 92.0

AMBIGUOUS_GLYPHS = "OolIiSsBbZz"

NUMBER_PATTERN = re.compile(r"^[<]?\s*([0-9OolIiSsBbZz]+(?:[.,][0-9OolIiSsBbZz]+)?)\s*$")

# Two structural rows that are not nutrients but are needed to normalise anything.
# They are read through the same confidence gate as the nutrient rows, because a
# misread serving size corrupts every per-100 figure on the panel at once.
STRUCTURAL_KEYS = ("serving_size", "servings_per_container")

# Panel row labels, longest first so that "saturated fat" wins over "fat" and
# "added sugars" over "sugars".
ROW_PATTERNS: list[tuple[str, str, tuple[str, ...]]] = [
    ("servings_per_container", "servings", ("servings per container", "serving per container")),
    ("serving_size", "g", ("serving size", "portion size")),
    ("energy_kcal", "kcal", ("calories", "energy")),
    ("saturated_fat", "g", ("saturated fat", "of which saturates", "saturates", "sat fat")),
    ("trans_fat", "g", ("trans fat", "trans")),
    ("monounsaturated_fat", "g", ("monounsaturated fat", "monounsaturates")),
    ("polyunsaturated_fat", "g", ("polyunsaturated fat", "polyunsaturates")),
    ("cholesterol", "mg", ("cholesterol", "cholest")),
    ("sodium", "mg", ("sodium",)),
    ("salt", "g", ("salt",)),
    ("added_sugars", "g", ("added sugars", "includes added sugars", "of which added sugars")),
    ("total_sugars", "g", ("total sugars", "of which sugars", "sugars")),
    ("fiber", "g", ("dietary fiber", "dietary fibre", "fibre", "fiber")),
    ("carbohydrate", "g", ("total carbohydrate", "carbohydrate", "total carb", "carbs")),
    ("protein", "g", ("protein",)),
    ("fat", "g", ("total fat", "fat")),
    ("potassium", "mg", ("potassium",)),
    ("calcium", "mg", ("calcium",)),
    ("iron", "mg", ("iron",)),
    ("vitamin_d", "mcg", ("vitamin d", "vit d")),
]


@dataclass(frozen=True)
class Token:
    text: str
    confidence: float
    line: tuple[int, int, int, int]


@dataclass(frozen=True)
class ReadRow:
    key: str
    raw_value: str
    unit: str
    confidence: float
    accepted: bool
    reason: str


@dataclass
class OcrRead:
    rows: list[ReadRow] = field(default_factory=list)
    unmatched_lines: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> list[ReadRow]:
        return [row for row in self.rows if row.accepted]

    @property
    def refused(self) -> list[ReadRow]:
        return [row for row in self.rows if not row.accepted]

    def to_label_dict(self, name: str, label_format: str = "US_2016", **extra) -> dict:
        """Build a label dictionary from the accepted rows only.

        Refused rows are omitted rather than filled with a best guess. A downstream
        "no protein row" is a visible gap; a downstream "protein 8 g" that came from a
        token the engine was 41 percent sure of is an invisible error.
        """
        nutrients = {}
        data: dict = {
            "name": name,
            "format": label_format,
            "provenance": "constructed",
            "source_note": "parsed from OCR output",
        }
        for row in self.accepted:
            if row.key == "serving_size":
                data["serving"] = {"amount": row.raw_value, "unit": row.unit}
            elif row.key == "servings_per_container":
                data["servings_per_container"] = row.raw_value
            else:
                nutrients[row.key] = {"value": row.raw_value, "unit": row.unit}
        data["nutrients"] = nutrients
        data.update(extra)
        return data


def _clean_number(text: str) -> Optional[str]:
    """Return the numeric text with a decimal point, or None if it is not a number.

    Letters that OCR substitutes for digits are deliberately NOT mapped back. A token
    reading "16O" is refused, not silently corrected to 160, because the correction is
    a guess and the whole contract of this module is that it does not guess.
    """
    match = NUMBER_PATTERN.match(text)
    if not match:
        return None
    body = match.group(1).replace(",", ".")
    if any(char in AMBIGUOUS_GLYPHS for char in body):
        return None
    prefix = "<" if text.strip().startswith("<") else ""
    return prefix + body


def _has_ambiguous_glyph(text: str) -> bool:
    return any(char in AMBIGUOUS_GLYPHS for char in text)


def _strip_decoration(text: str) -> str:
    """Peel the parentheses and trailing unit off a token, leaving the number.

    "(55g)" becomes "55" and "160mg" becomes "160". The unit is not read back from the
    token; it comes from the row table, because a misread unit is a factor of a
    thousand and there is no confidence score that makes that guess acceptable.
    """
    return text.strip().strip("()[]").rstrip("gmL%").strip()


def read_tsv(
    path: Path,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ambiguous_min_confidence: float = AMBIGUOUS_MIN_CONFIDENCE,
) -> OcrRead:
    tokens = load_tokens(path)
    return read_tokens(tokens, min_confidence, ambiguous_min_confidence)


def load_tokens(path: Path) -> list[Token]:
    """Read Tesseract TSV. Raises on a malformed file rather than returning nothing.

    A parser that returns an empty list for an unreadable file makes "the panel had no
    protein row" and "the file was not TSV" the same observation downstream.
    """
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"level", "page_num", "block_num", "par_num", "line_num", "conf", "text"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{path}: not Tesseract TSV, missing columns {sorted(missing)}. "
                f"Produce it with: tesseract panel.png stdout -c "
                f"tessedit_create_tsv=1 tsv"
            )
        tokens: list[Token] = []
        for line_number, row in enumerate(reader, start=2):
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                confidence = float(row["conf"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path}:{line_number}: unreadable conf field: {error}") from error
            if confidence < 0:
                continue
            key = (
                int(row["page_num"]), int(row["block_num"]),
                int(row["par_num"]), int(row["line_num"]),
            )
            tokens.append(Token(text, confidence, key))
    if not tokens:
        raise ValueError(f"{path}: contains no text tokens at all")
    return tokens


def group_lines(tokens: Iterable[Token]) -> list[list[Token]]:
    lines: dict[tuple[int, int, int, int], list[Token]] = {}
    for token in tokens:
        lines.setdefault(token.line, []).append(token)
    return [lines[key] for key in sorted(lines)]


def read_tokens(
    tokens: Iterable[Token],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ambiguous_min_confidence: float = AMBIGUOUS_MIN_CONFIDENCE,
) -> OcrRead:
    result = OcrRead()
    seen: set[str] = set()
    for line in group_lines(tokens):
        text = " ".join(token.text for token in line)
        lowered = text.lower()
        matched = None
        for key, unit, phrases in ROW_PATTERNS:
            if key in seen:
                continue
            for phrase in phrases:
                if phrase in lowered:
                    matched = (key, unit, phrase)
                    break
            if matched:
                break
        if not matched:
            result.unmatched_lines.append(text)
            continue
        key, unit, phrase = matched

        value_token = None
        stripped = ""
        for token in line:
            candidate = _strip_decoration(token.text)
            if candidate and (NUMBER_PATTERN.match(token.text.strip()) or NUMBER_PATTERN.match(candidate)):
                value_token = token
                stripped = candidate
                break
        if value_token is None:
            result.unmatched_lines.append(text)
            continue

        seen.add(key)
        raw = value_token.text.strip()
        confidence = value_token.confidence
        threshold = ambiguous_min_confidence if _has_ambiguous_glyph(stripped) else min_confidence
        cleaned = _clean_number(stripped)

        if cleaned is None:
            result.rows.append(ReadRow(
                key, raw, unit, confidence, False,
                f"the token {raw!r} contains a glyph that OCR confuses with a digit "
                f"({', '.join(sorted(set(c for c in stripped if c in AMBIGUOUS_GLYPHS)))}). "
                f"Refused rather than corrected.",
            ))
            continue
        if confidence < threshold:
            result.rows.append(ReadRow(
                key, cleaned, unit, confidence, False,
                f"confidence {confidence:.0f} is below the {threshold:.0f} required for "
                f"this token. Re-photograph the panel or enter this row by hand.",
            ))
            continue
        result.rows.append(ReadRow(key, cleaned, unit, confidence, True, "accepted"))
    return result


def summarise(read: OcrRead) -> str:
    lines = [f"OCR read {len(read.rows)} panel rows, "
             f"{len(read.accepted)} accepted, {len(read.refused)} refused."]
    for row in read.rows:
        mark = "ok " if row.accepted else "REFUSED"
        lines.append(f"  {mark} {row.key:<20} {row.raw_value:>8} {row.unit:<4} "
                     f"conf {row.confidence:5.1f}  {row.reason}")
    if read.unmatched_lines:
        lines.append(f"  {len(read.unmatched_lines)} line(s) matched no known panel row.")
    return "\n".join(lines)
