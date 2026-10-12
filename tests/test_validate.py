from datetime import date

import pytest

from pipeline.config import load_config
from pipeline.events import Event
from pipeline.known_values import KnownValue
from pipeline.validate import validate

from .helpers import PCT, build, make_spec, rows, series, src, var

NAN = float("nan")


@pytest.fixture
def config():
    return load_config()


def run(spec, fetched, config, events=(), known=()):
    return validate(build(spec, fetched), config, list(events), list(known))


def checks(report, level):
    return [(f.check, f.variable, f.iso3) for f in report.findings if f.level == level]


def test_a_clean_panel_passes(config):
    spec = make_spec(var("a"), countries=("UZB",), start=2000, end=2002)
    report = run(spec, {("a", 0): series(rows("UZB", 2000, [10, 11, 12]))}, config)
    assert report.errors == [] and report.flags == [] and report.missing.empty


def test_planted_duplicate_fails(config):
    spec = make_spec(var("a"), countries=("UZB",), start=2000, end=2002)
    report = run(spec, {("a", 0): series(rows("UZB", 2000, [10, 11, 12]) + [("UZB", 2001, 11)])}, config)
    assert ("duplicate_keys", "a", None) in checks(report, "error")


def test_ranges(config):
    spec = make_spec(var("s", src("S", unit="share", price_basis="current"), measure="share"),
                     var("lvl"), var("neg", allow_negative=True), var("ok", valid_range=[0, 5]),
                     countries=("UZB",), start=2000, end=2001)
    fetched = {("s", 0): series(rows("UZB", 2000, [0.5, 1.2])), ("lvl", 0): series(rows("UZB", 2000, [-1, 2])),
               ("neg", 0): series(rows("UZB", 2000, [-1, 2])), ("ok", 0): series(rows("UZB", 2000, [4, 6]))}
    errors = checks(run(spec, fetched, config), "error")
    assert ("range", "s", "UZB") in errors and ("range", "lvl", "UZB") in errors
    assert ("range", "ok", "UZB") in errors and ("range", "neg", "UZB") not in errors


def test_spec_comparison_reports_years_above_the_threshold(config):
    spec = make_spec(var("a"), var("b"), countries=("UZB",), start=2000, end=2002,
                     comparisons=[{"a": "a", "b": "b", "compare": "growth", "threshold": 1.0}])
    fetched = {("a", 0): series(rows("UZB", 2000, [100, 110, 121])), ("b", 0): series(rows("UZB", 2000, [100, 112, 123.2]))}
    report = run(spec, fetched, config)
    comparison = report.comparisons[0]
    assert comparison.overlap == 2 and [(c, y) for c, y, _ in comparison.above] == [("UZB", 2001)]
    assert any("2001 (-2.00)" in f.message for f in report.flags)


def test_level_comparison_needs_identical_units(config):
    spec = make_spec(var("a"), var("b", src("B", **PCT), measure="percent"), countries=("UZB",), start=2000, end=2001,
                     comparisons=[{"a": "a", "b": "b", "compare": "level", "threshold": 5.0}])
    fetched = {("a", 0): series(rows("UZB", 2000, [1, 2])), ("b", 0): series(rows("UZB", 2000, [1, 2]))}
    assert ("comparison", "a", None) in checks(run(spec, fetched, config), "error")


def test_fallback_sources_are_compared_with_the_source_used(config):
    spec = make_spec(var("a", src("P"), src("F")), countries=("UZB",), start=2000, end=2002)
    fetched = {("a", 0): series(rows("UZB", 2000, [100, 110, 121])), ("a", 1): series(rows("UZB", 2000, [100, 110, 130]))}
    report = run(spec, fetched, config)
    assert report.comparisons[0].origin == "fallback source"
    assert ("comparison", "a", "UZB") in checks(report, "flag")


def test_jumps_are_flagged_with_nearby_events(config):
    spec = make_spec(var("a"), var("p", src("P", **PCT), measure="percent"), countries=("UZB",), start=2016, end=2018)
    fetched = {("a", 0): series(rows("UZB", 2016, [100, 105, 230])), ("p", 0): series(rows("UZB", 2016, [40, 41, 55]))}
    event = Event(iso3="UZB", date=date(2017, 9, 5), kind="currency_regime", description="FX unified", source="x")
    report = run(spec, fetched, config, events=[event])
    jumps = [f for f in report.flags if f.check == "jump"]
    assert len(jumps) == 2
    assert "2017-2018" in jumps[0].message and "near known event 2017-09-05: FX unified" in jumps[0].message
    assert report.events == [event]


def test_runs_of_identical_values(config):
    spec = make_spec(var("a"), var("b"), countries=("UZB",), start=2000, end=2003)
    fetched = {("a", 0): series(rows("UZB", 2000, [1, 2, 2, 2])), ("b", 0): series(rows("UZB", 2000, [1, 2, 2, 3]))}
    flags = checks(run(spec, fetched, config), "flag")
    assert ("repeated_values", "a", "UZB") in flags and ("repeated_values", "b", "UZB") not in flags


def test_known_values(config):
    spec = make_spec(var("a"), var("s", src("S", source="pwt", unit="share", price_basis="current"), measure="share"),
                     countries=("UZB",), start=2022, end=2023)
    fetched = {("a", 0): series(rows("UZB", 2022, [34874645593.6796, NAN])),
               ("s", 0): series(rows("UZB", 2022, [0.5, 0.37945353984832764]), source="pwt", storage="float")}
    known = [KnownValue(variable="a", iso3="UZB", year=2022, value=34874645593.6796, source="WDI"),
             KnownValue(variable="s", iso3="UZB", year=2023, value=0.37945354, source="PWT Excel"),
             KnownValue(variable="a", iso3="UZB", year=2022, value=34874645593.68, source="rounded"),
             KnownValue(variable="a", iso3="UZB", year=2023, value=1.0, source="missing year"),
             KnownValue(variable="zzz", iso3="UZB", year=2023, value=1.0, source="no such variable")]
    report = run(spec, fetched, config, known=known)
    assert [k.matched for k in report.known] == [True, True, False, False, False]
    assert report.known[1].note == "matches at the source's 4-byte precision"
    assert len([f for f in report.errors if f.check == "known_value"]) == 3


def test_missing_data_lists_every_gap(config):
    spec = make_spec(var("a"), start=2000, end=2001)
    report = run(spec, {("a", 0): series(rows("UZB", 2000, [1, NAN]))}, config)
    assert report.missing.values.tolist() == [["a", "KAZ", 2000], ["a", "KAZ", 2001], ["a", "UZB", 2001]]
