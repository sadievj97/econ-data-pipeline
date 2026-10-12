import math
import subprocess

import pytest
import yaml

from pipeline import growth
from pipeline.build import run_build
from pipeline.config import PathSettings, load_config
from pipeline.events import load_events
from pipeline.outputs import GROWTH_FILES
from pipeline.setup_check import find_rscript
from pipeline.spec import SpecError, parse_spec

from .helpers import build, make_spec, rows, series, src, var

NAN = float("nan")
SHARE = {"unit": "share", "price_basis": "current"}
COUNT = {"unit": "persons", "price_basis": "not_applicable"}


@pytest.fixture
def config():
    return load_config()


def spec_with(ga, countries=("UZB",), start=2000, end=2002, extra_vars=()):
    return make_spec(var("y"), var("k"), var("l", src("L", **COUNT)), var("s", src("S", **SHARE), measure="share"),
                     *extra_vars, countries=countries, start=start, end=end, growth_accounting=ga,
                     sector_classification="isic4_3sector")


GA = {"output": "y", "capital": "k", "labor": "l", "labor_share": 0.6, "sensitivity": [0.5]}


def panel(spec, y, k, l, s=(0.5, 0.7, 0.7)):
    return build(spec, {("y", 0): series(rows("UZB", 2000, y)), ("k", 0): series(rows("UZB", 2000, k)),
                        ("l", 0): series(rows("UZB", 2000, l)), ("s", 0): series(rows("UZB", 2000, list(s)))})


def annual(result, scope=1, rule=1):
    return [r for r in result.annual if r["scope_id"] == scope and r["rule_id"] == rule]


def test_solow_residual_by_hand(config):
    spec = spec_with(GA)
    result = growth.run(panel(spec, [100, 110, 121], [200, 220, 242], [50, 50, 50]), spec.growth_accounting, config)
    first = annual(result)[0]
    g = 100 * math.log(1.1)
    assert first["g_output"] == pytest.approx(g, rel=1e-14) and first["g_labor"] == 0
    assert first["tfp_growth"] == pytest.approx(g - 0.4 * g, rel=1e-14)
    assert first["labor_productivity_growth"] == pytest.approx(g, rel=1e-14)
    assert [r.label for r in result.rules[1]] == ["constant 0.6", "constant 0.5"]


def test_variable_labor_share_is_averaged_over_adjacent_years(config):
    spec = spec_with(dict(GA, labor_share="s", sensitivity=[]))
    result = growth.run(panel(spec, [100, 110, 121], [200, 220, 242], [50, 55, 60]), spec.growth_accounting, config)
    rows_ = annual(result)
    assert rows_[0]["labor_share"] == pytest.approx(0.6) and rows_[1]["labor_share"] == pytest.approx(0.7)
    assert len(result.rules[1]) == 1 + len(config.growth_accounting.sensitivity_shares)  # config default shares


def test_periods_with_a_missing_year_are_left_empty(config):
    spec = spec_with(dict(GA, periods=[[2000, 2001]]), end=2003)
    result = growth.run(panel(spec, [100, 110, NAN, 130], [1, 2, 3, 4], [5, 5, 5, 5], s=(0.5,) * 4),
                        spec.growth_accounting, config)
    by_period = {r["period"]: r for r in result.summary if r["rule_id"] == 1}
    assert math.isnan(by_period["2000-2003"]["tfp_growth"])  # 2002 missing: no average over the gap
    assert not math.isnan(by_period["2000-2001"]["tfp_growth"])


def test_zero_or_negative_inputs_stop_the_build(config):
    spec = spec_with(GA)
    result = growth.run(panel(spec, [100, 0, 121], [1, 2, 3], [5, 5, 5]), spec.growth_accounting, config)
    assert result.errors and "zero or below" in result.errors[0]


def test_sector_without_capital_reports_labor_productivity_only(config):
    ga = dict(GA, sectors=[{"sector": "AGR", "output": "y", "labor": "l"}])
    spec = spec_with(ga)
    result = growth.run(panel(spec, [100, 110, 121], [1, 2, 3], [50, 50, 50]), spec.growth_accounting, config)
    sector = annual(result, scope=2)
    assert all(math.isnan(r["tfp_growth"]) for r in sector) and not math.isnan(sector[0]["labor_productivity_growth"])
    assert len(result.rules[2]) == 1  # no sensitivity without capital


def test_missing_inputs_are_explained(config):
    spec = spec_with(dict(GA, human_capital="s"))
    result = growth.run(panel(spec, [100, 110, 121], [1, 2, 3], [5, 5, 5], s=(NAN, NAN, NAN)),
                        spec.growth_accounting, config)
    assert any("s has no values" in n for n in result.notes)


@pytest.mark.parametrize("change, message", [
    ({"output": "nope"}, "'nope' is not a variable"),
    ({"labor_share": 1.5}, "must be between 0 and 1"),
    ({"labor_share": "y"}, "must have measure: share"),
    ({"periods": [[1990, 2001]]}, "must lie inside the spec's years"),
    ({"sectors": [{"sector": "MFG", "output": "y", "labor": "l"}]}, "not part of the spec's sector_classification"),
    ({"sensitivity": [0]}, "between 0 and 1"),
])
def test_growth_accounting_section_is_checked(change, message):
    with pytest.raises(SpecError, match=message):
        spec_with(dict(GA, **change))


def test_output_must_be_at_constant_prices():
    with pytest.raises(SpecError, match="must be at constant prices"):
        make_spec(var("y", src("Y", unit="current US$", currency="USD", price_basis="current")), var("k"),
                  var("l", src("L", **COUNT)), countries=("UZB",), growth_accounting=GA)


# --------------------------------------------------------- full build


@pytest.fixture
def growth_spec(example_data, tmp_path):
    example_data["confirmed"] = True
    example_data["growth_accounting"] = {"output": "gdp_real_pwt", "capital": "capital_real_pwt", "labor": "emp_pwt",
                                         "labor_share": "labor_share_pwt", "periods": [[2000, 2010]]}
    path = tmp_path / "specs" / "g.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(example_data, sort_keys=False), encoding="utf-8")
    return path


def test_build_writes_growth_accounting_files(growth_spec, sources, tmp_path):
    config = load_config().model_copy(update={"paths": PathSettings(
        raw_cache=str(tmp_path / "raw"), manual="data/manual", outputs=str(tmp_path / "out"))})
    spec = parse_spec(yaml.safe_load(growth_spec.read_text(encoding="utf-8")))
    result = run_build(growth_spec, spec, sources, config, load_events())
    assert result.report.errors == [], result.report.errors
    out = result.out_dir
    for name in GROWTH_FILES:
        assert (out / name).exists(), name
    md = (out / "growth_accounting.md").read_text(encoding="utf-8")
    assert "Whole economy" in md and "| KAZ | 2000-2010 |" in md
    if find_rscript() is not None:
        run = subprocess.run([str(find_rscript()), str(out / "growth_accounting.R")], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        assert "Growth accounting reproduced" in run.stdout and "Tables ready: growth_annual, growth_summary." in run.stdout
