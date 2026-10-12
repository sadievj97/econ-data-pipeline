"""One connector per data source, sharing one cache and one internet client."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

from ..cache import RawCache
from ..spec import NATIONAL_SOURCE, SourceRef
from .base import FetchedSeries, IndicatorInfo, SourceError, UnknownCode
from .faostat import FAOSTAT
from .ilostat import ILOSTAT
from .imf import IMF
from .manual import Manual
from .pwt import PWT
from .siat import SIAT
from .wdi import WDI

AVAILABLE: dict[str, type] = {"wdi": WDI, "pwt": PWT, "ilostat": ILOSTAT, "imf": IMF, "faostat": FAOSTAT,
                              "nso_uzb": SIAT, "manual": Manual}

__all__ = ["FetchedSeries", "IndicatorInfo", "NotAvailableYet", "SourceError", "Sources", "UnknownCode"]


class NotAvailableYet(SourceError):
    pass


class Sources:
    def __init__(self, cache: RawCache, client: httpx.Client, attempts: int = 4,
                 sleep: Callable[[float], None] = time.sleep, manual_dir: Path | None = None):
        self.cache = cache
        self.client = client
        self.attempts = attempts
        self.sleep = sleep
        self.manual_dir = manual_dir
        self._open: dict[str, Any] = {}

    def get(self, name: str) -> Any:
        if name in AVAILABLE:
            if name not in self._open:
                self._open[name] = AVAILABLE[name](self.cache, self.client, self.attempts, self.sleep,
                                                   manual_dir=self.manual_dir)
            return self._open[name]
        if name == "comtrade":
            raise NotAvailableYet("source 'comtrade' is not connected yet: it needs a UN Comtrade key in .env")
        if NATIONAL_SOURCE.match(name):
            raise NotAvailableYet(f"there is no connector for '{name}' yet: save its files in data/manual/ "
                                  "and use source: manual")
        raise SourceError(f"unknown source '{name}'")

    def fetch(self, ref: SourceRef, countries: Iterable[str], refresh: bool = False) -> FetchedSeries:
        return self.get(ref.source).fetch(ref, list(countries), refresh)

    def close(self) -> None:
        self.client.close()
