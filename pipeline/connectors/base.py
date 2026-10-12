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
    columns iso3, year, value (missing values stay missing), sorted by iso3 and
    year, and optionally note: the source's own remark on a value (for example
    ILOSTAT "Break in series", FAOSTAT "Imputed value", or "IMF projection")."""

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


def empty_frame(with_note: bool = False) -> pd.DataFrame:
    columns = {"iso3": pd.Series(dtype="string"), "year": pd.Series(dtype="int64"),
               "value": pd.Series(dtype="float64")}
    if with_note:
        columns["note"] = pd.Series(dtype="string")
    return pd.DataFrame(columns)


def make_frame(records: list[tuple[str, int, float, str]]) -> pd.DataFrame:
    """Records of (iso3, year, value, note) -> a sorted frame with a note column."""
    if not records:
        return empty_frame(with_note=True)
    frame = pd.DataFrame(records, columns=["iso3", "year", "value", "note"]).astype(
        {"iso3": "string", "year": "int64", "value": "float64", "note": "string"})
    return frame.sort_values(["iso3", "year"], kind="stable").reset_index(drop=True)


def ambiguity(rows: list[dict[str, str]], dims: list[str], labels: dict[str, str] | None = None) -> str:
    """Describe which dimensions take more than one value among rows that share a
    country-year, so the error can tell the user which filter to add."""
    out = []
    for dim in dims:
        values = sorted({r.get(dim, "") for r in rows})
        if len(values) > 1:
            shown = ", ".join(f"{v} ({labels[v]})" if labels and labels.get(v) else v for v in values[:12])
            more = f", and {len(values) - 12} more" if len(values) > 12 else ""
            out.append(f"{dim}: {shown}{more}")
    return "; ".join(out)
