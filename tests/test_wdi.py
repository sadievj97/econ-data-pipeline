import json
import math

import httpx
import pytest

from pipeline.connectors import SourceError, UnknownCode
from pipeline.net import DownloadError
from pipeline.spec import SourceRef

from .conftest import FIXTURES

AGR = SourceRef(source="wdi", code="NV.AGR.TOTL.KD", unit="constant 2015 US$", currency="USD",
                price_basis="constant", base_year=2015)


def data_requests(internet):
    return [r for r in internet.requests if r.url.path.startswith("/v2/country/")]


def test_fetch_returns_every_year_exactly_as_published(sources):
    series = sources.fetch(AGR, ["UZB", "KAZ"])
    data = series.data
    assert list(data.columns) == ["iso3", "year", "value"]
    assert list(data["iso3"].unique()) == ["KAZ", "UZB"]  # sorted
    assert len(data) == 132 and data["year"].min() == 1960
    uzb_2025 = data[(data.iso3 == "UZB") & (data.year == 2025)].value.iloc[0]
    assert uzb_2025 == 34874645593.6796  # the value in the raw World Bank file


def test_missing_values_stay_missing(sources):
    data = sources.fetch(AGR, ["UZB"]).data
    assert math.isnan(data[data.year == 1960].value.iloc[0])


def test_metadata_comes_from_the_catalog(sources):
    info = sources.fetch(AGR, ["UZB"]).info
    assert info.name == "Agriculture, forestry, and fishing, value added (constant 2015 US$)"
    assert info.unit.price_basis == "constant" and info.unit.base_year == 2015 and info.unit.currency == "USD"
    assert info.definition.startswith("Agriculture, forestry, and fishing")


def test_provenance_records_everything_rule_3_asks_for(sources):
    p = sources.fetch(AGR, ["UZB", "KAZ"]).provenance()
    for field in ("source", "code", "unit", "base_year", "currency", "price_basis", "retrieved_at", "raw_files"):
        assert p[field], field
    assert len(p["raw_files"]) == 2 and all(f.endswith("data.json") for f in p["raw_files"])


def test_second_fetch_uses_the_cache(sources, internet):
    sources.fetch(AGR, ["UZB"])
    before = len(internet.requests)
    again = sources.fetch(AGR, ["UZB"])
    assert len(internet.requests) == before
    assert again.raw[0].read_bytes()  # still verifies


def test_refresh_downloads_again_into_a_new_snapshot(sources, internet):
    first = sources.fetch(AGR, ["UZB"])
    second = sources.fetch(AGR, ["UZB"], refresh=True)
    assert len(data_requests(internet)) == 2
    assert first.raw[0].dir != second.raw[0].dir
    assert first.raw[0].dir.exists()  # the old snapshot is kept


def test_error_replies_are_reported_and_never_cached(sources, internet, tmp_path):
    refusal = [{"message": [{"id": "120", "key": "Invalid value", "value": "The provided parameter value is not valid"}]}]
    internet.add("/v2/country/UZB/indicator/NV.AGR.TOTL.KD", httpx.Response(200, json=refusal))
    with pytest.raises(SourceError, match="refused .*not valid"):
        sources.fetch(AGR, ["UZB"])
    assert sources.cache.latest("wdi/data/NV.AGR.TOTL.KD/UZB") is None


def test_wrong_country_in_the_reply_fails_loudly(sources, internet):
    body = json.loads((FIXTURES / "wdi" / "data_NV.AGR.TOTL.KD_KAZ.json").read_bytes())
    internet.add("/v2/country/UZB/indicator/NV.AGR.TOTL.KD", httpx.Response(200, json=body))
    with pytest.raises(SourceError, match="expected UZB"):
        sources.fetch(AGR, ["UZB"])


def test_multi_page_replies_are_refused(sources, internet):
    internet.add("/v2/country/UZB/indicator/NV.AGR.TOTL.KD",
                 httpx.Response(200, json=[{"page": 1, "pages": 2, "total": 40000}, []]))
    with pytest.raises(SourceError, match="2 pages"):
        sources.fetch(AGR, ["UZB"])


def test_unknown_code(sources):
    with pytest.raises(UnknownCode, match="not in the WDI indicator list"):
        sources.fetch(AGR.model_copy(update={"code": "NV.AGR.NOPE"}), ["UZB"])


def test_wdi_takes_no_filters(sources):
    with pytest.raises(SourceError, match="no dataset or filters"):
        sources.fetch(AGR.model_copy(update={"filters": {"sex": "T"}}), ["UZB"])


def test_retries_busy_server_then_succeeds(sources, internet):
    internet.add("/v2/country/UZB/indicator/NV.AGR.TOTL.KD", httpx.Response(503), httpx.Response(429))
    assert len(sources.fetch(AGR, ["UZB"]).data) == 66


def test_gives_up_after_the_last_try(sources, internet):
    internet.add("/v2/country/UZB/indicator/NV.AGR.TOTL.KD", *[httpx.Response(503)] * 3)
    with pytest.raises(DownloadError, match="after 3 tries"):
        sources.fetch(AGR, ["UZB"])


def test_country_without_data_gives_no_rows(sources):
    assert sources.fetch(AGR, ["TKM"]).data.empty
