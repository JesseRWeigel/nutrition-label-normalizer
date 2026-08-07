"""nutrilabel: restate nutrition labels on a common basis, carrying their own rounding.

This is a comparison tool. It is not medical or dietary advice, and it cannot tell you
whether a package is labelled correctly. See the README for what it cannot do.
"""

from .checks import Finding, flags, run_all
from .energy import EnergyCheck, check_energy, kj_kcal_ratio_note
from .interval import Interval, format_fraction
from .label import Label, LabelError
from .normalize import BASIS_CHOICES, NormalizedLabel, NormalizedValue, normalize
from .rounding import Declaration, RoundingRule

__version__ = "1.0.0"

__all__ = [
    "BASIS_CHOICES",
    "Declaration",
    "EnergyCheck",
    "Finding",
    "Interval",
    "Label",
    "LabelError",
    "NormalizedLabel",
    "NormalizedValue",
    "RoundingRule",
    "__version__",
    "check_energy",
    "flags",
    "format_fraction",
    "kj_kcal_ratio_note",
    "normalize",
    "run_all",
]
