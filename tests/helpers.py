"""Small hand-made specs and series for harmonization and validation tests."""

from pathlib import Path

import pandas as pd

from pipeline.cache import Snapshot
from pipeline.connectors import FetchedSeries, IndicatorInfo
from pipeline.datalog import DataLog
from pipeline.harmonize import harmonize
from pipeline.spec import parse_spec
from pipeline.units import UnitInfo

USD = {"unit": "constant 2015 US$", "currency": "USD", "price_basis": "constant", "base_year": 2015}
PCT = {"unit": "% of total employment", "price_basis": "not_applicable"}


def src(code="X", source="wdi", **unit):
    return {"source": source, "code": code, **(unit or USD)}


def var(name, *sources, measure="level", transforms=None, **extra):
    return {"name": name, "label": name, "measure": measure, "sources": list(sources) or [src(name)],
            "transforms": transforms or [], **extra}


def make_spec(*variables, countries=("UZB", "KAZ"), start=2000, end=2005, **extra):
    return parse_spec({"spec_version": 1, "name": "t", "title": "Test", "confirmed": True,
                       "countries": list(countries), "years": {"start": start, "end": end},
                       "variables": list(variables), **extra})


def series(rows, code="X", source="wdi", storage=None):
    data = pd.DataFrame(rows, columns=["iso3", "year", "value"]).astype(
        {"iso3": "string", "year": "int64", "value": "float64"})
    info = IndicatorInfo(source, code, code, code, UnitInfo("u", None, None, None),
                         {"storage_type": storage} if storage else {})
    snap = Snapshot("test/key", Path("raw") / "test", {"filename": "f.json", "retrieved_at": "2026-10-09T00:00:00Z",
                                                       "sha256": "0" * 64})
    return FetchedSeries(info, data, [snap], snap)


def rows(iso3, start, values):
    return [(iso3, start + i, v) for i, v in enumerate(values)]


def build(spec, fetched):
    return harmonize(spec, fetched, DataLog())
