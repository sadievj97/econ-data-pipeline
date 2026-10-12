import math

import numpy as np
import pytest

from .helpers import PCT, USD, build, make_spec, rows, series, src, var

NAN = float("nan")


def values(panel, name, iso3):
    return panel.results[name].row(iso3).tolist()


def same(a, b):
    return all((math.isnan(x) and math.isnan(y)) or x == y for x, y in zip(a, b, strict=True))


def test_keeps_only_the_spec_years_and_logs_what_was_left_out():
    spec = make_spec(var("a"), countries=("UZB",), start=2000, end=2002)
    panel = build(spec, {("a", 0): series(rows("UZB", 1998, [1, 2, 3, 4, 5, NAN]))})
    assert values(panel, "a", "UZB") == [3, 4, 5]
    text = panel.log.markdown("t")
    assert "left out 3 observations outside 2000-2002 (2 of them had values)" in text


def test_every_country_and_year_is_in_the_grid():
    spec = make_spec(var("a"), start=2000, end=2003)
    panel = build(spec, {("a", 0): series(rows("UZB", 2001, [1.5]))})
    assert same(values(panel, "a", "KAZ"), [NAN] * 4)
    assert same(values(panel, "a", "UZB"), [NAN, 1.5, NAN, NAN])
    assert len(panel.long()) == 8


def test_same_for_all_uses_the_preferred_source_when_complete():
    spec = make_spec(var("a", src("P"), src("F")), start=2000, end=2001)
    fetched = {("a", 0): series(rows("UZB", 2000, [1, 2]) + rows("KAZ", 2000, [3, 4])),
               ("a", 1): series(rows("UZB", 2000, [9, 9]) + rows("KAZ", 2000, [9, 9]))}
    panel = build(spec, fetched)
    assert values(panel, "a", "KAZ") == [3, 4]
    assert panel.results["a"].source_for == {"KAZ": 0, "UZB": 0}


def test_same_for_all_falls_back_for_every_country_rather_than_mixing():
    spec = make_spec(var("a", src("P"), src("F")), start=2000, end=2001)
    fetched = {("a", 0): series(rows("UZB", 2000, [1, 2])),
               ("a", 1): series(rows("UZB", 2000, [5, 6]) + rows("KAZ", 2000, [7, 8]))}
    panel = build(spec, fetched)
    assert values(panel, "a", "UZB") == [5, 6]  # not the preferred source's 1, 2
    assert "earlier sources lack data" in panel.log.markdown("t")


def test_same_for_all_without_a_complete_source_says_what_is_missing():
    spec = make_spec(var("a", src("P"), src("F")), start=2000, end=2001)
    panel = build(spec, {("a", 1): series(rows("UZB", 2000, [5, 6]))})
    assert "no values for KAZ" in panel.log.markdown("t")


def test_per_country_choice_and_unit_mixing():
    lcu = {"unit": "constant LCU", "currency": "LCU", "price_basis": "constant", "base_year": 2015}
    spec = make_spec(var("a", src("P"), src("F", **lcu)), start=2000, end=2001, source_choice="per_country")
    fetched = {("a", 0): series(rows("UZB", 2000, [1, 2])), ("a", 1): series(rows("KAZ", 2000, [7, 8]))}
    panel = build(spec, fetched)
    assert panel.results["a"].source_for == {"KAZ": 1, "UZB": 0}
    assert [p.check for p in panel.problems] == ["unit_mixing"]


def test_a_duplicate_country_year_stops_that_variable():
    spec = make_spec(var("a"), countries=("UZB",), start=2000, end=2001)
    panel = build(spec, {("a", 0): series(rows("UZB", 2000, [1, 2]) + [("UZB", 2001, 2.5)])})
    assert "a" not in panel.results
    assert panel.problems[0].check == "duplicate_keys" and "UZB 2001" in panel.problems[0].message


def test_float32_storage_is_recorded():
    spec = make_spec(var("a"), countries=("UZB",), start=2000, end=2000)
    panel = build(spec, {("a", 0): series(rows("UZB", 2000, [0.5]), storage="float")})
    assert panel.results["a"].precision == "float32"


# ------------------------------------------------------------- transforms


def one(transforms, data, measure="level", **unit):
    spec = make_spec(var("a", src("A", **(unit or USD)), measure=measure, transforms=transforms),
                     countries=("UZB",), start=2000, end=2000 + len(data) - 1)
    return build(spec, {("a", 0): series(rows("UZB", 2000, data))})


def test_nothing_is_filled_without_a_transform():
    panel = one([], [1, NAN, 3])
    assert same(values(panel, "a", "UZB"), [1, NAN, 3])


def test_linear_interpolation_fills_short_interior_gaps_and_flags_them():
    panel = one([{"kind": "interpolate", "method": "linear", "max_gap": 2}], [NAN, 1, NAN, 3, NAN, NAN, NAN, 7])
    r = panel.results["a"]
    assert same(r.row("UZB").tolist(), [NAN, 1, 2, 3, NAN, NAN, NAN, 7])  # leading gap and 3-year gap left alone
    assert r.flags[0][2] == "interpolated (linear)" and r.flags[0][1] == ""
    log = panel.log.markdown("t")
    assert "filled 2002" in log and "left unfilled (longer than 2 years): 2004-2006" in log


def test_log_linear_interpolation():
    panel = one([{"kind": "interpolate", "method": "log_linear", "max_gap": 1}], [1, NAN, 4])
    assert values(panel, "a", "UZB")[1] == pytest.approx(2.0, rel=1e-15)
    bad = one([{"kind": "interpolate", "method": "log_linear", "max_gap": 1}], [0, NAN, 4])
    assert bad.problems and "positive values" in bad.problems[0].message


def test_extrapolation_with_proxy_growth():
    spec = make_spec(
        var("x", src("X"), transforms=[{"kind": "extrapolate", "method": "proxy_growth", "proxy": "p",
                                         "direction": "both"}]),
        var("p", src("P")), countries=("UZB",), start=2000, end=2004)
    fetched = {("x", 0): series(rows("UZB", 2000, [NAN, NAN, 10, 11, NAN])),
               ("p", 0): series(rows("UZB", 2000, [1, 2, 4, 4.5, 9]))}
    panel = build(spec, fetched)  # x is listed first but built after its proxy
    assert values(panel, "x", "UZB") == [2.5, 5.0, 10, 11, 22.0]
    assert panel.results["x"].flags[0][0].startswith("extrapolated")


def test_rebase_to_an_index():
    panel = one([{"kind": "rebase", "method": "index", "year": 2001}], [2, 4, 8])
    r = panel.results["a"]
    assert r.row("UZB").tolist() == [50, 100, 200]
    assert r.unit["unit"].startswith("index, 2001=100") and r.unit["base_year"] == 2001


def test_rebase_to_another_reference_year():
    current = {"unit": "current US$", "currency": "USD", "price_basis": "current"}
    spec = make_spec(
        var("real", src("R"), transforms=[{"kind": "rebase", "method": "shift_reference_year", "year": 2001,
                                            "current_price_variable": "nominal"}]),
        var("nominal", src("N", **current)), countries=("UZB",), start=2000, end=2001)
    panel = build(spec, {("real", 0): series(rows("UZB", 2000, [2, 4])),
                         ("nominal", 0): series(rows("UZB", 2000, [3, 8]))})
    assert values(panel, "real", "UZB") == [4, 8] and panel.results["real"].unit["base_year"] == 2001


def test_splice_takes_earlier_years_from_the_other_source_scaled_at_the_link_year():
    spec = make_spec(var("a", src("P"), src("O", source="nso_uzb"),
                         transforms=[{"kind": "splice", "method": "ratio_at_link_year", "with_source": "nso_uzb",
                                      "link_year": 2002}]),
                     countries=("UZB",), start=2000, end=2003)
    fetched = {("a", 0): series(rows("UZB", 2000, [NAN, 99, 20, 22])),
               ("a", 1): series(rows("UZB", 2000, [4, 5, 10, NAN]), source="nso_uzb")}
    panel = build(spec, fetched)
    r = panel.results["a"]
    assert r.row("UZB").tolist() == [8, 10, 20, 22]
    assert r.flags[0][0].startswith("spliced") and r.flags[0][2] == ""
    assert "1 preferred-source values replaced, 1 added" in panel.log.markdown("t")


def test_scale_changes_every_value_and_the_unit():
    panel = one([{"kind": "scale", "from_unit": "thousands", "to_unit": "units"}], [1.5, 2])
    assert values(panel, "a", "UZB") == [1500, 2000]
    panel = one([{"kind": "scale", "from_unit": "percent", "to_unit": "fraction"}], [25, 50], measure="percent", **PCT)
    assert values(panel, "a", "UZB") == [0.25, 0.5]
    assert "rescaled from percent to fraction" in panel.results["a"].unit["unit"]


def test_transforms_make_values_full_doubles():
    spec = make_spec(var("a", transforms=[{"kind": "scale", "from_unit": "thousands", "to_unit": "units"}]),
                     countries=("UZB",), start=2000, end=2000)
    panel = build(spec, {("a", 0): series(rows("UZB", 2000, [0.5]), storage="float")})
    assert panel.results["a"].precision == "float64"


def test_transform_loops_are_reported():
    spec = make_spec(
        var("x", transforms=[{"kind": "extrapolate", "method": "proxy_growth", "proxy": "y", "direction": "both"}]),
        var("y", transforms=[{"kind": "extrapolate", "method": "proxy_growth", "proxy": "x", "direction": "both"}]),
        countries=("UZB",), start=2000, end=2001)
    panel = build(spec, {})
    assert {p.variable for p in panel.problems} == {"x", "y"} and not panel.results


def test_long_panel_is_sorted_and_typed():
    spec = make_spec(var("b"), var("a"), start=2000, end=2001)
    fetched = {("b", 0): series(rows("UZB", 2000, [1, 2]) + rows("KAZ", 2000, [3, 4])),
               ("a", 0): series(rows("UZB", 2000, [5, 6]) + rows("KAZ", 2000, [7, 8]))}
    long = build(spec, fetched).long()
    assert long["variable"].tolist()[:4] == ["b"] * 4  # spec order
    assert long["iso3"].tolist()[:4] == ["KAZ", "KAZ", "UZB", "UZB"]
    assert str(long["year"].dtype) == "int64" and np.isnan(long["value"]).sum() == 0
