"""Penn World Table 11.0, the Stata file from the GGDC Dataverse (no key needed).

Dataset: https://doi.org/10.34894/FABVLR on dataverse.nl (CC BY 4.0).
The download is checked against the SHA-1 fingerprint the Dataverse publishes
for the file, then cached once; every variable is read from that one file.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import pandas as pd
import pyreadstat

from .. import net
from ..cache import RawCache, Snapshot
from ..spec import SourceRef
from ..units import from_pwt_label
from .base import FetchedSeries, IndicatorInfo, SourceError, UnknownCode

DATAVERSE = "https://dataverse.nl"
DOI = "doi:10.34894/FABVLR"
VERSION = "11.0"
FILENAME = "pwt110.dta"
CACHE_KEY = "pwt/11.0/pwt110.dta"
KEY_COLUMNS = ("countrycode", "country", "currency_unit", "year")
CITATION = ("Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer (2015), "
            "\"The Next Generation of the Penn World Table\", American Economic Review, 105(10), 3150-3182")


class PWT:
    name = "pwt"

    def __init__(self, cache: RawCache, client: httpx.Client, attempts: int = 4,
                 sleep: Callable[[float], None] = time.sleep):
        self.cache = cache
        self.client = client
        self.attempts = attempts
        self.sleep = sleep
        self._loaded: tuple[Snapshot, pd.DataFrame, Any] | None = None
        self._refreshed = False

    def _download(self) -> Snapshot:
        listing = net.get(self.client, f"{DATAVERSE}/api/datasets/:persistentId/", {"persistentId": DOI},
                          attempts=self.attempts, sleep=self.sleep).json()
        version = listing["data"]["latestVersion"]
        matches = [f["dataFile"] for f in version["files"] if f["dataFile"]["filename"] == FILENAME]
        if len(matches) != 1:
            raise SourceError(f"The PWT Dataverse record no longer has exactly one {FILENAME}")
        entry = matches[0]
        checksum = entry.get("checksum") or {}
        if checksum.get("type") != "SHA-1" or not checksum.get("value"):
            raise SourceError(f"The Dataverse publishes no SHA-1 fingerprint for {FILENAME}, so it cannot be verified")
        url = f"{DATAVERSE}/api/access/datafile/{entry['id']}"
        content = net.get(self.client, url, attempts=self.attempts, sleep=self.sleep).content
        if hashlib.sha1(content).hexdigest() != checksum["value"] or len(content) != entry["filesize"]:
            raise SourceError(f"The downloaded {FILENAME} does not match the fingerprint published on the Dataverse")
        meta = {
            "source": self.name,
            "url": url,
            "doi": DOI,
            "dataset_version": f"{version['versionNumber']}.{version['versionMinorNumber']}",
            "dataverse_file_id": entry["id"],
            "published_sha1": checksum["value"],
            "license": (version.get("license") or {}).get("name"),
            "citation": CITATION,
        }
        return self.cache.store(CACHE_KEY, FILENAME, content, meta)

    def snapshot(self, refresh: bool = False) -> Snapshot:
        if refresh and not self._refreshed:
            self._refreshed = True
            self._loaded = None
            return self._download()
        return self.cache.latest(CACHE_KEY) or self._download()

    def _load(self, refresh: bool = False) -> tuple[Snapshot, pd.DataFrame, Any]:
        if self._loaded is None or (refresh and not self._refreshed):
            snap = self.snapshot(refresh)
            snap.verify()
            frame, meta = pyreadstat.read_dta(str(snap.file))
            self._loaded = (snap, frame, meta)
        return self._loaded

    def catalog(self, refresh: bool = False) -> dict[str, IndicatorInfo]:
        _, frame, meta = self._load(refresh)
        labels = meta.column_names_to_labels
        entries = {}
        for col in frame.columns:
            if col in KEY_COLUMNS:
                continue
            label = (labels.get(col) or "").strip()
            details: dict[str, Any] = {"storage_type": meta.readstat_variable_types.get(col)}
            value_label = meta.variable_to_label.get(col)
            if value_label:
                details["value_labels"] = {str(k): v for k, v in meta.value_labels[value_label].items()}
            entries[col] = IndicatorInfo(self.name, col, label, label, from_pwt_label(label), details)
        return dict(sorted(entries.items()))

    def info(self, code: str) -> IndicatorInfo:
        catalog = self.catalog()
        if code not in catalog:
            raise UnknownCode(f"'{code}' is not a variable in PWT {VERSION}")
        return catalog[code]

    def fetch(self, ref: SourceRef, countries: Iterable[str], refresh: bool = False) -> FetchedSeries:
        if ref.dataset not in (None, VERSION):
            raise SourceError(f"Only PWT {VERSION} is available; the spec asks for dataset '{ref.dataset}'")
        if ref.filters:
            raise SourceError("PWT variables take no filters; remove them from the spec")
        snap, frame, _ = self._load(refresh)
        info = self.info(ref.code)
        wanted = frame.loc[frame["countrycode"].isin(list(countries)), ["countrycode", "year", ref.code]]
        data = pd.DataFrame({
            "iso3": wanted["countrycode"].astype("string"),
            "year": wanted["year"].astype("int64"),
            "value": wanted[ref.code].astype("float64"),
        }).sort_values(["iso3", "year"], kind="stable").reset_index(drop=True)
        return FetchedSeries(info=info, data=data, raw=[snap], metadata_raw=snap)
