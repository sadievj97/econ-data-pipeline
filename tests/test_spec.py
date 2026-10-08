import pytest
import yaml

from pipeline.spec import SpecError, dump_spec, load_spec, parse_spec

from .conftest import EXAMPLE_SPEC


def problems_for(data: dict) -> str:
    with pytest.raises(SpecError) as info:
        parse_spec(data)
    return str(info.value)


def test_example_spec_is_valid():
    spec = load_spec(EXAMPLE_SPEC)
    assert spec.countries == ["UZB", "KAZ"]
    assert spec.confirmed is False
    assert spec.variables[0].sources[0].code == "NV.AGR.TOTL.KD"


def test_dump_and_reload_gives_the_same_spec():
    spec = load_spec(EXAMPLE_SPEC)
    assert parse_spec(yaml.safe_load(dump_spec(spec))) == spec


def test_dump_is_deterministic():
    spec = load_spec(EXAMPLE_SPEC)
    assert dump_spec(spec) == dump_spec(load_spec(EXAMPLE_SPEC))


def test_lowercase_country_codes_are_accepted(example_data):
    example_data["countries"] = ["uzb", "kaz"]
    assert parse_spec(example_data).countries == ["UZB", "KAZ"]


def _var(data: dict, name: str) -> dict:
    return next(v for v in data["variables"] if v["name"] == name)


# Each case breaks the example spec in one way and names text the error must contain.
BROKEN = {
    "unknown country": (lambda d: d.update(countries=["UZB", "UZK"]), "not ISO 3166 alpha-3 country codes: UZK"),
    "repeated country": (lambda d: d.update(countries=["UZB", "UZB"]), "listed more than once: UZB"),
    "years reversed": (lambda d: d.update(years={"start": 2023, "end": 2000}), "start (2023) is after end (2000)"),
    "wrong spec version": (lambda d: d.update(spec_version=2), "spec_version"),
    "no variables": (lambda d: d.update(variables=[]), "variables"),
    "typo in a field name": (lambda d: _var(d, "gfcf_real").update(souces=[]), "unknown field (check the spelling)"),
    "missing measure": (lambda d: _var(d, "gfcf_real").pop("measure"), "#7 (gfcf_real) -> measure: this field is required"),
    "repeated variable name": (lambda d: _var(d, "va_ind_real").update(name="va_agr_real"), "used more than once: va_agr_real"),
    "uppercase variable name": (lambda d: _var(d, "gfcf_real").update(name="GFCF"), "must start with a lowercase letter"),
    "name too long for Stata": (lambda d: _var(d, "gfcf_real").update(name="g" * 33), "Stata allows at most 32"),
    "name is a key column": (lambda d: _var(d, "gfcf_real").update(name="year"), "reserved for the panel's key columns"),
    "name is a Stata word": (lambda d: _var(d, "gfcf_real").update(name="long"), "reserved word in Stata or R"),
    "label too long for Stata": (lambda d: _var(d, "gfcf_real").update(label="x" * 81), "at most 80 characters"),
    "constant prices without base year": (
        lambda d: _var(d, "gfcf_real")["sources"][0].pop("base_year"),
        "#1 (wdi): a constant-price series needs a base_year",
    ),
    "current prices with base year": (
        lambda d: _var(d, "labor_share_pwt")["sources"][0].update(base_year=2021),
        "a current-price series has no base_year",
    ),
    "currency without price basis": (
        lambda d: _var(d, "emp_pwt")["sources"][0].update(currency="USD"),
        "needs price_basis: current or constant",
    ),
    "unknown currency": (lambda d: _var(d, "gfcf_real")["sources"][0].update(currency="USX"), "not an ISO 4217"),
    "unknown source": (lambda d: _var(d, "gfcf_real")["sources"][0].update(source="wdl"), "unknown source 'wdl'"),
    "code given as a number": (lambda d: _var(d, "gfcf_real")["sources"][0].update(code=123), "code"),
    "manual source without file": (
        lambda d: _var(d, "gfcf_real")["sources"].append(
            {"source": "manual", "code": "x", "unit": "u", "price_basis": "not_applicable"}
        ),
        "a manual source needs file:",
    ),
    "manual file outside data/manual": (
        lambda d: _var(d, "gfcf_real")["sources"].append(
            {"source": "manual", "code": "x", "unit": "u", "price_basis": "not_applicable", "file": "../secret.csv"}
        ),
        "must be a path inside data/manual/",
    ),
    "sector outside the classification": (
        lambda d: _var(d, "va_ind_real").update(sector="MFG"),
        "sector 'MFG' is not part of isic4_3sector",
    ),
    "sector without a classification": (
        lambda d: d.pop("sector_classification"),
        "va_agr_real: has sector AGR but the spec has no sector_classification",
    ),
    "comparison with unknown variable": (
        lambda d: d["comparisons"][0].update(b="gdp_pwt"),
        "comparison #1: 'gdp_pwt' is not a variable in this spec",
    ),
    "comparison without threshold": (lambda d: d["comparisons"][0].pop("threshold"), "threshold: this field is required"),
    "interpolation without method": (
        lambda d: _var(d, "gfcf_real").update(transforms=[{"kind": "interpolate", "max_gap": 2}]),
        "method: this field is required",
    ),
    "unknown transform": (
        lambda d: _var(d, "gfcf_real").update(transforms=[{"kind": "smooth", "method": "hp"}]),
        "transforms -> #1 (smooth)",
    ),
    "extrapolation with unknown proxy": (
        lambda d: _var(d, "gfcf_real").update(
            transforms=[{"kind": "extrapolate", "method": "proxy_growth", "proxy": "nope", "direction": "both"}]
        ),
        "gfcf_real: extrapolate refers to 'nope', which is not a variable in this spec",
    ),
    "rebase shift without current-price series": (
        lambda d: _var(d, "gfcf_real").update(
            transforms=[{"kind": "rebase", "method": "shift_reference_year", "year": 2021}]
        ),
        "shift_reference_year needs current_price_variable",
    ),
    "splice with a source the variable lacks": (
        lambda d: _var(d, "gfcf_real").update(
            transforms=[{"kind": "splice", "method": "ratio_at_link_year", "with_source": "nso_uzb", "link_year": 2010}]
        ),
        "splice with_source 'nso_uzb' must name exactly one of this variable's sources",
    ),
    "scale between incompatible units": (
        lambda d: _var(d, "gfcf_real").update(
            transforms=[{"kind": "scale", "from_unit": "thousands", "to_unit": "percent"}]
        ),
        "can only scale between",
    ),
}


@pytest.mark.parametrize("case", BROKEN, ids=list(BROKEN))
def test_broken_spec_fails_loudly(example_data, case):
    breaker, expected = BROKEN[case]
    breaker(example_data)
    assert expected in problems_for(example_data)


def test_all_problems_are_reported_at_once(example_data):
    example_data["countries"] = ["UZK"]
    _var(example_data, "gfcf_real").pop("measure")
    message = problems_for(example_data)
    assert "has 2 problems" in message
    assert "UZK" in message and "measure" in message


def test_valid_transforms_are_accepted(example_data):
    _var(example_data, "gfcf_real")["sources"].append(
        {"source": "nso_uzb", "code": "gfcf", "unit": "constant 2015 US$", "currency": "USD",
         "price_basis": "constant", "base_year": 2015}
    )
    _var(example_data, "gfcf_real")["transforms"] = [
        {"kind": "splice", "method": "ratio_at_link_year", "with_source": "nso_uzb", "link_year": 2010},
        {"kind": "interpolate", "method": "linear", "max_gap": 2, "note": "two missing years in the 1990s"},
        {"kind": "extrapolate", "method": "proxy_growth", "proxy": "gdp_real_wdi", "direction": "backward"},
        {"kind": "scale", "from_unit": "units", "to_unit": "millions"},
    ]
    assert len(parse_spec(example_data).variables[6].transforms) == 4


def test_yaml_syntax_error_reports_the_line(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: x\ncountries: [UZB, KAZ\nyears: 1\n", encoding="utf-8")
    with pytest.raises(SpecError) as info:
        load_spec(path)
    assert "line 3" in str(info.value) and "not valid YAML" in str(info.value)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(SpecError, match="file not found"):
        load_spec(tmp_path / "nope.yaml")
