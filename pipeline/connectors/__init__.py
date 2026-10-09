"""One connector per data source, sharing one cache and one internet client."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

import httpx

from ..cache import RawCache
from ..spec import NATIONAL_SOURCE, SourceRef
from .base import FetchedSeries, IndicatorInfo, SourceError, UnknownCode
from .pwt import PWT
from .wdi import WDI

AVAILABLE = {"wdi": WDI, "pwt": PWT}
LATER = {"ilostat", "imf", "faostat", "comtrade", "manual"}

__all__ = ["FetchedSeries", "IndicatorInfo", "NotAvailableYet", "SourceError", "Sources", "UnknownCode"]


class NotAvailableYet(SourceError):
    pass


class Sources:
    def __init__(self, cache: RawCache, client: httpx.Client, attempts: int = 4,
                 sleep: Callable[[float], None] = time.sleep):
        self.cache = cache
        self.client = client
        self.attempts = attempts
        self.sleep = sleep
        self._open: dict[str, WDI | PWT] = {}

    def get(self, name: str) -> WDI | PWT:
        if name in AVAILABLE:
            if name not in self._open:
                self._open[name] = AVAILABLE[name](self.cache, self.client, self.attempts, self.sleep)
            return self._open[name]
        if name in LATER or NATIONAL_SOURCE.match(name):
            raise NotAvailableYet(f"source '{name}' is added in Phase 5")
        raise SourceError(f"unknown source '{name}'")

    def fetch(self, ref: SourceRef, countries: Iterable[str], refresh: bool = False) -> FetchedSeries:
        return self.get(ref.source).fetch(ref, list(countries), refresh)

    def close(self) -> None:
        self.client.close()
