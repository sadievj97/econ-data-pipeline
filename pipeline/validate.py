"""Validation. Checks never change a value.

Errors stop the build: duplicate country-years, mixed units, values outside a
plausible range, known values that do not match, and any problem found while
fetching or harmonizing. Flags are listed for you to look at: differences
between sources above a threshold, jumps, runs of identical values, and known
events (currency regime changes, national accounts revisions) inside the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .config import Config
from .events import Event
from .harmonize import Panel, VariableResult, duplicate_keys, unit_of, year_ranges
from .known_values import KnownValue, KnownValueResult, same_value
from .numbers import fmt, plural
from .spec import Variable


@dataclass(frozen=True)
class Finding:
    level: str  # "error" stops the build; "flag" is something to look at
    check: str
    variable: str | None
    iso3: str | None
    message: str


@dataclass
class ComparisonResult:
    a: str
    b: str
    compare: str
    threshold: float
    origin: str  # "spec" or "fallback source"
    overlap: int = 0
    max_abs: float | None = None
    above: list[tuple[str, int, float]] = field(default_factory=list)  # (iso3, year, difference)


@dataclass
class ValidationReport:
    findings: list[Finding]
    comparisons: list[ComparisonResult]
    known: list[KnownValueResult]
    missing: pd.DataFrame  # variable, iso3, year for every missing value
    events: list[Event]  # known events inside the spec's countries and years
    settings: dict[str, Any]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def flags(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "flag"]


# ------------------------------------------------------------------ checks


def _growth(row: np.ndarray) -> np.ndarray:
    """Annual growth in percent; NaN where either year is missing or the earlier one is zero."""
    g = np.full(len(row), np.nan)
    prev, cur = row[:-1], row[1:]
    ok = ~np.isnan(prev) & ~np.isnan(cur) & (prev != 0)
    g[1:][ok] = (cur[ok] / prev[ok] - 1.0) * 100.0
    return g


@dataclass(frozen=True)
class RangeRule:
    """Allowed values: low <= x (or low < x when low_strict), and x <= high."""

    low: float | None
    high: float | None
    low_strict: bool
    text: str

    def breaks(self, x: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            bad = np.zeros(len(x), dtype=bool)
            if self.low is not None:
                bad |= (x <= self.low) if self.low_strict else (x < self.low)
            if self.high is not None:
                bad |= x > self.high
        return ~np.isnan(x) & bad


def range_rule(v: Variable, config: Config) -> RangeRule | None:
    """The range check for one variable. The R and Stata scripts apply the same rules."""
    if v.valid_range is not None:
        lo, hi = v.valid_range
        return RangeRule(lo, hi, False, f"the spec's valid_range [{lo:g}, {hi:g}]")
    if v.measure in ("share", "percent", "growth_rate"):
        lo, hi = getattr(config.checks.plausible_range, v.measure)
        return RangeRule(lo, hi, False, f"the plausible range for a {v.measure} [{lo:g}, {hi:g}] in config.yaml")
    if v.allow_negative:
        return None
    if v.measure == "index":
        return RangeRule(0.0, None, True, "an index must be above zero")
    return RangeRule(0.0, None, False, "a level must not be negative (set allow_negative: true if it can be)")


def check_ranges(panel: Panel, config: Config) -> list[Finding]:
    findings = []
    for name, r in panel.results.items():
        rule = range_rule(r.variable, config)
        if rule is None:
            continue
        for ci, c in enumerate(r.countries):
            row = r.values[ci]
            hits = rule.breaks(row)
            if hits.any():
                years = [r.years[i] for i in np.flatnonzero(hits)]
                lo_v, hi_v = float(np.nanmin(row[hits])), float(np.nanmax(row[hits]))
                findings.append(Finding("error", "range", name, c,
                                        f"{len(years)} value(s) break {rule.text}: years {year_ranges(years)} "
                                        f"(from {fmt(lo_v, r.precision)} to {fmt(hi_v, r.precision)})"))
    return findings


def _compare_rows(ra: np.ndarray, rb: np.ndarray, compare: str) -> np.ndarray:
    if compare == "growth":
        return _growth(ra) - _growth(rb)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = ~np.isnan(ra) & ~np.isnan(rb) & (rb != 0)
        diff = np.full(len(ra), np.nan)
        diff[ok] = (ra[ok] - rb[ok]) / np.abs(rb[ok]) * 100.0
    return diff


def _run_comparison(result: ComparisonResult, rows: dict[str, tuple[np.ndarray, np.ndarray]],
                    years: list[int]) -> ComparisonResult:
    diffs = []
    for c, (ra, rb) in sorted(rows.items()):
        d = _compare_rows(ra, rb, result.compare)
        for yi in np.flatnonzero(~np.isnan(d)):
            diffs.append(abs(d[yi]))
            if abs(d[yi]) > result.threshold:
                result.above.append((c, years[yi], float(d[yi])))
    result.overlap = len(diffs)
    result.max_abs = float(max(diffs)) if diffs else None
    return result


def check_comparisons(panel: Panel, config: Config) -> tuple[list[Finding], list[ComparisonResult]]:
    findings, results = [], []
    for c in panel.spec.comparisons:
        if c.a not in panel.results or c.b not in panel.results:
            continue  # the missing variable already has an error
        a, b = panel.results[c.a], panel.results[c.b]
        if c.compare == "level" and a.unit != b.unit:
            findings.append(Finding("error", "comparison", c.a, None,
                                    f"a level comparison with {c.b} needs identical units ({a.unit['unit']} vs {b.unit['unit']})"))
            continue
        rows = {iso3: (a.row(iso3), b.row(iso3)) for iso3 in panel.countries}
        results.append(_run_comparison(ComparisonResult(c.a, c.b, c.compare, c.threshold, "spec"), rows, panel.years))

    # Fallback sources describe the same concept: compare them with the source used.
    threshold = config.checks.comparison_threshold.growth
    for name, r in panel.results.items():
        v = r.variable
        for i, ref in enumerate(v.sources):
            other = panel.fetched.get((name, i))
            if other is None:
                continue
            rows = {}
            for c in panel.countries:
                used = r.source_for[c]
                if used is None or used == i:
                    continue
                orow = np.full(len(panel.years), np.nan)
                sub = other.data[(other.data["iso3"] == c) & other.data["year"].between(panel.years[0], panel.years[-1])]
                for year, value in zip(sub["year"].tolist(), sub["value"].tolist()):
                    orow[year - panel.years[0]] = value
                if (~np.isnan(orow)).any():
                    rows[c] = (r.row(c), orow)
            if rows:
                label = f"{name} [{ref.source} {ref.code}]"
                results.append(_run_comparison(ComparisonResult(name, label, "growth", threshold, "fallback source"),
                                               rows, panel.years))

    for res in results:
        by_country: dict[str, list[tuple[int, float]]] = {}
        for c, year, d in res.above:
            by_country.setdefault(c, []).append((year, d))
        limit = (plural(res.threshold, "percentage point") if res.compare == "growth"
                 else f"{res.threshold:g} percent")
        for c, items in by_country.items():
            detail = ", ".join(f"{y} ({d:+.2f})" for y, d in items)
            findings.append(Finding("flag", "comparison", res.a, c,
                                    f"{res.compare} differs from {res.b} by more than {limit} in: {detail}"))
    return findings, results


def _jump_rule(r: VariableResult, config: Config):
    j = config.checks.jump
    if r.variable.measure in ("level", "index"):
        def size(prev, cur):
            with np.errstate(invalid="ignore", divide="ignore"):
                out = np.full(len(cur), np.nan)
                ok = (prev > 0) & (cur > 0)
                out[ok] = np.log(cur[ok] / prev[ok])
            return out
        return size, j.level_log_change, "log change"
    if r.variable.measure == "percent":
        return (lambda prev, cur: cur - prev), j.percent_points, "change in percentage points"
    if r.variable.measure == "share":
        return (lambda prev, cur: cur - prev), j.share_points, "change"
    return None


def check_breaks(panel: Panel, config: Config, events: list[Event]) -> list[Finding]:
    findings = []
    events_by_country: dict[str, list[Event]] = {}
    for e in events:
        events_by_country.setdefault(e.iso3, []).append(e)
    run_length = config.checks.repeated_run
    for name, r in panel.results.items():
        rule = _jump_rule(r, config)
        for ci, c in enumerate(r.countries):
            row = r.values[ci]
            near = events_by_country.get(c, [])
            if rule is not None:
                size, limit, what = rule
                s = size(row[:-1], row[1:])
                for k in np.flatnonzero(~np.isnan(s) & (np.abs(s) > limit)):
                    year = r.years[k + 1]
                    hits = [e for e in near if e.date.year in (year - 1, year)]
                    context = "".join(f"; near known event {e.date.isoformat()}: {e.description}" for e in hits)
                    findings.append(Finding("flag", "jump", name, c,
                                            f"{r.years[k]}-{year}: {what} {s[k]:+.3f} (limit {limit:g}){context}"))
            y = 0
            while y < len(row):
                if np.isnan(row[y]):
                    y += 1
                    continue
                z = y
                while z + 1 < len(row) and row[z + 1] == row[y]:
                    z += 1
                if z - y + 1 >= run_length:
                    findings.append(Finding("flag", "repeated_values", name, c,
                                            f"the same value {fmt(row[y], r.precision)} in {z - y + 1} years in a row "
                                            f"({r.years[y]}-{r.years[z]}): possibly carried forward or filled in by the source"))
                y = z + 1
    return findings


def check_known_values(panel: Panel, known: list[KnownValue]) -> tuple[list[Finding], list[KnownValueResult]]:
    findings, results = [], []
    for kv in known:
        r = panel.results.get(kv.variable)
        if r is None or kv.iso3 not in r.countries or kv.year not in r.years:
            res = KnownValueResult(kv, None, False, "not in the panel")
        else:
            actual = float(r.values[r.countries.index(kv.iso3), r.years.index(kv.year)])
            if np.isnan(actual):
                res = KnownValueResult(kv, None, False, "missing in the panel")
            elif same_value(kv.value, actual, r.precision):
                note = "matches at the source's 4-byte precision" if r.precision == "float32" else "matches exactly"
                res = KnownValueResult(kv, actual, True, note)
            else:
                res = KnownValueResult(kv, actual, False, f"differs by {fmt(actual - kv.value)}")
        results.append(res)
        if not res.matched:
            findings.append(Finding("error", "known_value", kv.variable, kv.iso3,
                                    f"{kv.year}: expected {fmt(kv.value)} ({kv.source}); {res.note}"
                                    + (f", panel has {fmt(res.actual)}" if res.actual is not None else "")))
    return findings, results


def missing_data(panel: Panel) -> pd.DataFrame:
    long = panel.long()
    gaps = long[long["value"].isna()][["variable", "iso3", "year"]]
    return gaps.reset_index(drop=True)


def validate(panel: Panel, config: Config, events: list[Event], known: list[KnownValue],
             extra: list[Finding] | None = None) -> ValidationReport:
    findings = list(extra or [])
    findings += [Finding("error", p.check, p.variable, None, p.message) for p in panel.problems]
    long = panel.long()
    for key in duplicate_keys(long, ("variable", "iso3", "year")):
        findings.append(Finding("error", "duplicate_keys", key.split()[0], None, f"more than one row for {key}"))
    for name, r in panel.results.items():
        units = {tuple(unit_of(r.variable.sources[i]).values()) for i in set(r.source_for.values()) if i is not None}
        if len(units) > 1 and not any(p.check == "unit_mixing" and p.variable == name for p in panel.problems):
            findings.append(Finding("error", "unit_mixing", name, None, "values come from sources in different units"))
    findings += check_ranges(panel, config)
    comparison_findings, comparisons = check_comparisons(panel, config)
    findings += comparison_findings
    findings += check_breaks(panel, config, events)
    known_findings, known_results = check_known_values(panel, known)
    findings += known_findings
    in_scope = [e for e in events if e.iso3 in panel.countries and panel.years[0] <= e.date.year <= panel.years[-1]]
    settings = {
        "comparison_threshold": config.checks.comparison_threshold.model_dump(),
        "plausible_range": config.checks.plausible_range.model_dump(),
        "jump": config.checks.jump.model_dump(),
        "repeated_run": config.checks.repeated_run,
    }
    return ValidationReport(findings, comparisons, known_results, missing_data(panel), in_scope, settings)
