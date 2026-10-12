"""Known values: numbers you trust, with sources, that the harmonized panel must match exactly.

They live next to the spec as <spec file name>.known_values.yaml. "Exactly"
means at the source's own storage precision: PWT stores many variables as
4-byte floats, so 0.37945354 and 0.37945353984832764 are the same PWT value.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field


class KnownValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variable: str
    iso3: str = Field(pattern=r"^[A-Z]{3}$")
    year: int
    value: float
    source: str = Field(min_length=1)


class KnownValueFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: list[KnownValue] = Field(default_factory=list)


@dataclass(frozen=True)
class KnownValueResult:
    known: KnownValue
    actual: float | None
    matched: bool
    note: str


def known_values_path(spec_path: Path) -> Path:
    return spec_path.with_name(f"{spec_path.stem}.known_values.yaml")


def load_known_values(path: Path) -> list[KnownValue]:
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    return KnownValueFile.model_validate(data).values


def same_value(expected: float, actual: float, precision: str) -> bool:
    if precision == "float32":
        return bool(np.float32(expected) == np.float32(actual))
    return expected == actual
