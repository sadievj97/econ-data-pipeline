"""Writing numbers as text without losing or inventing precision."""

import math

import numpy as np


def fmt(value: float | None, precision: str = "float64") -> str:
    """The shortest text that reads back as exactly this value: at 4-byte precision
    for values a source stores as 4-byte floats (PWT), otherwise as a full double."""
    if value is None or math.isnan(float(value)):
        return "missing"
    if precision == "float32":
        return np.format_float_positional(np.float32(value), unique=True, trim="-")
    return repr(float(value))


def plural(n: float, word: str) -> str:
    return f"{n:g} {word}" if n == 1 else f"{n:g} {word}s"
