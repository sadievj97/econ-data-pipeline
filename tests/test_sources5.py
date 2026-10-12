"""ILOSTAT, IMF, FAOSTAT, Statistics Agency of Uzbekistan, and manual files (saved fixtures, no internet)."""

import math

import pytest

from pipeline.cache import RawCache
from pipeline.connectors import NotAvailableYet, SourceError, Sources, UnknownCode
from pipeline.spec import SourceRef
from pipeline.units import UnitInfo, from_fao, from_ilo_label, from_imf_name, from_siat_unit


def ref(**kw):
    return SourceRef(**({"unit": "u", "price_basis": "not_applicable"} | kw))


def value(series, iso3, year):
    d = series.data
    return d[(d.iso3 == iso3) & (d.year == year)].value.iloc[0]


# ------------------------------------------------------------------- ILOSTAT

ILO_AGR = ref(source="ilostat", code="EMP_2EMP_SEX_ECO_NB_A", unit="thousands",
              filters={"sex": "SEX_T", "classif1": "ECO_SECTOR_AGR"})


def test_ilostat_catalog_has_annual_indicators_with_units(sources):
    catalog = sources.get("ilostat").catalog()
    assert "EMP_2EMP_SEX_ECO_NB_A" in catalog
    assert catalog["EMP_2EMP_SEX_ECO_NB_A"].unit == UnitInfo("thousands", "not_applicable", None, None)


def test_ilostat_fetch_with_filters(sources):
    series = sources.fetch(ILO_AGR, ["UZB", "KAZ"])
    assert value(series, "UZB", 2025) == 3518.158  # as published in the raw file
    assert set(series.data.iso3) == {"KAZ", "UZB"} and series.data.year.min() == 1991
    assert "warnings" not in series.info.details  # one source per country


def test_ilostat_needs_enough_filters_to_pick_one_series(sources):
    with pytest.raises(SourceError, match="add a filter to pick one.*classif1: ECO_SECTOR_AGR"):
        sources.fetch(ILO_AGR.model_copy(update={"filters": {"sex": "SEX_T"}}), ["UZB"])


def test_ilostat_wrong_filter_value_and_name(sources):
    with pytest.raises(SourceError, match="No ILOSTAT .* values match"):
        sources.fetch(ILO_AGR.model_copy(update={"filters": {"sex": "SEX_X"}}), ["UZB"])
    with pytest.raises(SourceError, match="filters can be"):
        sources.fetch(ILO_AGR.model_copy(update={"filters": {"gender": "T"}}), ["UZB"])


def test_ilostat_series_from_several_surveys_is_flagged(sources):
    lfs = ILO_AGR.model_copy(update={"code": "EMP_TEMP_SEX_ECO_NB_A"})
    series = sources.fetch(lfs, ["UZB"])
    assert any("combines" in w and "source" in w for w in series.info.details["warnings"])
    assert series.data.note.str.startswith("source ").all()
    one = sources.fetch(lfs.model_copy(update={"filters": dict(lfs.filters, source="BA:15965")}), ["UZB"])
    assert "warnings" not in one.info.details


def test_ilostat_describe_lists_choices(sources):
    choices = sources.get("ilostat").describe("EMP_2EMP_SEX_ECO_NB_A", None, "UZB")
    assert "ECO_SECTOR_AGR" in choices["filters"]["classif1"] and choices["years"] == "1991-2025"


def test_ilostat_unknown_code(sources):
    with pytest.raises(UnknownCode, match="annual ids end in _A"):
        sources.fetch(ILO_AGR.model_copy(update={"code": "EMP_TEMP_SEX_ECO_NB_Q"}), ["UZB"])


# ----------------------------------------------------------------------- IMF

WEO = ref(source="imf", dataset="IMF.RES,WEO", code="NGDP_RPCH", unit="percent change")


def test_imf_catalog_reads_the_indicator_code_list(sources):
    catalog = sources.get("imf").catalog()
    assert "IMF.RES,WEO:NGDP_RPCH" in catalog
    assert catalog["IMF.RES,WEO:NGDP_R"].unit.price_basis == "constant"


def test_imf_fetch_marks_projections(sources):
    series = sources.fetch(WEO, ["UZB", "KAZ"])
    assert value(series, "UZB", 2018) == 5.723684
    uzb = series.data[series.data.iso3 == "UZB"]
    assert (uzb[uzb.year > 2024].note == "IMF projection").all() and (uzb[uzb.year <= 2024].note == "").all()
    assert any("projections" in w for w in series.info.details["warnings"])


def test_imf_needs_a_dataset_and_a_known_code(sources):
    with pytest.raises(SourceError, match="need a dataset"):
        sources.fetch(WEO.model_copy(update={"dataset": None}), ["UZB"])
    with pytest.raises(UnknownCode):
        sources.fetch(WEO.model_copy(update={"code": "NOPE"}), ["UZB"])
    with pytest.raises(SourceError, match="no dimension"):
        sources.fetch(WEO.model_copy(update={"filters": {"PRICES": "Q"}}), ["UZB"])


def test_imf_no_data_is_cached_as_an_answer(sources, internet):
    series = sources.fetch(WEO, ["TKM"])
    assert series.data.empty and series.raw[0].meta["http_status"] == 404
    before = len(internet.requests)
    sources.fetch(WEO, ["TKM"])
    assert len(internet.requests) == before


# ------------------------------------------------------------------- FAOSTAT

FAO_K = ref(source="faostat", dataset="CS", code="22034", filters={"element": "6184"}, unit="million USD",
            currency="USD", price_basis="constant", base_year=2015)


def test_faostat_fetch_maps_countries_and_reads_units(sources):
    series = sources.fetch(FAO_K, ["UZB", "KAZ"])
    assert set(series.data.iso3) == {"KAZ", "UZB"} and series.data.year.max() == 2023
    assert series.info.unit == UnitInfo("million USD", "constant", 2015, "USD")
    assert not math.isnan(value(series, "UZB", 2020))
    notes = set(series.data.note)
    assert "F: Forecast value" in notes and "I: Value imputed by a receiving agency" in notes


def test_faostat_requires_an_element(sources):
    with pytest.raises(SourceError, match="needs filters: {element: ...}; choices: 6110"):
        sources.fetch(FAO_K.model_copy(update={"filters": {}}), ["UZB"])


def test_faostat_describe(sources):
    fao = sources.get("faostat")
    assert "22034" in fao.describe("CS", None, "UZB")["items"]
    assert "6184" in fao.describe("22034", "CS", "UZB")["filters"]["element"]


# ------------------------------------------- Statistics Agency of Uzbekistan

SIAT_A = ref(source="nso_uzb", code="1209", filters={"row": "A"}, unit="billion soums", currency="UZS",
             price_basis="constant", base_year=2015)


def test_siat_fetch_reads_one_row_for_uzbekistan_only(sources):
    series = sources.fetch(SIAT_A, ["UZB", "KAZ"])
    assert set(series.data.iso3) == {"UZB"} and value(series, "UZB", 2023) == 221004.9
    assert any("previous year's prices" in w for w in series.info.details["warnings"])
    assert series.info.unit.price_basis is None  # never called constant


def test_siat_needs_a_row_and_refuses_quarterly_tables(sources):
    with pytest.raises(SourceError, match="add filters: {row: ...}. Choices: A"):
        sources.fetch(SIAT_A.model_copy(update={"filters": {}}), ["UZB"])
    with pytest.raises(SourceError, match="quarterly"):
        sources.fetch(SIAT_A.model_copy(update={"code": "1584", "filters": {}}), ["UZB"])


def test_siat_reads_the_price_basis_from_the_name_when_needed(sources):
    info = sources.get("nso_uzb").info("548")
    assert info.unit.price_basis == "current" and info.unit.currency == "UZS"


def test_siat_catalog(sources):
    catalog = sources.get("nso_uzb").catalog()
    assert {"548", "1209", "1584"} <= set(catalog)
    assert "System of National Accounts" in catalog["1209"].definition


# -------------------------------------------------------------- manual files


@pytest.fixture
def manual(tmp_path, sources):
    folder = tmp_path / "manual"
    folder.mkdir()
    return folder, Sources(RawCache(tmp_path / "raw"), sources.client, manual_dir=folder)


MANUAL = ref(source="manual", code="gva", file="uzstat/gva.csv", unit="billion soums", currency="UZS",
             price_basis="current")


def write_manual(folder, text, note=True):
    path = folder / "uzstat" / "gva.csv"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if note:
        (folder / "uzstat" / "gva.csv.source.md").write_text("Downloaded from stat.uz on 2026-10-12.", encoding="utf-8")


def test_manual_file_is_read_and_cached(manual):
    folder, srcs = manual
    write_manual(folder, "iso3,year,gva,note\nUZB,2020,100.5,\nUZB,2021,,not published\nKAZ,2020,7,\n")
    series = srcs.fetch(MANUAL, ["UZB"])
    assert series.data.value.tolist()[0] == 100.5 and math.isnan(series.data.value.tolist()[1])
    assert series.data.note.tolist()[1] == "not published"
    assert series.raw[0].relative_file().endswith("gva.csv") and "warnings" in series.info.details


def test_manual_file_needs_a_note_and_must_not_change(manual):
    folder, srcs = manual
    write_manual(folder, "iso3,year,gva\nUZB,2020,1\n", note=False)
    with pytest.raises(SourceError, match="has no note"):
        srcs.fetch(MANUAL, ["UZB"])
    write_manual(folder, "iso3,year,gva\nUZB,2020,1\n")
    srcs.fetch(MANUAL, ["UZB"])
    write_manual(folder, "iso3,year,gva\nUZB,2020,2\n")
    with pytest.raises(SourceError, match="has changed since it was first used"):
        srcs.fetch(MANUAL, ["UZB"])
    assert srcs.fetch(MANUAL, ["UZB"], refresh=True).data.value.tolist() == [2.0]


def test_manual_values_must_be_numbers(manual):
    folder, srcs = manual
    write_manual(folder, "iso3,year,gva\nUZB,2020,about 5\n")
    with pytest.raises(SourceError, match="is not a number"):
        srcs.fetch(MANUAL, ["UZB"])


def test_unconnected_sources_say_what_to_do(sources):
    with pytest.raises(NotAvailableYet, match="data/manual"):
        sources.get("nso_kaz")
    with pytest.raises(NotAvailableYet, match="Comtrade key"):
        sources.get("comtrade")


# --------------------------------------------------------------------- units


@pytest.mark.parametrize("label, expected", [
    ("Employment by sex and economic activity (thousands)", UnitInfo("thousands", "not_applicable", None, None)),
    ("Unemployment rate by sex and age (%)", UnitInfo("%", "not_applicable", None, None)),
    ("Mean nominal monthly earnings of employees (Local currency)", UnitInfo("Local currency", "current", None, "LCU")),
    ("Mean weekly hours actually worked per employed person", UnitInfo("", None, None, None)),
])
def test_ilo_units(label, expected):
    assert from_ilo_label(label) == expected


@pytest.mark.parametrize("name, basis, currency", [
    ("Gross domestic product (GDP), Constant prices, Percent change", "not_applicable", None),
    ("Gross domestic product (GDP), Constant prices, Domestic currency", "constant", "LCU"),
    ("Gross domestic product (GDP), Current prices, U.S. dollars", "current", "USD"),
    ("Unemployment rate", None, None),
])
def test_imf_units(name, basis, currency):
    unit = from_imf_name(name)
    assert (unit.price_basis, unit.currency) == (basis, currency)


@pytest.mark.parametrize("element, unit, expected", [
    ("Value US$, 2015 prices", "million USD", UnitInfo("million USD", "constant", 2015, "USD")),
    ("Value US$", "million USD", UnitInfo("million USD", "current", None, "USD")),
    ("Value Standard Local Currency", "million SLC", UnitInfo("million SLC", "current", None, "LCU")),
    ("Area harvested", "ha", UnitInfo("ha", "not_applicable", None, None)),
])
def test_fao_units(element, unit, expected):
    assert from_fao(element, unit) == expected


def test_siat_units():
    assert from_siat_unit("at current prices, billion soums")[0] == UnitInfo("at current prices, billion soums", "current", None, "UZS")
    unit, warnings = from_siat_unit("previous year prices, national currency, billion soums")
    assert unit.price_basis is None and warnings
    assert from_siat_unit("thousand people")[0].price_basis == "not_applicable"
    assert from_siat_unit("in % to the previous year")[0].price_basis == "not_applicable"
