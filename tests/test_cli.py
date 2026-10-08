import subprocess
import sys

from pipeline import setup_check
from pipeline.cli import main
from pipeline.paths import PROJECT_ROOT

from .conftest import EXAMPLE_SPEC


def run(capsys, *args):
    code = main(list(args))
    return code, capsys.readouterr().out


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


def test_build_refuses_a_draft_spec(capsys):
    code, out = run(capsys, "build", str(EXAMPLE_SPEC))
    assert code == 1
    assert "still a draft, so nothing was built" in out


def test_build_on_a_confirmed_spec_says_fetching_is_not_built_yet(capsys, example_data, write_spec):
    example_data["confirmed"] = True
    code, out = run(capsys, "build", str(write_spec(example_data)))
    assert code == 1 and "Phase 2" in out


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
    for command in ("check-setup", "request", "check-spec", "build"):
        assert command in result.stdout
