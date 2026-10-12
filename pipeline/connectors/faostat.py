"""FAOSTAT, via FAO's bulk downloads (the FAOSTAT query API now needs a login).

List: https://bulks-faostat.fao.org/production/datasets_E.json
Data: one zip per dataset (FAOSTAT "domain") holding a normalized CSV.

A spec names the dataset (for example CS, capital stock), the item code, and
the element code as a filter, for example:
    source: faostat, dataset: CS, code: "22034", filters: {element: "6184"}
Countries are matched through their M49 codes. FAOSTAT's flag for each value
(for example "I: Imputed value") is kept as the value's source note.
"""

from __future__ import annotations

import csv
import io
import json
import math
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import pycountry

from .. import net
from ..cache import RawCache, Snapshot
from ..spec import SourceRef
from ..units import UnitInfo, from_fao
from .base import FetchedSeries, IndicatorInfo, SourceError, UnknownCode, make_frame

LIST_URL = "https://bulks-faostat.fao.org/production/datasets_E.json"
LIST_KEY = "faostat/datasets"


def _m49(iso3: str) -> str | None:
    country = pycountry.countries.get(alpha_3=iso3)
    return str(int(country.numeric)) if country else None


class Table:
    def __init__(self, rows: list[dict[str, str]], flags: dict[str, str], snapshot: Snapshot):
        self.rows = rows
        self.flags = flags
        self.snapshot = snapshot


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


class FAOSTAT:
    name = "faostat"

    def __init__(self, cache: RawCache, client: httpx.Client, attempts: int = 4,
                 sleep: Callable[[float], None] = time.sleep, **_: Any):
        self.cache = cache
        self.client = client
        self.attempts = attempts
        self.sleep = sleep
        self._datasets: dict[str, dict[str, Any]] | None = None
        self._list_snapshot: Snapshot | None = None
        self._tables: dict[str, Table] = {}
        self._refreshed: set[str] = set()

    def datasets(self, refresh: bool = False) -> dict[str, dict[str, Any]]:
        if self._datasets is not None and (not refresh or "__list__" in self._refreshed):
            return self._datasets
        snap = None if refresh else self.cache.latest(LIST_KEY)
        if snap is None:
            response = net.get(self.client, LIST_URL, attempts=self.attempts, sleep=self.sleep)
            try:
                json.loads(response.content)["Datasets"]["Dataset"]
            except (ValueError, KeyError, TypeError):
                raise SourceError("FAOSTAT's dataset list is not in the expected format") from None
            snap = self.cache.store(LIST_KEY, "datasets_E.json", response.content, {"source": self.name, "url": LIST_URL})
        items = json.loads(snap.read_bytes())["Datasets"]["Dataset"]
        self._datasets = {d["DatasetCode"]: d for d in items}
        self._list_snapshot = snap
        if refresh:
            self._refreshed.add("__list__")
        return self._datasets

    def catalog(self, refresh: bool = False) -> dict[str, IndicatorInfo]:
        """FAOSTAT datasets (domains). Items and elements are listed by describe()."""
        out = {}
        for code, d in sorted(self.datasets(refresh).items()):
            definition = " ".join(x for x in (d.get("Topic"), d.get("DatasetDescription")) if x)
            out[code] = IndicatorInfo(self.name, code, d.get("DatasetName", code), definition,
                                      UnitInfo("", None, None, None),
                                      {"dataset": code, "kind": "dataset", "updated": d.get("DateUpdate")})
        return out

    def table(self, dataset: str, refresh: bool = False) -> Table:
        if dataset in self._tables and (not refresh or dataset in self._refreshed):
            return self._tables[dataset]
        entry = self.datasets().get(dataset)
        if entry is None:
            raise UnknownCode(f"'{dataset}' is not a FAOSTAT dataset code")
        key = f"faostat/bulk/{dataset}"
        snap = None if refresh else self.cache.latest(key)
        if snap is None:
            url = entry["FileLocation"]
            content = net.get(self.client, url, attempts=self.attempts, sleep=self.sleep).content
            try:
                zipfile.ZipFile(io.BytesIO(content)).testzip()
            except zipfile.BadZipFile:
                raise SourceError(f"FAOSTAT's download for {dataset} is not a zip file") from None
            snap = self.cache.store(key, f"{dataset}.zip", content,
                                    {"source": self.name, "url": url, "dataset_updated": entry.get("DateUpdate")})
        with zipfile.ZipFile(io.BytesIO(snap.read_bytes())) as z:
            main = next((n for n in z.namelist() if "Normalized" in n and n.endswith(".csv")), None)
            if main is None:
                raise SourceError(f"FAOSTAT's {dataset} zip has no normalized CSV")
            rows = list(csv.DictReader(io.StringIO(_decode(z.read(main)))))
            flag_file = next((n for n in z.namelist() if n.endswith("_Flags.csv")), None)
            flags = {}
            if flag_file:
                for r in csv.DictReader(io.StringIO(_decode(z.read(flag_file)))):
                    flags[r.get("Flag", "")] = r.get("Description", "")
        table = Table(rows, flags, snap)
        self._tables[dataset] = table
        if refresh:
            self._refreshed.add(dataset)
        return table

    def _elements(self, table: Table, item: str) -> dict[str, tuple[str, str]]:
        return {r["Element Code"]: (r["Element"], r["Unit"]) for r in table.rows if r["Item Code"] == item}

    def info(self, code: str, dataset: str | None = None, filters: dict | None = None) -> IndicatorInfo:
        if not dataset:
            raise SourceError("FAOSTAT series need a dataset (for example dataset: CS)")
        table = self.table(dataset)
        items = {r["Item Code"]: r["Item"] for r in table.rows}
        if code not in items:
            raise UnknownCode(f"'{code}' is not an item code in FAOSTAT dataset {dataset}")
        elements = self._elements(table, code)
        element = (filters or {}).get("element")
        if element is None:
            if len(elements) != 1:
                choices = ", ".join(f"{k} ({v[0]}, {v[1]})" for k, v in sorted(elements.items()))
                raise SourceError(f"FAOSTAT {dataset} item {code} needs filters: {{element: ...}}; choices: {choices}")
            element = next(iter(elements))
        if element not in elements:
            raise UnknownCode(f"'{element}' is not an element of FAOSTAT {dataset} item {code}")
        el_name, unit = elements[element]
        about = self.datasets()[dataset]
        return IndicatorInfo(self.name, code, f"{items[code]} - {el_name}",
                             f"{items[code]}, {el_name} ({unit}). Dataset: {about.get('DatasetName')}.",
                             from_fao(el_name, unit), {"dataset": dataset, "element": element})

    def describe(self, code: str, dataset: str | None, iso3: str) -> dict[str, Any]:
        """For a dataset: its items. For an item (code) in a dataset: its elements and units."""
        target = dataset or code
        table = self.table(target)
        m49 = _m49(iso3)
        rows = [r for r in table.rows if r["Area Code (M49)"].lstrip("'").lstrip("0") == (m49 or "")]
        if dataset is None:
            items = {r["Item Code"]: r["Item"] for r in rows}
            return {"dataset": target, "country": iso3, "items": dict(sorted(items.items())),
                    "note": "use code: <item code> with dataset: " + target}
        elements = {r["Element Code"]: f"{r['Element']} ({r['Unit']})" for r in rows if r["Item Code"] == code}
        years = sorted({r["Year"] for r in rows if r["Item Code"] == code})
        return {"dataset": target, "item": code, "country": iso3, "filters": {"element": dict(sorted(elements.items()))},
                "years": f"{years[0]}-{years[-1]}" if years else "none"}

    def fetch(self, ref: SourceRef, countries: Iterable[str], refresh: bool = False) -> FetchedSeries:
        unknown = sorted(set(ref.filters) - {"element"})
        if unknown:
            raise SourceError(f"FAOSTAT filters can only be element; not {', '.join(unknown)}")
        if refresh:
            self.datasets(refresh=True)
        table = self.table(ref.dataset or "", refresh)
        info = self.info(ref.code, ref.dataset, ref.filters)
        element = info.details["element"]
        wanted = {m: iso3 for iso3 in countries if (m := _m49(iso3))}
        by_key: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
        for r in table.rows:
            m49 = r["Area Code (M49)"].lstrip("'").lstrip("0")
            if m49 in wanted and r["Item Code"] == ref.code and r["Element Code"] == element:
                by_key[(wanted[m49], int(r["Year"]))].append(r)
        records = []
        for (iso3, year), group in sorted(by_key.items()):
            if len(group) > 1:
                raise SourceError(f"FAOSTAT {ref.dataset} item {ref.code} has {len(group)} values for {iso3} {year}")
            r = group[0]
            raw = (r.get("Value") or "").strip()
            value = float(raw) if raw else math.nan
            flag = (r.get("Flag") or "").strip()
            note = f"{flag}: {table.flags.get(flag, '')}".strip(": ") if flag and flag != "A" else ""
            records.append((iso3, year, value, note))
        return FetchedSeries(info, make_frame(records), [table.snapshot], table.snapshot)
