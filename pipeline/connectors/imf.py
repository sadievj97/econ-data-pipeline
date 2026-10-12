"""IMF data via the IMF's SDMX 2.1 API at api.imf.org (no key needed).

Structure: https://api.imf.org/external/sdmx/2.1/dataflow/<agency>/<id>/latest?references=all
Data:      https://api.imf.org/external/sdmx/2.1/data/<agency>,<id>/<key>   (CSV)

A spec names the dataset (for example IMF.RES,WEO, or a fixed release such as
IMF.RES,WEO_2026_APR_VINTAGE, which never changes) and the indicator code;
other dimensions of the dataset are filters. World Economic Outlook values after
a country's latest actual year are IMF projections and are marked as such.
"""

from __future__ import annotations

import csv
import io
import math
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

import httpx

from .. import net
from ..cache import RawCache, Snapshot
from ..spec import SourceRef
from ..units import from_imf_name
from .base import FetchedSeries, IndicatorInfo, SourceError, UnknownCode, ambiguity, make_frame

BASE_URL = "https://api.imf.org/external/sdmx/2.1"
CATALOG_DATASETS = ("IMF.RES,WEO", "IMF.STA,ANEA")
CSV_ACCEPT = "application/vnd.sdmx.data+csv;version=1.0.0"
FIXED_DIMS = ("COUNTRY", "INDICATOR", "FREQUENCY")
SCALES = {"0": "", "3": "thousands", "6": "millions", "9": "billions", "12": "trillions"}
NS = {"str": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
      "com": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"}
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", text)


def _english(element: ET.Element, tag: str) -> str:
    texts = element.findall(f"com:{tag}", NS)
    for t in texts:
        if t.get(XML_LANG) == "en":
            return (t.text or "").strip()
    return (texts[0].text or "").strip() if texts else ""


class Structure:
    def __init__(self, dims: list[str], codes: dict[str, dict[str, tuple[str, str]]], snapshot: Snapshot):
        self.dims = dims  # dimension ids in key order
        self.codes = codes  # dimension -> {code: (name, description)}
        self.snapshot = snapshot

    @property
    def extra_dims(self) -> list[str]:
        return [d for d in self.dims if d not in FIXED_DIMS]


def parse_structure(content: bytes, snapshot: Snapshot) -> Structure:
    root = ET.fromstring(content)
    dsd = root.find(".//str:DataStructure", NS)
    if dsd is None:
        raise SourceError("The IMF structure reply has no data structure")
    codelists = {}
    for cl in root.iter(f"{{{NS['str']}}}Codelist"):
        codelists[cl.get("id")] = {c.get("id"): (_english(c, "Name"), _english(c, "Description"))
                                   for c in cl.findall("str:Code", NS)}
    # A dimension names its code list directly, or through its concept's core representation.
    concept_lists = {}
    for concept in root.iter(f"{{{NS['str']}}}Concept"):
        ref = concept.find("str:CoreRepresentation/str:Enumeration/Ref", NS)
        if ref is not None:
            concept_lists[concept.get("id")] = ref.get("id")
    dims, codes = [], {}
    found = sorted(dsd.findall(".//str:DimensionList/str:Dimension", NS), key=lambda d: int(d.get("position", 0)))
    for dim in found:
        dims.append(dim.get("id"))
        ref = dim.find("str:LocalRepresentation/str:Enumeration/Ref", NS)
        if ref is not None:
            list_id = ref.get("id")
        else:
            concept = dim.find("str:ConceptIdentity/Ref", NS)
            list_id = concept_lists.get(concept.get("id") if concept is not None else dim.get("id"))
        codes[dim.get("id")] = codelists.get(list_id, {})
    return Structure(dims, codes, snapshot)


class IMF:
    name = "imf"

    def __init__(self, cache: RawCache, client: httpx.Client, attempts: int = 4,
                 sleep: Callable[[float], None] = time.sleep, **_: Any):
        self.cache = cache
        self.client = client
        self.attempts = attempts
        self.sleep = sleep
        self._structures: dict[str, Structure] = {}
        self._refreshed: set[str] = set()

    def structure(self, dataset: str, refresh: bool = False) -> Structure:
        if "," not in dataset:
            raise SourceError(f"IMF dataset '{dataset}' should look like AGENCY,ID (for example IMF.RES,WEO)")
        if dataset in self._structures and (not refresh or dataset in self._refreshed):
            return self._structures[dataset]
        key = f"imf/structure/{_safe(dataset)}"
        snap = None if refresh else self.cache.latest(key)
        if snap is None:
            agency, flow = dataset.split(",", 1)
            url = f"{BASE_URL}/dataflow/{agency}/{flow}/latest"
            response = net.get(self.client, url, {"references": "all"}, attempts=self.attempts, sleep=self.sleep)
            try:
                ET.fromstring(response.content)
            except ET.ParseError:
                raise SourceError(f"The IMF sent something that is not XML for the {dataset} structure") from None
            snap = self.cache.store(key, "structure.xml", response.content,
                                    {"source": self.name, "url": url, "params": {"references": "all"}})
        structure = parse_structure(snap.read_bytes(), snap)
        if "INDICATOR" not in structure.dims or "COUNTRY" not in structure.dims:
            raise SourceError(f"IMF dataset {dataset} has no COUNTRY and INDICATOR dimensions")
        self._structures[dataset] = structure
        if refresh:
            self._refreshed.add(dataset)
        return structure

    def catalog(self, refresh: bool = False) -> dict[str, IndicatorInfo]:
        """Indicators of the main IMF datasets, keyed 'DATASET:CODE'."""
        entries = {}
        for dataset in CATALOG_DATASETS:
            for code, (name, description) in self.structure(dataset, refresh).codes["INDICATOR"].items():
                entries[f"{dataset}:{code}"] = IndicatorInfo(self.name, code, name, description or name,
                                                             from_imf_name(name), {"dataset": dataset})
        return dict(sorted(entries.items()))

    def info(self, code: str, dataset: str | None = None, filters: dict | None = None) -> IndicatorInfo:
        if not dataset:
            raise SourceError("IMF series need a dataset, for example dataset: IMF.RES,WEO")
        codes = self.structure(dataset).codes["INDICATOR"]
        if code not in codes:
            raise UnknownCode(f"'{code}' is not an indicator in IMF dataset {dataset}")
        name, description = codes[code]
        return IndicatorInfo(self.name, code, name, description or name, from_imf_name(name), {"dataset": dataset})

    def describe(self, code: str, dataset: str | None, iso3: str) -> dict[str, Any]:
        info = self.info(code, dataset)
        structure = self.structure(info.details["dataset"])
        filters = {d: {k: v[0] for k, v in list(structure.codes[d].items())[:40]} for d in structure.extra_dims}
        return {"dataset": info.details["dataset"], "filters": filters,
                "note": "every dimension listed under filters needs a value in the spec"}

    def _rows(self, dataset: str, key: str, refresh: bool) -> tuple[list[dict[str, str]], Snapshot]:
        cache_key = f"imf/data/{_safe(dataset)}/{_safe(key)}"
        snap = None if refresh else self.cache.latest(cache_key)
        if snap is None:
            url = f"{BASE_URL}/data/{dataset}/{key}"
            response = net.get(self.client, url, attempts=self.attempts, sleep=self.sleep, ok_statuses=(200, 404),
                               headers={"Accept": CSV_ACCEPT})
            content = response.content
            if response.status_code == 200 and not content.decode("utf-8-sig", errors="replace").startswith("DATAFLOW"):
                raise SourceError(f"The IMF sent an unexpected reply for {dataset} {key}")
            snap = self.cache.store(cache_key, "data.csv", content,
                                    {"source": self.name, "url": url, "http_status": response.status_code,
                                     "note": "404 means the IMF has no data for this query" if response.status_code == 404 else ""})
        if snap.meta.get("http_status") == 404:
            return [], snap
        return list(csv.DictReader(io.StringIO(snap.read_bytes().decode("utf-8-sig")))), snap

    def fetch(self, ref: SourceRef, countries: Iterable[str], refresh: bool = False) -> FetchedSeries:
        info = self.info(ref.code, ref.dataset)
        structure = self.structure(info.details["dataset"], refresh)
        unknown = sorted(set(ref.filters) - set(structure.extra_dims))
        if unknown:
            raise SourceError(f"IMF dataset {ref.dataset} has no dimension(s) {', '.join(unknown)}; "
                              f"its filters are {', '.join(structure.extra_dims) or 'none'}")
        records, snaps, scales, base_years, latest = [], [], set(), {}, {}
        for iso3 in countries:
            parts = []
            for dim in structure.dims:
                parts.append({"COUNTRY": iso3, "INDICATOR": ref.code, "FREQUENCY": "A"}.get(dim, ref.filters.get(dim, "")))
            rows, snap = self._rows(info.details["dataset"], ".".join(parts), refresh)
            snaps.append(snap)
            by_year: dict[str, list[dict[str, str]]] = defaultdict(list)
            for r in rows:
                by_year[r["TIME_PERIOD"]].append(r)
            for year, group in sorted(by_year.items()):
                if len(group) > 1:
                    raise SourceError(f"IMF {ref.dataset} {ref.code} has {len(group)} values for {iso3} {year}; "
                                      f"add filters to pick one. Choices: {ambiguity(group, structure.extra_dims)}")
                if not re.fullmatch(r"\d{4}", year):
                    raise SourceError(f"{snap.relative_file()}: '{year}' is not an annual date")
                r = group[0]
                raw = (r.get("OBS_VALUE") or "").strip()
                value = float(raw) if raw not in ("", "NA", "NaN") else math.nan
                last = (r.get("LATEST_ACTUAL_ANNUAL_DATA") or "").strip()
                if last.isdigit():
                    latest[iso3] = int(last)
                note = "IMF projection" if last.isdigit() and int(year) > int(last) else ""
                scales.add((r.get("SCALE") or "").strip())
                if (r.get("BASE_YEAR") or "").strip():
                    base_years[iso3] = r["BASE_YEAR"].strip()
                records.append((iso3, int(year), value, note))
        scales.discard("")
        if len(scales) > 1:
            raise SourceError(f"IMF {ref.code} comes in different scales for different countries ({sorted(scales)})")
        scale = SCALES.get(next(iter(scales), "0"), f"x10^{next(iter(scales), '0')}")
        unit = info.unit
        if scale:
            unit = replace(unit, text=f"{unit.text}, {scale}" if unit.text else scale)
        if unit.price_basis == "constant" and len(set(base_years.values())) == 1:
            year = next(iter(base_years.values()))
            if year.isdigit():
                unit = replace(unit, base_year=int(year))
        warnings = []
        if len(set(base_years.values())) > 1:
            warnings.append("the base year differs by country: " + ", ".join(f"{c} {y}" for c, y in sorted(base_years.items())))
        if latest:
            warnings.append("values after each country's latest actual year are IMF projections (marked in the data): "
                            + ", ".join(f"{c} after {y}" for c, y in sorted(latest.items())))
        details = dict(info.details, scale=scale or "units", base_years=base_years, latest_actual=latest,
                       warnings=warnings)
        info = replace(info, unit=unit, details=details)
        return FetchedSeries(info, make_frame(records), snaps, structure.snapshot)
