"""What every connector returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..cache import Snapshot
from ..units import UnitInfo


class SourceError(RuntimeError):
    """A source refused a request or sent something unexpected."""


class UnknownCode(SourceError):
    pass


@dataclass(frozen=True)
class IndicatorInfo:
    source: str
    code: str
    name: str
    definition: str
    unit: UnitInfo
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchedSeries:
    """One indicator for the requested countries, exactly as the source gave it:
    columns iso3, year, value (missing values stay missing), sorted by iso3 and year."""

    info: IndicatorInfo
    data: pd.DataFrame
    raw: list[Snapshot]  # the raw files the values came from
    metadata_raw: Snapshot  # the raw file the unit and definition came from

    def provenance(self) -> dict[str, Any]:
        """The record every variable carries (project rule 3)."""
        return {
            "source": self.info.source,
            "code": self.info.code,
            "name": self.info.name,
            "unit": self.info.unit.text,
            "price_basis": self.info.unit.price_basis,
            "base_year": self.info.unit.base_year,
            "currency": self.info.unit.currency,
            "retrieved_at": sorted({s.retrieved_at for s in self.raw}),
            "raw_files": [s.relative_file() for s in self.raw],
            "raw_sha256": [s.sha256 for s in self.raw],
            "metadata_file": self.metadata_raw.relative_file(),
        }


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame({"iso3": pd.Series(dtype="string"), "year": pd.Series(dtype="int64"),
                         "value": pd.Series(dtype="float64")})
