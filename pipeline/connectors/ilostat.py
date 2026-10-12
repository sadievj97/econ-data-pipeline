"""ILOSTAT, the ILO's labour statistics, via its public API (no key needed).

Catalog: https://rplumber.ilo.org/metadata/toc/indicator/?lang=en&format=.csv
Data:    https://rplumber.ilo.org/data/indicator/?id=<id>&ref_area=<ISO3>&format=.csv&type=both

One raw file per indicator and country, with every breakdown and year. A spec
picks one series with filters, for example {sex: SEX_T, classif1: ECO_SECTOR_AGR}.
If a country-year still has several values (for example from two surveys), the
fetch stops and lists the choices instead of picking one.
"""

from __future__ import annotations

import csv
import io
import math
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

import httpx

from .. import net
from ..cache import RawCache, Snapshot
from ..spec import SourceRef
from ..units import from_ilo_label
from .base import FetchedSeries, IndicatorInfo, SourceError, UnknownCode, ambiguity, make_frame

BASE_URL = "https://rplumber.ilo.org"
CATALOG_KEY = "ilostat/catalog"
FILTER_DIMS = ("source", "sex", "classif1", "classif2")


def _read_csv(snap: Snapshot) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(snap.read_bytes().decode("utf-8-sig"))))


def _check_csv(content: bytes, required: tuple[str, ...], what: str, allow_empty: bool) -> None:
    text = content.decode("utf-8-sig", errors="replace").strip()
    if not text:
        if allow_empty:
            return  # no data for this country is a valid answer
        raise SourceError(f"ILOSTAT sent an empty reply for {what}")
    header = next(csv.reader(io.StringIO(text.splitlines()[0])), [])
    if not all(col in header for col in required):
        raise SourceError(f"ILOSTAT sent an unexpected reply for {what}: {text[:200]!r}")


class ILOSTAT:
    name = "ilostat"

    def __init__(self, cache: RawCache, client: httpx.Client, attempts: int = 4,
                 sleep: Callable[[float], None] = time.sleep, **_: Any):
        self.cache = cache
        self.client = client
        self.attempts = attempts
        self.sleep = sleep
        self._catalog: dict[str, IndicatorInfo] | None = None
        self._catalog_snapshot: Snapshot | None = None
        self._catalog_refreshed = False

    def _snapshot(self, key: str, filename: str, url: str, params: dict[str, str], what: str,
                  required: tuple[str, ...], refresh: bool, allow_empty: bool = True) -> Snapshot:
        if not refresh:
            cached = self.cache.latest(key)
            # An empty catalog is never valid; skip such a snapshot rather than use it.
            if cached is not None and (allow_empty or cached.meta.get("bytes", 0) > 0):
                return cached
        response = net.get(self.client, url, params, attempts=self.attempts, sleep=self.sleep)
        _check_csv(response.content, required, what, allow_empty)  # never cache an error reply
        meta = {"source": self.name, "url": url, "params": params, "http_status": response.status_code}
        return self.cache.store(key, filename, response.content, meta)

    def catalog(self, refresh: bool = False) -> dict[str, IndicatorInfo]:
        if self._catalog is not None and (not refresh or self._catalog_refreshed):
            return self._catalog
        snap = self._snapshot(CATALOG_KEY, "toc.csv", f"{BASE_URL}/metadata/toc/indicator/",
                              {"lang": "en", "format": ".csv"}, "the ILOSTAT indicator list",
                              ("id", "indicator.label"), refresh, allow_empty=False)
        entries = {}
        for row in _read_csv(snap):
            if row.get("freq") != "A":
                continue  # annual series only
            label = row["indicator.label"].strip()
            definition = (f"{label}. Breakdowns: {row.get('classif.labels') or 'none'}. "
                          f"Database: {row.get('database.label') or row.get('database')}. "
                          f"Data {row.get('data.start')}-{row.get('data.end')}, last updated {row.get('last.update')}.")
            entries[row["id"]] = IndicatorInfo(self.name, row["id"], label, definition, from_ilo_label(label),
                                               {"database": row.get("database"), "breakdowns": row.get("classif.labels")})
        self._catalog = dict(sorted(entries.items()))
        self._catalog_snapshot = snap
        self._catalog_refreshed = self._catalog_refreshed or refresh
        return self._catalog

    def info(self, code: str, dataset: str | None = None, filters: dict | None = None) -> IndicatorInfo:
        catalog = self.catalog()
        if code not in catalog:
            raise UnknownCode(f"'{code}' is not an annual ILOSTAT indicator (annual ids end in _A)")
        return catalog[code]

    def _rows(self, code: str, iso3: str, refresh: bool) -> tuple[list[dict[str, str]], Snapshot]:
        snap = self._snapshot(f"ilostat/data/{code}/{iso3}", "data.csv", f"{BASE_URL}/data/indicator/",
                              {"id": code, "ref_area": iso3, "format": ".csv", "type": "both"},
                              f"{code} for {iso3}", ("ref_area", "time", "obs_value"), refresh)
        return _read_csv(snap), snap

    def describe(self, code: str, dataset: str | None, iso3: str) -> dict[str, Any]:
        """The filter choices this indicator offers for one country, with labels."""
        self.info(code)
        rows, _ = self._rows(code, iso3, False)
        options: dict[str, dict[str, str]] = {}
        for dim in FILTER_DIMS:
            values = {r[dim]: r.get(f"{dim}.label", "") for r in rows if r.get(dim)}
            if values:
                options[dim] = dict(sorted(values.items()))
        years = sorted({r["time"] for r in rows})
        return {"country": iso3, "years": f"{years[0]}-{years[-1]}" if years else "none", "filters": options}

    def fetch(self, ref: SourceRef, countries: Iterable[str], refresh: bool = False) -> FetchedSeries:
        if ref.dataset:
            raise SourceError("ILOSTAT series take no dataset; remove it from the spec")
        unknown = sorted(set(ref.filters) - set(FILTER_DIMS))
        if unknown:
            raise SourceError(f"ILOSTAT filters can be {', '.join(FILTER_DIMS)}; not {', '.join(unknown)}")
        if refresh:
            self.catalog(refresh=True)
        info = self.info(ref.code)
        records, snaps, seen, matched, warnings = [], [], [], 0, []
        for iso3 in countries:
            rows, snap = self._rows(ref.code, iso3, refresh)
            snaps.append(snap)
            seen += rows
            chosen = [r for r in rows if all(r.get(k) == v for k, v in ref.filters.items())]
            matched += len(chosen)
            surveys = {r.get("source", ""): r.get("source.label", "") for r in chosen}
            mixed = len(surveys) > 1
            if mixed:
                warnings.append(f"{iso3}: the series combines {len(surveys)} different ILO sources over the years ("
                                + "; ".join(f"{k} {v}".strip() for k, v in sorted(surveys.items()))
                                + "); each value's source is in its note. Add filters: {source: ...} to use one")
            by_year: dict[str, list[dict[str, str]]] = defaultdict(list)
            for r in chosen:
                by_year[r["time"]].append(r)
            for year, group in sorted(by_year.items()):
                if len(group) > 1:
                    labels = {r.get(d, ""): r.get(f"{d}.label", "") for r in group for d in FILTER_DIMS}
                    raise SourceError(f"ILOSTAT {ref.code} has {len(group)} values for {iso3} {year} with these filters; "
                                      f"add a filter to pick one. Choices: {ambiguity(group, list(FILTER_DIMS), labels)}")
                if not re.fullmatch(r"\d{4}", year):
                    raise SourceError(f"{snap.relative_file()}: '{year}' is not an annual date")
                r = group[0]
                raw = (r.get("obs_value") or "").strip()
                value = float(raw) if raw not in ("", "NA") else math.nan
                note = (r.get("obs_status.label") or r.get("obs_status") or "").strip()
                if mixed:
                    survey = f"source {r.get('source', '')} {r.get('source.label', '')}".strip()
                    note = f"{note}; {survey}" if note else survey
                records.append((iso3, int(year), value, note))
        if seen and not matched:
            labels = {r.get(d, ""): r.get(f"{d}.label", "") for r in seen for d in FILTER_DIMS}
            raise SourceError(f"No ILOSTAT {ref.code} values match the filters {ref.filters}. "
                              f"Choices: {ambiguity(seen, list(FILTER_DIMS), labels)}")
        assert self._catalog_snapshot is not None
        if warnings:
            info = IndicatorInfo(info.source, info.code, info.name, info.definition, info.unit,
                                 dict(info.details, warnings=warnings))
        return FetchedSeries(info, make_frame(records), snaps, self._catalog_snapshot)
