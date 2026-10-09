import pyreadstat
import pytest

from pipeline.connectors import SourceError, UnknownCode
from pipeline.spec import SourceRef

from .conftest import PWT_FIXTURE

LABSH = SourceRef(source="pwt", dataset="11.0", code="labsh", unit="share", price_basis="current")
RGDPNA = SourceRef(source="pwt", dataset="11.0", code="rgdpna", unit="millions of 2021 US$", currency="USD",
                   price_basis="constant", base_year=2021)


def test_fetch_matches_the_file_exactly(sources):
    data = sources.fetch(RGDPNA, ["UZB", "KAZ"]).data
    original, _ = pyreadstat.read_dta(str(PWT_FIXTURE))
    expected = original[(original.countrycode == "UZB") & (original.year == 2023)].rgdpna.iloc[0]
    assert data[(data.iso3 == "UZB") & (data.year == 2023)].value.iloc[0] == expected
    assert len(data) == 148 and list(data.iso3.unique()) == ["KAZ", "UZB"]


def test_known_labour_share_value(sources):
    data = sources.fetch(LABSH, ["UZB"]).data
    assert data[data.year == 2023].value.iloc[0] == 0.37945353984832764


def test_download_is_checked_against_the_published_fingerprint(sources, internet):
    internet.pwt_sha1 = "0" * 40
    with pytest.raises(SourceError, match="does not match the fingerprint"):
        sources.fetch(LABSH, ["UZB"])
    assert sources.cache.latest("pwt/11.0/pwt110.dta") is None


def test_one_download_serves_every_variable(sources, internet):
    sources.fetch(LABSH, ["UZB"])
    sources.fetch(RGDPNA, ["UZB"])
    assert sum(r.url.path == "/api/access/datafile/554030" for r in internet.requests) == 1


def test_catalog_has_labels_units_and_storage_types(sources):
    catalog = sources.get("pwt").catalog()
    assert "countrycode" not in catalog and "year" not in catalog
    rnna = catalog["rnna"]
    assert rnna.name == "Capital stock at constant 2021 national prices (in mil. 2021US$)"
    assert (rnna.unit.price_basis, rnna.unit.base_year, rnna.unit.currency) == ("constant", 2021, "USD")
    # The real pwt110.dta stores labsh as "float"; the re-saved fixture stores "double".
    assert catalog["labsh"].details["storage_type"] in ("float", "double")
    assert catalog["i_outlier"].details["value_labels"]


def test_provenance_points_at_the_raw_file(sources):
    p = sources.fetch(RGDPNA, ["UZB"]).provenance()
    assert p["raw_files"][0].endswith("pwt110.dta") and p["base_year"] == 2021


def test_unknown_variable_and_wrong_version(sources):
    with pytest.raises(UnknownCode):
        sources.fetch(LABSH.model_copy(update={"code": "nope"}), ["UZB"])
    with pytest.raises(SourceError, match="Only PWT 11.0"):
        sources.fetch(LABSH.model_copy(update={"dataset": "10.01"}), ["UZB"])
