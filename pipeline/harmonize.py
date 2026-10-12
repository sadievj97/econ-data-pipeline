"""Harmonization: one series per spec variable, for every spec country and year.

For each variable (proxies and rebasing partners first):
  1. keep only the spec's countries and years, counting what is left out;
  2. choose a source as the spec's source_choice says, never mixing sources
     within one country's series except through an explicit splice;
  3. apply the spec's transforms in the order listed, flagging every value
     they create.
Nothing else changes a value, and every step is written to the data log.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .connectors import FetchedSeries
from .datalog import DataLog
from .spec import Extrapolate, Interpolate, Rebase, Scale, SourceRef, Spec, Splice, Variable

# Scale factors relative to the smallest unit of each family.
SCALE_FACTORS = {"units": 1, "thousands": 10**3, "millions": 10**6, "billions": 10**9, "percent": 1, "fraction": 100}


@dataclass(frozen=True)
class Problem:
    """Something that stops the build."""

    check: str
    variable: str | None
    message: str


@dataclass
class VariableResult:
    variable: Variable
    countries: list[str]
    years: list[int]
    values: np.ndarray  # shape (countries, years); NaN means missing
    flags: list[list[str]]  # same shape; "" means the value is exactly as the source published it
    source_for: dict[str, int | None]  # country -> index into variable.sources (None: no source has data)
    unit: dict[str, Any]  # unit, currency, price_basis, base_year of the final series
    precision: str  # "float32" when values are a source's 4-byte floats, otherwise "float64"
    transforms: list[str] = field(default_factory=list)  # plain-language record of each transform applied

    def row(self, iso3: str) -> np.ndarray:
        return self.values[self.countries.index(iso3)]

    def frame(self) -> pd.DataFrame:
        rows = [(c, y, self.values[ci, yi], self.flags[ci][yi])
                for ci, c in enumerate(self.countries) for yi, y in enumerate(self.years)]
        return pd.DataFrame(rows, columns=["iso3", "year", "value", "flag"]).astype(
            {"iso3": "string", "year": "int64", "value": "float64", "flag": "string"})


@dataclass
class Panel:
    spec: Spec
    countries: list[str]
    years: list[int]
    results: dict[str, VariableResult]  # in spec order; only variables that could be built
    fetched: dict[tuple[str, int], FetchedSeries]  # (variable, source index) -> series as fetched
    problems: list[Problem]
    log: DataLog

    def long(self) -> pd.DataFrame:
        frames = []
        for name, result in self.results.items():
            f = result.frame()
            f.insert(0, "variable", name)
            frames.append(f)
        if not frames:
            return pd.DataFrame(columns=["variable", "iso3", "year", "value", "flag"])
        out = pd.concat(frames, ignore_index=True)
        out["variable"] = out["variable"].astype("string")
        return out


# ------------------------------------------------------------------ helpers


def _span(first: int, last: int) -> str:
    return str(first) if first == last else f"{first}-{last}"


def year_ranges(years: list[int]) -> str:
    parts, start = [], None
    for i, y in enumerate(years):
        if start is None:
            start = y
        if i + 1 == len(years) or years[i + 1] != y + 1:
            parts.append(_span(start, y))
            start = None
    return ", ".join(parts)


def _add_flag(existing: str, new: str) -> str:
    return new if not existing else f"{existing}; {new}"


def source_label(i: int, ref: SourceRef) -> str:
    role = "preferred source" if i == 0 else f"fallback {i}"
    return f"{role} {ref.source} {ref.code}"


def unit_of(ref: SourceRef) -> dict[str, Any]:
    return {"unit": ref.unit, "currency": ref.currency, "price_basis": ref.price_basis, "base_year": ref.base_year}


def duplicate_keys(data: pd.DataFrame, keys: tuple[str, ...] = ("iso3", "year")) -> list[str]:
    dup = data[data.duplicated(list(keys), keep=False)]
    return sorted({" ".join(str(getattr(r, k)) for k in keys) for r in dup.itertuples(index=False)})


def _dependencies(v: Variable) -> list[str]:
    names = []
    for t in v.transforms:
        for ref in (getattr(t, "proxy", None), getattr(t, "current_price_variable", None)):
            if ref:
                names.append(ref)
    return names


def _dependency_order(spec: Spec) -> tuple[list[Variable], list[str]]:
    """Spec order, except that a variable comes after the variables its transforms use."""
    remaining = list(spec.variables)
    done: set[str] = set()
    order: list[Variable] = []
    while remaining:
        ready = [v for v in remaining if all(d in done for d in _dependencies(v))]
        if not ready:
            return order, [v.name for v in remaining]
        first = ready[0]
        order.append(first)
        done.add(first.name)
        remaining.remove(first)
    return order, []


# ------------------------------------------------------------- harmonize


def harmonize(spec: Spec, fetched: dict[tuple[str, int], FetchedSeries], log: DataLog) -> Panel:
    countries = sorted(spec.countries)
    years = list(range(spec.years.start, spec.years.end + 1))
    problems: list[Problem] = []
    log.add("Scope", f"countries: {', '.join(countries)}", f"years: {years[0]}-{years[-1]} ({len(years)} years)",
            f"variables: {len(spec.variables)}, in the order listed in the spec")

    order, looped = _dependency_order(spec)
    for name in looped:
        problems.append(Problem("transforms", name, "its transforms refer to other variables in a loop"))

    built: dict[str, VariableResult] = {}
    for v in order:
        missing = [d for d in _dependencies(v) if d not in built]
        if missing:
            problems.append(Problem("transforms", v.name, f"needs {', '.join(missing)}, which could not be built"))
            continue
        result = _build_variable(v, spec, fetched, countries, years, built, problems, log)
        if result is not None:
            built[v.name] = result

    results = {v.name: built[v.name] for v in spec.variables if v.name in built}
    return Panel(spec, countries, years, results, fetched, problems, log)


def _build_variable(v: Variable, spec: Spec, fetched: dict[tuple[str, int], FetchedSeries], countries: list[str],
                    years: list[int], built: dict[str, VariableResult], problems: list[Problem],
                    log: DataLog) -> VariableResult | None:
    start, end = years[0], years[-1]
    lines: list[str] = []
    available: dict[int, pd.DataFrame] = {}
    for i, ref in enumerate(v.sources):
        series = fetched.get((v.name, i))
        label = source_label(i, ref)
        if series is None:
            lines.append(f"{label}: not available")
            continue
        dups = duplicate_keys(series.data)
        if dups:
            problems.append(Problem("duplicate_keys", v.name,
                                    f"{ref.source} {ref.code} has more than one value for: {', '.join(dups)}"))
            return None
        data = series.data
        keep = data["iso3"].isin(countries) & data["year"].between(start, end)
        left_out = data[~keep]
        if len(left_out):
            lines.append(f"{label}: left out {len(left_out)} observations outside {start}-{end} "
                         f"({int(left_out['value'].notna().sum())} of them had values)")
        available[i] = data[keep]
    covered = {i: set(df.loc[df["value"].notna(), "iso3"]) for i, df in available.items()}

    splice = next((t for t in v.transforms if isinstance(t, Splice)), None)
    if splice is not None:
        pick = 0 if 0 in available else None
        source_for: dict[str, int | None] = {c: pick for c in countries}
        lines.append("source: the preferred source for every country, because the spec splices it with another")
    elif spec.source_choice == "same_for_all":
        full = [i for i in sorted(covered) if covered[i] >= set(countries)]
        some = [i for i in sorted(covered) if covered[i]]
        pick = full[0] if full else (some[0] if some else None)
        source_for = {c: pick for c in countries}
        if pick is None:
            lines.append(f"source: no source has any value in {start}-{end}")
        elif full:
            lines.append(f"source: {source_label(pick, v.sources[pick])} for every country"
                         + ("" if pick == 0 else "; earlier sources lack data for some countries"))
        else:
            lacking = sorted(set(countries) - covered[pick])
            lines.append(f"source: no source has values for every country; using {source_label(pick, v.sources[pick])}, "
                         f"the first with any; no values for {', '.join(lacking)}")
    else:
        source_for = {c: next((i for i in sorted(covered) if c in covered[i]), None) for c in countries}
        for c in countries:
            i = source_for[c]
            lines.append(f"source for {c}: " + ("none has values" if i is None else source_label(i, v.sources[i])))

    used = sorted({i for i in source_for.values() if i is not None})
    unit_sets = {tuple(unit_of(v.sources[i]).values()) for i in used}
    if len(unit_sets) > 1:
        problems.append(Problem("unit_mixing", v.name, "countries would come from sources in different units: "
                                + "; ".join(f"{v.sources[i].source} {v.sources[i].code} in {v.sources[i].unit}" for i in used)))

    values = np.full((len(countries), len(years)), np.nan)
    flags = [["" for _ in years] for _ in countries]
    for ci, c in enumerate(countries):
        i = source_for[c]
        if i is None:
            continue
        sub = available[i][available[i]["iso3"] == c]
        for year, value in zip(sub["year"].tolist(), sub["value"].tolist()):
            values[ci, year - start] = value

    first = used[0] if used else 0
    first_series = fetched.get((v.name, first))
    storage = first_series.info.details.get("storage_type") if first_series is not None else None
    result = VariableResult(v, countries, years, values, flags, source_for, unit_of(v.sources[first]),
                            "float32" if storage == "float" else "float64")
    if result.precision == "float32":
        lines.append("precision: the source stores these values as 4-byte floats (about 7 significant digits)")
    log.add("Source and years", *lines, variable=v.name)

    for t in v.transforms:
        before = problems.copy()
        if isinstance(t, Interpolate):
            tlines = _interpolate(t, result, v, problems)
        elif isinstance(t, Extrapolate):
            tlines = _extrapolate(t, result, built[t.proxy], v, problems)
        elif isinstance(t, Rebase):
            tlines = _rebase(t, result, built.get(t.current_price_variable or ""), v, problems)
        elif isinstance(t, Splice):
            tlines = _splice(t, result, available, v, problems)
        else:
            tlines = _scale(t, result)
        result.precision = "float64"  # computed values are full doubles
        description = tlines[0]
        result.transforms.append(description)
        note = [f"note: {t.note}"] if t.note else []
        status = [] if problems == before else ["PROBLEM: see the validation report"]
        log.add(f"Transform: {t.kind}", *tlines, *note, *status, variable=v.name)

    n_missing = int(np.isnan(result.values).sum())
    log.add("Result", f"{result.values.size - n_missing} of {result.values.size} country-years have values; "
            f"{n_missing} missing", variable=v.name)
    return result


# ------------------------------------------------------------ transforms


def _interpolate(t: Interpolate, r: VariableResult, v: Variable, problems: list[Problem]) -> list[str]:
    lines = [f"{t.method} interpolation of interior gaps of at most {t.max_gap} years"]
    flag = f"interpolated ({t.method})"
    for ci, c in enumerate(r.countries):
        row = r.values[ci]
        present = ~np.isnan(row)
        filled, too_long = [], []
        y, n = 0, len(row)
        while y < n:
            if present[y]:
                y += 1
                continue
            g = y
            while g < n and not present[g]:
                g += 1
            if y > 0 and g < n:
                gap, a, b = g - y, row[y - 1], row[g]
                span = _span(r.years[y], r.years[g - 1])
                if gap > t.max_gap:
                    too_long.append(span)
                elif t.method == "log_linear" and (a <= 0 or b <= 0):
                    problems.append(Problem("transforms", v.name,
                                            f"{c}: log-linear interpolation of {span} needs positive values on both sides"))
                else:
                    for k in range(1, gap + 1):
                        w = k / (gap + 1)
                        row[y + k - 1] = (a + (b - a) * w if t.method == "linear"
                                          else math.exp(math.log(a) + (math.log(b) - math.log(a)) * w))
                        r.flags[ci][y + k - 1] = _add_flag(r.flags[ci][y + k - 1], flag)
                    filled.append(span)
            y = g
        if filled:
            lines.append(f"{c}: filled {', '.join(filled)}")
        if too_long:
            lines.append(f"{c}: left unfilled (longer than {t.max_gap} years): {', '.join(too_long)}")
        if not filled and not too_long:
            lines.append(f"{c}: no interior gaps")
    return lines


def _extrapolate(t: Extrapolate, r: VariableResult, proxy: VariableResult, v: Variable,
                 problems: list[Problem]) -> list[str]:
    lines = [f"extended {t.direction} with the year-on-year growth of {t.proxy}"]
    flag = f"extrapolated (growth of {t.proxy})"
    n = len(r.years)
    for ci, c in enumerate(r.countries):
        row, prow = r.values[ci], proxy.row(c)
        present = np.flatnonzero(~np.isnan(row))
        if len(present) == 0:
            lines.append(f"{c}: no values to extend from")
            continue
        made = []
        if t.direction in ("backward", "both"):
            k = present[0] - 1
            while k >= 0 and not np.isnan(prow[k]) and not np.isnan(prow[k + 1]) and prow[k + 1] != 0:
                row[k] = row[k + 1] * prow[k] / prow[k + 1]
                r.flags[ci][k] = _add_flag(r.flags[ci][k], flag)
                made.append(r.years[k])
                k -= 1
        if t.direction in ("forward", "both"):
            k = present[-1] + 1
            while k < n and not np.isnan(prow[k]) and not np.isnan(prow[k - 1]) and prow[k - 1] != 0:
                row[k] = row[k - 1] * prow[k] / prow[k - 1]
                r.flags[ci][k] = _add_flag(r.flags[ci][k], flag)
                made.append(r.years[k])
                k += 1
        lines.append(f"{c}: " + (f"added {year_ranges(sorted(made))}" if made else "nothing to add"))
        if any(proxy.flags[proxy.countries.index(c)][r.years.index(y)] for y in made):
            lines.append(f"{c}: some proxy values used were themselves filled in by a transform")
    return lines


def _rebase(t: Rebase, r: VariableResult, partner: VariableResult | None, v: Variable,
            problems: list[Problem]) -> list[str]:
    if t.year not in r.years:
        problems.append(Problem("transforms", v.name, f"rebase year {t.year} is outside the spec's years"))
        return [f"rebase to {t.year} not done"]
    yi = r.years.index(t.year)
    old_unit = r.unit["unit"]
    if t.method == "index":
        lines = [f"rebased to an index, {t.year}=100 (value / value in {t.year} x 100)"]
        for ci, c in enumerate(r.countries):
            base = r.values[ci, yi]
            if np.isnan(base) or base == 0:
                problems.append(Problem("transforms", v.name, f"{c}: no non-zero value in {t.year} to rebase on"))
                continue
            r.values[ci] = r.values[ci] / base * 100
        r.unit = {"unit": f"index, {t.year}=100 (from {old_unit})", "currency": None,
                  "price_basis": r.unit["price_basis"], "base_year": t.year}
        return lines

    assert partner is not None
    lines = [f"re-referenced to {t.year} prices: multiplied by {t.current_price_variable} / {v.name} in {t.year}"]
    if partner.unit["price_basis"] != "current" or partner.unit["currency"] != r.unit["currency"]:
        problems.append(Problem("transforms", v.name, f"{t.current_price_variable} must be at current prices in "
                                                      f"the same currency ({r.unit['currency']})"))
        return lines
    for ci, c in enumerate(r.countries):
        x_b, c_b = r.values[ci, yi], partner.row(c)[yi]
        if np.isnan(x_b) or np.isnan(c_b) or x_b == 0:
            problems.append(Problem("transforms", v.name, f"{c}: needs both series in {t.year} to re-reference"))
            continue
        factor = c_b / x_b
        r.values[ci] = r.values[ci] * factor
        lines.append(f"{c}: factor {factor!r}")
    r.unit = dict(r.unit, unit=f"{old_unit}, re-referenced to {t.year} prices", base_year=t.year)
    return lines


def _splice(t: Splice, r: VariableResult, available: dict[int, pd.DataFrame], v: Variable,
            problems: list[Problem]) -> list[str]:
    other = next(i for i, s in enumerate(v.sources) if s.source == t.with_source)
    ref = v.sources[other]
    lines = [f"years before {t.link_year} taken from {ref.source} {ref.code}, scaled by the ratio of the two "
             f"sources in {t.link_year}"]
    if t.link_year not in r.years:
        problems.append(Problem("transforms", v.name, f"splice link year {t.link_year} is outside the spec's years"))
        return lines
    if other not in available:
        problems.append(Problem("transforms", v.name, f"splice source {ref.source} {ref.code} has no data"))
        return lines
    li = r.years.index(t.link_year)
    flag = f"spliced ({ref.source} {ref.code}, ratio at {t.link_year})"
    data = available[other]
    for ci, c in enumerate(r.countries):
        orow = np.full(len(r.years), np.nan)
        sub = data[data["iso3"] == c]
        for year, value in zip(sub["year"].tolist(), sub["value"].tolist()):
            orow[year - r.years[0]] = value
        p_l, o_l = r.values[ci, li], orow[li]
        if np.isnan(p_l) or np.isnan(o_l) or o_l == 0:
            problems.append(Problem("transforms", v.name, f"{c}: splice needs both sources in {t.link_year}"))
            continue
        ratio = p_l / o_l
        replaced = dropped = added = 0
        for k in range(li):
            had = not np.isnan(r.values[ci, k])
            if np.isnan(orow[k]):
                if had:
                    dropped += 1
                r.values[ci, k] = np.nan
                r.flags[ci][k] = ""
                continue
            replaced += had
            added += not had
            r.values[ci, k] = orow[k] * ratio
            r.flags[ci][k] = _add_flag(r.flags[ci][k], flag)
        lines.append(f"{c}: ratio {ratio!r}; {replaced} preferred-source values replaced, {added} added, "
                     f"{dropped} removed because {ref.source} has no value there")
    return lines


def _scale(t: Scale, r: VariableResult) -> list[str]:
    f_from, f_to = SCALE_FACTORS[t.from_unit], SCALE_FACTORS[t.to_unit]
    if f_from >= f_to:
        factor = f_from // f_to
        r.values = r.values * factor
        how = f"multiplied by {factor}"
    else:
        divisor = f_to // f_from
        r.values = r.values / divisor
        how = f"divided by {divisor}"
    r.unit = dict(r.unit, unit=f"{r.unit['unit']} (rescaled from {t.from_unit} to {t.to_unit})")
    return [f"rescaled from {t.from_unit} to {t.to_unit}: every value {how}"]
