import subprocess
import sys

import pytest

import pipeline.cli as cli
from pipeline import setup_check
from pipeline.cli import main
from pipeline.paths import PROJECT_ROOT

from .conftest import EXAMPLE_SPEC
from .test_drafting import GOOD, REQUEST, FakeClaude, reply, tool_use


def run(capsys, *args):
    code = main(list(args))
    return code, capsys.readouterr().out


@pytest.fixture
def offline(monkeypatch, sources):
    """Make every command use the saved fixture files instead of the internet."""
    monkeypatch.setattr(cli, "open_sources", lambda config: sources)
    return sources


def small_spec(example_data, write_spec, keep=("va_agr_real", "gdp_real_wdi", "gdp_real_pwt", "labor_share_pwt")):
    example_data["variables"] = [v for v in example_data["variables"] if v["name"] in keep]
    example_data["comparisons"] = [c for c in example_data["comparisons"] if c["a"] in keep and c["b"] in keep]
    return write_spec(example_data)


def test_check_spec_summarizes_a_good_spec(capsys):
    code, out = run(capsys, "check-spec", str(EXAMPLE_SPEC))
    assert code == 0
    assert "UZB (Uzbekistan), KAZ (Kazakhstan)" in out
    assert "va_agr_real" in out and "NV.AGR.TOTL.KD" in out
    assert "DRAFT" in out and "passes all checks" in out


def test_check_spec_lists_problems_in_a_bad_spec(capsys, example_data, write_spec):
    example_data["countries"] = ["UZK"]
    code, out = run(capsys, "check-spec", str(write_spec(example_data)))
    assert code == 1
    assert "has 1 problem:" in out and "UZK" in out


def test_check_spec_reports_a_missing_file(capsys, tmp_path):
    code, out = run(capsys, "check-spec", str(tmp_path / "nope.yaml"))
    assert code == 1 and "file not found" in out


def test_fetch_downloads_checks_and_reports(capsys, offline, example_data, write_spec):
    code, out = run(capsys, "fetch", str(small_spec(example_data, write_spec)))
    assert code == 0, out
    assert "values in 2000-2023: 48 of 48" in out
    assert "Done: 4 series fetched, 0 problem(s)" in out


def test_fetch_flags_a_spec_that_disagrees_with_the_source(capsys, offline, example_data, write_spec):
    for v in example_data["variables"]:
        if v["name"] == "gdp_real_pwt":
            v["sources"][0]["base_year"] = 2017
    code, out = run(capsys, "fetch", str(small_spec(example_data, write_spec)))
    assert code == 1
    assert "PROBLEM: the spec says base_year 2017, but the source says 2021" in out


def test_fetch_reports_missing_years_and_skips_later_sources(capsys, offline, example_data, write_spec):
    example_data["countries"] = ["UZB", "TKM"]
    example_data["variables"][0]["sources"].append(
        {"source": "nso_uzb", "code": "x", "unit": "constant 2015 US$", "currency": "USD",
         "price_basis": "constant", "base_year": 2015})
    code, out = run(capsys, "fetch", str(small_spec(example_data, write_spec, keep=("va_agr_real",))))
    assert "missing for TKM: 2000-2023" in out
    assert "skipped: source 'nso_uzb' is added in Phase 5" in out


def test_search_prints_codes_units_and_definitions(capsys, offline):
    code, out = run(capsys, "search", "wdi", "gross fixed capital formation", "--limit", "2")
    assert code == 0
    assert "NE.GDI.FTOT" in out and "unit: constant" in out and "acquisitions less disposals" in out


def test_request_without_a_key_names_the_line_to_fill(capsys, monkeypatch):
    from pipeline import envfile

    def no_key(name):
        raise envfile.MissingKeyError(f"{name} on line 5 of .env is still the placeholder.")
    monkeypatch.setattr(envfile, "get_key", no_key)
    code, out = run(capsys, "request", REQUEST)
    assert code == 1 and "line 5 of .env" in out


def test_request_writes_a_draft_and_review(capsys, monkeypatch, offline, tmp_path):
    import anthropic

    from pipeline import envfile

    monkeypatch.setattr(envfile, "get_key", lambda name: "test-key-not-real")
    claude = FakeClaude([reply(tool_use("submit_spec", GOOD, "t1"))])
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: claude)
    monkeypatch.setattr(cli, "SPECS_DIR", tmp_path)
    code, out = run(capsys, "request", REQUEST)
    assert code == 0, out
    draft = tmp_path / "uzb_kaz_test.yaml"
    assert draft.exists() and (tmp_path / "uzb_kaz_test.review.md").exists()
    assert "test-key-not-real" not in out
    code, out = run(capsys, "check-spec", str(draft))
    assert code == 0 and "DRAFT" in out
    # A second draft never overwrites the first.
    claude.replies.append(reply(tool_use("submit_spec", GOOD, "t2")))
    run(capsys, "request", REQUEST)
    assert (tmp_path / "uzb_kaz_test_2.yaml").exists()


def test_build_refuses_a_draft_spec(capsys):
    code, out = run(capsys, "build", str(EXAMPLE_SPEC))
    assert code == 1
    assert "still a draft, so nothing was built" in out


def test_build_on_a_confirmed_spec_says_outputs_come_later(capsys, example_data, write_spec):
    example_data["confirmed"] = True
    code, out = run(capsys, "build", str(write_spec(example_data)))
    assert code == 1 and "Phases 3 and 4" in out


def test_check_setup_never_prints_keys(tmp_path):
    secret = "test-value-not-a-real-key-456"
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={secret}\nCOMTRADE_API_KEY=paste-your-key-here\n", encoding="utf-8")
    text, ok = setup_check.report(env_file=env)
    assert ok
    assert secret not in text
    assert "ANTHROPIC_API_KEY in .env: set" in text
    assert "COMTRADE_API_KEY in .env: not set (optional)" in text


def test_module_runs_from_the_command_line():
    result = subprocess.run(
        [sys.executable, "-m", "pipeline", "--help"], cwd=PROJECT_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0
    for command in ("check-setup", "request", "search", "check-spec", "fetch", "build"):
        assert command in result.stdout
