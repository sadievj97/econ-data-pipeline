"""Statistics Agency of Uzbekistan (SIAT, siat.stat.uz), via its open download service (no key).

Catalog: https://api.siat.stat.uz/sdmx/  (a tree of 3,000+ indicators)
Data:    https://api.siat.stat.uz/sdmx/<id>/table/download/?download_format=json
         answers with a link to the table file, which is what gets cached.

Only annual tables are read: quarterly tables are year-to-date running totals,
and turning them into annual figures would be a transformation. A table's rows
are regions or kinds of activity; a spec picks one with filters: {row: <Code>}.
Without a filter, a one-row table is used as it is, and otherwise the row for
the whole republic (code 1700) if the table has one.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Iterable
from typing import Any

import httpx

from .. import net
from ..cache import RawCache, Snapshot
from ..spec import SourceRef
from ..units import UnitInfo, from_siat_unit
from .base import FetchedSeries, IndicatorInfo, SourceError, UnknownCode, empty_frame, make_frame

API = "https://api.siat.stat.uz"
CATALOG_KEY = "nso_uzb/catalog"
COUNTRY = "UZB"
REPUBLIC_ROW = "1700"


def _leaves(node: dict, path: list[str], out: list[tuple[dict, list[str]]]) -> None:
    children = node.get("children") or []
    if not children:
        out.append((node, path))
    for child in children:
        _leaves(child, path + [node.get("name_en") or node.get("name") or ""], out)


def _number(value: Any) -> float:
    if value is None:
        return math.nan
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(",", ".")
    if text in ("", "-", "...", "…", "x", "X"):
        return math.nan
    try:
        return float(text)
    except ValueError:
        raise SourceError(f"SIAT value {value!r} is not a number") from None


class SIAT:
    name = "nso_uzb"

    def __init__(self, cache: RawCache, client: httpx.Client, attempts: int = 4,
                 sleep: Callable[[float], None] = time.sleep, **_: Any):
        self.cache = cache
        self.client = client
        self.attempts = attempts
        self.sleep = sleep
        self._catalog: dict[str, IndicatorInfo] | None = None
        self._catalog_snapshot: Snapshot | None = None
        self._tables: dict[str, tuple[dict, Snapshot]] = {}
        self._refreshed: set[str] = set()

    def catalog(self, refresh: bool = False) -> dict[str, IndicatorInfo]:
        if self._catalog is not None and (not refresh or "__catalog__" in self._refreshed):
            return self._catalog
        snap = None if refresh else self.cache.latest(CATALOG_KEY)
        if snap is None:
            response = net.get(self.client, f"{API}/sdmx/", attempts=self.attempts, sleep=self.sleep)
            try:
                json.loads(response.content)["results"]
            except (ValueError, KeyError, TypeError):
                raise SourceError("The SIAT indicator list is not in the expected format") from None
            snap = self.cache.store(CATALOG_KEY, "catalog.json", response.content,
                                    {"source": self.name, "url": f"{API}/sdmx/"})
        leaves: list[tuple[dict, list[str]]] = []
        for top in json.loads(snap.read_bytes())["results"]:
            _leaves(top, [], leaves)
        entries = {}
        for node, path in leaves:
            code = str(node["id"])
            name = (node.get("name_en") or node.get("name") or "").strip()
            entries[code] = IndicatorInfo(self.name, code, name,
                                          f"{' > '.join(p for p in path if p)} > {name} (SIAT code {node.get('code')})",
                                          UnitInfo("", None, None, None),
                                          {"siat_code": node.get("code"), "updated": node.get("updated_at")})
        self._catalog = dict(sorted(entries.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0))
        self._catalog_snapshot = snap
        if refresh:
            self._refreshed.add("__catalog__")
        return self._catalog

    def table(self, code: str, refresh: bool = False) -> tuple[dict, Snapshot]:
        if code in self._tables and (not refresh or code in self._refreshed):
            return self._tables[code]
        if not code.isdigit():
            raise UnknownCode(f"'{code}' is not a SIAT indicator id (a number such as 1209)")
        key = f"nso_uzb/data/{code}"
        snap = None if refresh else self.cache.latest(key)
        if snap is None:
            pointer_url = f"{API}/sdmx/{code}/table/download/"
            pointer = net.get(self.client, pointer_url, {"download_format": "json"}, attempts=self.attempts,
                              sleep=self.sleep).json()
            file_url = pointer.get("file") if isinstance(pointer, dict) else None
            if not file_url:
                raise UnknownCode(f"SIAT has no downloadable table for indicator {code}")
            content = net.get(self.client, file_url, attempts=self.attempts, sleep=self.sleep).content
            try:
                item = json.loads(content)[0]
                item["metadata"], item["data"]
            except (ValueError, KeyError, IndexError, TypeError):
                raise SourceError(f"SIAT's table for indicator {code} is not in the expected format") from None
            snap = self.cache.store(key, "table.json", content,
                                    {"source": self.name, "url": file_url, "pointer_url": pointer_url,
                                     "siat_updated_at": pointer.get("updated_at")})
        item = json.loads(snap.read_bytes())[0]
        self._tables[code] = (item, snap)
        if refresh:
            self._refreshed.add(code)
        return item, snap

    @staticmethod
    def _meta(item: dict) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in item["metadata"]:
            name, value = m.get("name_en") or "", (m.get("value_en") or "").strip()
            if value:
                out[name] = f"{out[name]} {value}" if name in out else value
        return out

    def info(self, code: str, dataset: str | None = None, filters: dict | None = None) -> IndicatorInfo:
        item, _ = self.table(code)
        meta = self._meta(item)
        unit_text = meta.get("Unit of measurement", "")
        unit, warnings = from_siat_unit(unit_text)
        if unit.price_basis is None and not warnings:
            # Some tables give the price basis only in the indicator name, e.g. "(at current prices, annual)".
            from_name, _ = from_siat_unit(meta.get("Indicator name", ""))
            if from_name.price_basis in ("current", "constant"):
                unit = UnitInfo(unit.text, from_name.price_basis, unit.base_year, unit.currency)
        definition = " ".join(x for x in (meta.get("Calculation methodology (briefly)"), meta.get("Note")) if x)
        details = {"periodicity": meta.get("Periodicity"), "classifier": meta.get("Classifiers"),
                   "last_modified": meta.get("Last modified date"), "warnings": warnings}
        return IndicatorInfo(self.name, code, meta.get("Indicator name", code), definition or meta.get("Indicator name", ""),
                             unit, details)

    def _rows(self, item: dict) -> dict[str, dict]:
        return {str(r.get("Code")): r for r in item["data"]}

    def describe(self, code: str, dataset: str | None, iso3: str) -> dict[str, Any]:
        item, _ = self.table(code)
        rows = self._rows(item)
        periods = [k for k in item["data"][0] if re.fullmatch(r"\d{4}(-Q\d)?", k)] if item["data"] else []
        return {"country": "UZB only", "periodicity": self._meta(item).get("Periodicity"),
                "periods": f"{periods[0]}-{periods[-1]}" if periods else "none",
                "filters": {"row": {c: r.get("Klassifikator_en") or r.get("Klassifikator") or "" for c, r in rows.items()}}}

    def fetch(self, ref: SourceRef, countries: Iterable[str], refresh: bool = False) -> FetchedSeries:
        unknown = sorted(set(ref.filters) - {"row"})
        if unknown:
            raise SourceError(f"SIAT filters can only be row; not {', '.join(unknown)}")
        if refresh:
            self.catalog(refresh=True)
        item, snap = self.table(ref.code, refresh)
        info = self.info(ref.code)
        if COUNTRY not in list(countries):
            return FetchedSeries(info, empty_frame(with_note=True), [snap], snap)
        columns = list(item["data"][0]) if item["data"] else []
        if any(re.fullmatch(r"\d{4}-Q\d", c) for c in columns):
            raise SourceError(f"SIAT indicator {ref.code} is quarterly; only annual tables are read "
                              "(quarterly values are year-to-date totals). Look for the annual table instead.")
        years = [c for c in columns if re.fullmatch(r"\d{4}", c)]
        rows = self._rows(item)
        wanted = ref.filters.get("row")
        if wanted is None:
            wanted = next(iter(rows)) if len(rows) == 1 else (REPUBLIC_ROW if REPUBLIC_ROW in rows else None)
        if wanted not in rows:
            choices = ", ".join(f"{c} ({r.get('Klassifikator_en') or ''})" for c, r in list(rows.items())[:25])
            raise SourceError(f"SIAT indicator {ref.code} has several rows; add filters: {{row: ...}}. Choices: {choices}")
        row = rows[wanted]
        records = [(COUNTRY, int(y), _number(row.get(y)), "") for y in years]
        details = dict(info.details, row=wanted, row_label=row.get("Klassifikator_en"))
        info = IndicatorInfo(info.source, info.code, f"{info.name}: {row.get('Klassifikator_en') or wanted}",
                             info.definition, info.unit, details)
        return FetchedSeries(info, make_frame(records), [snap], snap)
