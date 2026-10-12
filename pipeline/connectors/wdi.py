"""World Bank World Development Indicators, via the public API (no key needed).

Data:    https://api.worldbank.org/v2/country/<ISO3>/indicator/<code>?format=json&source=2
Catalog: https://api.worldbank.org/v2/source/2/indicator?format=json

One raw file per indicator and country, covering every year the API has, so a
cached download can serve any year range. Units and definitions come from the
cached catalog.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import pandas as pd

from .. import net
from ..cache import RawCache, Snapshot
from ..spec import SourceRef
from ..units import from_wdi_name
from .base import FetchedSeries, IndicatorInfo, SourceError, UnknownCode, empty_frame

BASE_URL = "https://api.worldbank.org/v2"
WDI_SOURCE_ID = "2"
PER_PAGE = "20000"
CATALOG_KEY = "wdi/catalog"


def _check_payload(payload: Any, what: str) -> dict:
    if isinstance(payload, list) and payload and isinstance(payload[0], dict) and "message" in payload[0]:
        reasons = "; ".join(str(m.get("value", "")) for m in payload[0]["message"])
        raise SourceError(f"The World Bank API refused {what}: {reasons}")
    if not (isinstance(payload, list) and len(payload) == 2 and isinstance(payload[0], dict)):
        raise SourceError(f"The World Bank API sent an unexpected response for {what}")
    header = payload[0]
    if int(header.get("pages", 1)) > 1:
        raise SourceError(f"The World Bank response for {what} has {header['pages']} pages; "
                          "the connector only reads one and needs updating")
    return header


class WDI:
    name = "wdi"

    def __init__(self, cache: RawCache, client: httpx.Client, attempts: int = 4,
                 sleep: Callable[[float], None] = time.sleep, **_: Any):
        self.cache = cache
        self.client = client
        self.attempts = attempts
        self.sleep = sleep
        self._catalog: dict[str, IndicatorInfo] | None = None
        self._catalog_snapshot: Snapshot | None = None
        self._catalog_refreshed = False  # --refresh downloads the catalog once per run, not per variable

    # ------------------------------------------------------------ downloading

    def _snapshot(self, key: str, filename: str, url: str, params: dict[str, str], what: str,
                  refresh: bool) -> Snapshot:
        if not refresh:
            cached = self.cache.latest(key)
            if cached is not None:
                return cached
        response = net.get(self.client, url, params, attempts=self.attempts, sleep=self.sleep)
        try:
            payload = json.loads(response.content)
        except json.JSONDecodeError:
            raise SourceError(f"The World Bank API sent something that is not JSON for {what}") from None
        header = _check_payload(payload, what)  # never cache an error reply
        meta = {
            "source": self.name,
            "url": url,
            "params": params,
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "source_last_updated": header.get("lastupdated"),
        }
        return self.cache.store(key, filename, response.content, meta)

    # ---------------------------------------------------------------- catalog

    def catalog(self, refresh: bool = False) -> dict[str, IndicatorInfo]:
        if self._catalog is not None and (not refresh or self._catalog_refreshed):
            return self._catalog
        snap = self._snapshot(CATALOG_KEY, "indicators.json", f"{BASE_URL}/source/{WDI_SOURCE_ID}/indicator",
                              {"format": "json", "per_page": PER_PAGE}, "the WDI indicator list", refresh)
        payload = json.loads(snap.read_bytes())
        entries: dict[str, IndicatorInfo] = {}
        for row in payload[1] or []:
            name = (row.get("name") or "").strip()
            entries[row["id"]] = IndicatorInfo(
                source=self.name,
                code=row["id"],
                name=name,
                definition=(row.get("sourceNote") or "").strip(),
                unit=from_wdi_name(name),
                details={"source_organization": (row.get("sourceOrganization") or "").strip()},
            )
        self._catalog = dict(sorted(entries.items()))
        self._catalog_snapshot = snap
        self._catalog_refreshed = self._catalog_refreshed or refresh
        return self._catalog

    def info(self, code: str, dataset: str | None = None, filters: dict | None = None) -> IndicatorInfo:
        catalog = self.catalog()
        if code not in catalog:
            raise UnknownCode(f"'{code}' is not in the WDI indicator list")
        return catalog[code]

    # ------------------------------------------------------------------- data

    def series(self, code: str, iso3: str, refresh: bool = False) -> tuple[pd.DataFrame, Snapshot]:
        snap = self._snapshot(f"wdi/data/{code}/{iso3}", "data.json", f"{BASE_URL}/country/{iso3}/indicator/{code}",
                              {"format": "json", "source": WDI_SOURCE_ID, "per_page": PER_PAGE},
                              f"{code} for {iso3}", refresh)
        payload = json.loads(snap.read_bytes())
        records = []
        for row in payload[1] or []:
            if row.get("countryiso3code") != iso3:
                raise SourceError(f"{snap.relative_file()}: expected {iso3}, found {row.get('countryiso3code')!r}")
            if row["indicator"]["id"] != code:
                raise SourceError(f"{snap.relative_file()}: expected {code}, found {row['indicator']['id']!r}")
            date = str(row["date"])
            if not re.fullmatch(r"\d{4}", date):
                raise SourceError(f"{snap.relative_file()}: '{date}' is not an annual date")
            value = row["value"]
            records.append((iso3, int(date), math.nan if value is None else float(value)))
        if not records:
            return empty_frame(), snap
        frame = pd.DataFrame(records, columns=["iso3", "year", "value"]).astype(
            {"iso3": "string", "year": "int64", "value": "float64"})
        return frame.sort_values(["iso3", "year"], kind="stable").reset_index(drop=True), snap

    def fetch(self, ref: SourceRef, countries: Iterable[str], refresh: bool = False) -> FetchedSeries:
        if ref.dataset or ref.filters:
            raise SourceError("WDI series take no dataset or filters; remove them from the spec")
        if refresh:
            self.catalog(refresh=True)
        info = self.info(ref.code)
        frames, snaps = [], []
        for iso3 in countries:
            frame, snap = self.series(ref.code, iso3, refresh)
            frames.append(frame)
            snaps.append(snap)
        data = pd.concat(frames, ignore_index=True) if frames else empty_frame()
        data = data.sort_values(["iso3", "year"], kind="stable").reset_index(drop=True)
        assert self._catalog_snapshot is not None
        return FetchedSeries(info=info, data=data, raw=snaps, metadata_raw=self._catalog_snapshot)
