"""End-to-end builds on the saved fixture files (no internet)."""

import hashlib
import json

import httpx
import pytest
import yaml

import pipeline.cli as cli
from pipeline.build import lock_path, read_lock, run_build
from pipeline.cache import RawCache
from pipeline.config import PathSettings, load_config
from pipeline.connectors import Sources
from pipeline.events import load_events
from pipeline.outputs import PANEL_FILES
from pipeline.spec import parse_spec

from .conftest import FIXTURES

KEEP = ("va_agr_real", "gdp_real_wdi", "gdp_real_pwt", "labor_share_pwt")


@pytest.fixture
def config(tmp_path):
    base = load_config()
    return base.model_copy(update={"paths": PathSettings(raw_cache=str(tmp_path / "raw"), manual="data/manual",
                                                         outputs=str(tmp_path / "out"))})


@pytest.fixture
def spec_file(example_data, tmp_path):
    example_data["confirmed"] = True
    example_data["variables"] = [v for v in example_data["variables"] if v["name"] in KEEP]
    path = tmp_path / "specs" / "small.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(example_data, sort_keys=False), encoding="utf-8")
    return path


def build(spec_file, sources, config, refresh=False):
    spec = parse_spec(yaml.safe_load(spec_file.read_text(encoding="utf-8")))
    return run_build(spec_file, spec, sources, config, load_events(), refresh=refresh)


def hashes(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


def fresh_sources(cache_root, internet, pins=None):
    client = httpx.Client(transport=httpx.MockTransport(internet))
    return Sources(RawCache(cache_root, pins=pins), client, attempts=2, sleep=lambda _: None)


def test_build_passes_and_writes_reports_and_a_lock(spec_file, sources, config):
    result = build(spec_file, sources, config)
    assert result.report.errors == [], result.report.errors
    assert sorted(p.name for p in result.written) == sorted(
        PANEL_FILES + ("data_log.md", "missing_data.csv", "validation_report.md"))
    assert result.lock_changed and lock_path(spec_file).exists()
    report = (result.out_dir / "validation_report.md").read_text(encoding="utf-8")
    assert "**PASSED**" in report and "gdp_real_wdi vs gdp_real_pwt" in report
    assert "repeated_values" in report  # UZB labour share 2000-2010
    log = (result.out_dir / "data_log.md").read_text(encoding="utf-8")
    assert log.index("Raw files used") < log.index("Scope") < log.index("Validation")


def test_rebuild_from_the_cache_gives_identical_files(spec_file, sources, config, internet, tmp_path):
    first = build(spec_file, sources, config)
    before = hashes(first.out_dir)
    lock = lock_path(spec_file).read_text(encoding="utf-8")
    again = build(spec_file, fresh_sources(tmp_path / "raw", internet, read_lock(lock_path(spec_file))), config)
    assert hashes(again.out_dir) == before
    assert not again.lock_changed and lock_path(spec_file).read_text(encoding="utf-8") == lock


def test_planted_duplicate_country_year_fails_validation(spec_file, sources, config, internet):
    body = json.loads((FIXTURES / "wdi" / "data_NV.AGR.TOTL.KD_UZB.json").read_bytes())
    body[1].append(dict(body[1][10]))  # a second copy of one country-year
    internet.add("/v2/country/UZB/indicator/NV.AGR.TOTL.KD", httpx.Response(200, json=body))
    result = build(spec_file, sources, config)
    duplicates = [f for f in result.report.errors if f.check == "duplicate_keys"]
    assert duplicates and duplicates[0].variable == "va_agr_real" and f"UZB {body[1][10]['date']}" in duplicates[0].message
    assert "**FAILED**" in (result.out_dir / "validation_report.md").read_text(encoding="utf-8")


def test_known_values_next_to_the_spec_are_checked(spec_file, sources, config):
    known = {"values": [
        {"variable": "va_agr_real", "iso3": "UZB", "year": 2023, "value": 0.0, "source": "deliberately wrong"},
        # The fixture stores PWT values as 8-byte doubles (the real file uses 4-byte floats; see test_validate).
        {"variable": "labor_share_pwt", "iso3": "UZB", "year": 2023, "value": 0.37945353984832764, "source": "PWT"},
    ]}
    spec_file.with_name("small.known_values.yaml").write_text(yaml.safe_dump(known), encoding="utf-8")
    report = build(spec_file, sources, config).report
    assert [k.matched for k in report.known] == [False, True]
    assert [f.check for f in report.errors] == ["known_value"]


def test_lock_keeps_old_snapshots_until_refresh(spec_file, sources, config, internet, tmp_path):
    build(spec_file, sources, config)
    pinned = read_lock(lock_path(spec_file))
    # A newer download of one file arrives in the cache (for example from another spec).
    cache = RawCache(tmp_path / "raw")
    key = "wdi/data/NV.AGR.TOTL.KD/UZB"
    newer = cache.store(key, "data.json", (FIXTURES / "wdi" / "data_NV.AGR.TOTL.KD_KAZ.json").read_bytes(), {})
    result = build(spec_file, fresh_sources(tmp_path / "raw", internet, pinned), config)
    assert result.report.errors == [] and not result.lock_changed  # still the pinned snapshot
    refreshed = build(spec_file, fresh_sources(tmp_path / "raw", internet), config, refresh=True)
    assert refreshed.lock_changed and read_lock(lock_path(spec_file))[key] not in (pinned[key], newer.id)


def test_missing_pinned_snapshot_fails_loudly(spec_file, sources, config, internet, tmp_path):
    build(spec_file, sources, config)
    pins = read_lock(lock_path(spec_file))
    pins["wdi/data/NV.AGR.TOTL.KD/UZB"] = "19990101T000000Z"
    result = build(spec_file, fresh_sources(tmp_path / "raw", internet, pins), config)
    assert any(f.check == "fetch" and "no longer in" in f.message for f in result.report.errors)


def test_build_command(capsys, monkeypatch, spec_file, sources, config, internet):
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "open_sources", lambda cfg, pins=None: sources)
    assert cli.main(["build", str(spec_file)]) == 0
    out = capsys.readouterr().out
    assert "Errors: 0" in out and "Validation passed" in out and "Pinned the raw files" in out

    body = json.loads((FIXTURES / "wdi" / "data_NV.AGR.TOTL.KD_UZB.json").read_bytes())
    body[1].append(dict(body[1][10]))
    internet.add("/v2/country/UZB/indicator/NV.AGR.TOTL.KD", httpx.Response(200, json=body))
    monkeypatch.setattr(cli, "open_sources",
                        lambda cfg, pins=None: fresh_sources(config.folder("raw_cache"), internet))
    assert cli.main(["build", str(spec_file), "--refresh"]) == 1
    assert "ERROR duplicate_keys va_agr_real" in capsys.readouterr().out
