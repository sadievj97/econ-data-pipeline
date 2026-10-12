"""Outputs: panel files, codebook and the R scripts; Stata files only when outputs.stata is on."""

import csv
import hashlib
import io
import json
import subprocess

import pyarrow.parquet as pq
import pyreadstat
import pytest
import yaml

from pipeline.build import run_build
from pipeline.config import OutputSettings, PathSettings, load_config
from pipeline.events import load_events
from pipeline.outputs import PANEL_FILES, STATA_FILES, _stata_str, dta_label, dta_timestamp
from pipeline.setup_check import find_rscript
from pipeline.spec import parse_spec

KEEP = ("va_agr_real", "gdp_real_wdi", "gdp_real_pwt", "labor_share_pwt")
ALL_FILES = sorted(PANEL_FILES + ("validation_report.md", "missing_data.csv", "data_log.md"))


@pytest.fixture
def config(tmp_path):
    return load_config().model_copy(update={"paths": PathSettings(
        raw_cache=str(tmp_path / "raw"), manual="data/manual", outputs=str(tmp_path / "out"))})


@pytest.fixture
def stata_config(config):
    return config.model_copy(update={"outputs": OutputSettings(stata=True)})


@pytest.fixture
def spec_file(example_data, tmp_path):
    example_data["confirmed"] = True
    example_data["countries"] = ["UZB", "KAZ", "TKM"]  # no fixture data for TKM: every value missing
    example_data["variables"] = [v for v in example_data["variables"] if v["name"] in KEEP]
    path = tmp_path / "specs" / "small.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(example_data, sort_keys=False), encoding="utf-8")
    known = {"values": [{"variable": "labor_share_pwt", "iso3": "UZB", "year": 2023,
                         "value": 0.37945353984832764, "source": "PWT"}]}
    path.with_name("small.known_values.yaml").write_text(yaml.safe_dump(known), encoding="utf-8")
    return path


def build(spec_file, sources, config):
    spec = parse_spec(yaml.safe_load(spec_file.read_text(encoding="utf-8")))
    result = run_build(spec_file, spec, sources, config, load_events())
    assert result.report.errors == [], result.report.errors
    return result


def read_csv(path):
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


def test_every_output_file_is_written(spec_file, sources, config):
    result = build(spec_file, sources, config)
    assert sorted(p.name for p in result.out_dir.iterdir()) == ALL_FILES


def test_rerun_on_cached_data_gives_identical_output_hashes(spec_file, sources, config):
    out = build(spec_file, sources, config).out_dir
    first = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}
    build(spec_file, sources, config)
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()} == first


def test_wide_and_long_csv(spec_file, sources, config):
    out = build(spec_file, sources, config).out_dir
    wide = read_csv(out / "panel_wide.csv")
    assert list(wide[0]) == ["iso3", "country", "year", *KEEP]
    assert len(wide) == 3 * 24 and [r["iso3"] for r in wide[::24]] == ["KAZ", "TKM", "UZB"]
    uzb_2023 = next(r for r in wide if r["iso3"] == "UZB" and r["year"] == "2023")
    assert uzb_2023["labor_share_pwt"] == "0.37945353984832764" and uzb_2023["country"] == "Uzbekistan"
    assert all(r[v] == "" for r in wide if r["iso3"] == "TKM" for v in KEEP)
    long = read_csv(out / "panel_long.csv")
    assert list(long[0]) == ["variable", "iso3", "country", "year", "value", "flag", "source_note"]
    assert len(long) == 3 * 24 * len(KEEP)
    assert (out / "panel_wide.csv").read_bytes().count(b"\r") == 0


def test_stata_files_only_when_switched_on(spec_file, sources, config, stata_config):
    out = build(spec_file, sources, config).out_dir
    assert not any((out / n).exists() for n in STATA_FILES)
    build(spec_file, sources, stata_config)
    assert (out / "panel.dta").exists() and (out / "load_and_check.do").exists()
    result = build(spec_file, sources, config)  # switched off again: the old Stata files go
    assert not any((out / n).exists() for n in STATA_FILES)
    assert "removed files an earlier build left" in (out / "data_log.md").read_text(encoding="utf-8")


def test_dta_has_labels_values_and_a_fixed_timestamp(spec_file, sources, stata_config):
    result = build(spec_file, sources, stata_config)
    frame, meta = pyreadstat.read_dta(str(result.out_dir / "panel.dta"))
    assert list(frame.columns) == ["iso3", "country", "year", *KEEP]
    assert meta.file_label == result.spec.title
    assert meta.column_names_to_labels["labor_share_pwt"].startswith("Share of labour compensation")
    value = frame[(frame.iso3 == "UZB") & (frame.year == 2023)].labor_share_pwt.iloc[0]
    assert value == result.panel.results["labor_share_pwt"].row("UZB")[-1]
    assert frame[frame.iso3 == "TKM"].va_agr_real.isna().all()
    raw = (result.out_dir / "panel.dta").read_bytes()
    newest = dta_timestamp([s.retrieved_at for s in sources.cache.used.values()])
    assert raw.startswith(b"<stata_dta><header><release>118</release>")
    assert b"<timestamp>\x11" + newest.encode() + b"</timestamp>" in raw


def test_parquet_types_nulls_and_codebook(spec_file, sources, config):
    out = build(spec_file, sources, config).out_dir
    table = pq.read_table(out / "panel.parquet")
    assert [f.name for f in table.schema] == ["variable", "iso3", "country", "year", "value", "flag", "source_note"]
    assert str(table.schema.field("year").type) == "int32" and str(table.schema.field("value").type) == "double"
    assert table.column("value").null_count == 24 * len(KEEP)  # TKM
    meta = json.loads(table.schema.metadata[b"econ_pipeline"])
    assert [row["variable"] for row in meta["codebook"]] == list(KEEP)


def test_codebook_records_everything_rule_3_asks_for(spec_file, sources, config):
    out = build(spec_file, sources, config).out_dir
    rows = {r["variable"]: r for r in read_csv(out / "codebook.csv")}
    assert list(rows) == list(KEEP)
    for row in rows.values():
        for field in ("source", "code", "unit", "price_basis", "retrieved_at", "raw_files", "raw_sha256", "definition"):
            assert row[field], (row["variable"], field)
    assert (rows["va_agr_real"]["base_year"], rows["va_agr_real"]["currency"]) == ("2015", "USD")
    assert (rows["gdp_real_pwt"]["base_year"], rows["gdp_real_pwt"]["dataset"]) == ("2021", "11.0")
    assert rows["va_agr_real"]["missing"] == "24"  # TKM
    text = (out / "codebook.md").read_text(encoding="utf-8")
    assert "Feenstra, Robert C." in text and "World Development Indicators" in text


def test_a_failed_build_removes_earlier_panel_files(spec_file, sources, config, internet):
    out = build(spec_file, sources, config).out_dir
    spec_file.with_name("small.known_values.yaml").write_text(yaml.safe_dump({"values": [
        {"variable": "va_agr_real", "iso3": "UZB", "year": 2020, "value": 1.0, "source": "wrong on purpose"}]}),
        encoding="utf-8")
    spec = parse_spec(yaml.safe_load(spec_file.read_text(encoding="utf-8")))
    result = run_build(spec_file, spec, sources, config, load_events())
    assert result.report.errors and {p.name for p in result.removed} == set(PANEL_FILES)
    assert sorted(p.name for p in out.iterdir()) == ["data_log.md", "missing_data.csv", "validation_report.md"]


def test_stata_strings_and_labels():
    assert _stata_str('GDP (constant 2015 US$) `x"') == '`"GDP (constant 2015 US\\$) \\`x""\''
    assert len(dta_label("x" * 100)) == 80 and dta_label("short") == "short"
    assert dta_timestamp(["2026-10-09T18:49:50Z", "2026-01-05T07:03:00Z"]) == "09 Oct 2026 18:49"


@pytest.mark.skipif(find_rscript() is None, reason="R is not installed")
def test_r_script_runs_and_catches_a_broken_file(spec_file, sources, config):
    out = build(spec_file, sources, config).out_dir
    script = (out / "load_and_check.R").read_text(encoding="utf-8")
    assert "labor_share_pwt UZB 2023" in script  # the known value is rechecked
    ok = subprocess.run([str(find_rscript()), str(out / "load_and_check.R")], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert "All checks passed" in ok.stdout and "va_agr_real: 24 missing" in ok.stdout
    assert "Ready: panel (one row per country-year), panel_long (with flag and source_note), codebook." in ok.stdout

    wide = out / "panel_wide.csv"
    lines = wide.read_text(encoding="utf-8").splitlines(keepends=True)
    wide.write_text("".join(lines + [lines[1]]), encoding="utf-8", newline="")  # plant a duplicate row
    bad = subprocess.run([str(find_rscript()), str(out / "load_and_check.R")], capture_output=True, text=True)
    assert bad.returncode != 0 and "CHECK FAILED" in bad.stderr

