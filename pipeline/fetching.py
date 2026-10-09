"""Fetch every series a spec lists, and check each against the spec's expectations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .cache import CacheIntegrityError
from .connectors import FetchedSeries, NotAvailableYet, SourceError, Sources
from .net import DownloadError
from .spec import SourceRef, Spec
from .units import compare


@dataclass
class FetchReport:
    variable: str
    ref: SourceRef
    series: FetchedSeries | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: str | None = None


@dataclass(frozen=True)
class Coverage:
    with_values: int
    expected: int
    missing: dict[str, list[int]]  # country -> years in the spec's range with no value


def coverage(series: FetchedSeries, spec: Spec) -> Coverage:
    years = range(spec.years.start, spec.years.end + 1)
    data = series.data
    in_range = data[(data["year"] >= spec.years.start) & (data["year"] <= spec.years.end) & data["value"].notna()]
    missing = {}
    for iso3 in spec.countries:
        have = set(in_range.loc[in_range["iso3"] == iso3, "year"].tolist())
        gaps = [y for y in years if y not in have]
        if gaps:
            missing[iso3] = gaps
    return Coverage(len(in_range), len(spec.countries) * len(years), missing)


def fetch_spec(spec: Spec, sources: Sources, refresh: bool = False,
               progress: Callable[[str], None] = lambda _: None) -> list[FetchReport]:
    reports = []
    for variable in spec.variables:
        for ref in variable.sources:
            report = FetchReport(variable.name, ref)
            progress(f"  {variable.name}: {ref.source} {ref.code}")
            try:
                report.series = sources.fetch(ref, spec.countries, refresh)
            except NotAvailableYet as exc:
                report.skipped = str(exc)
            except (SourceError, DownloadError, CacheIntegrityError) as exc:
                report.errors.append(str(exc))
            else:
                report.errors, report.warnings = compare(ref, report.series.info.unit)
            reports.append(report)
    return reports
