"""What is installed and configured, for `check-setup`. Never prints key values."""

import glob
import shutil
import sys
from pathlib import Path

from .config import load_config
from .envfile import key_status
from .paths import CONFIG_FILE, ENV_FILE, PROJECT_ROOT

_KEY_TEXT = {
    "set": "set",
    "placeholder": "still the placeholder - paste your key into .env",
    "missing": "no line for it in .env",
    "no_env_file": "there is no .env file",
}


def find_rscript() -> Path | None:
    found = shutil.which("Rscript")
    if found:
        return Path(found)
    hits = sorted(glob.glob(r"C:\Program Files*\R\R-*\bin\x64\Rscript.exe"))
    return Path(hits[-1]) if hits else None


def find_stata() -> Path | None:
    for drive in ("C", "D"):
        hits = sorted(glob.glob(rf"{drive}:\Program Files*\Stata*\Stata*-64.exe"))
        if hits:
            return Path(hits[-1])
    return None


def report(env_file: Path = ENV_FILE, config_file: Path = CONFIG_FILE) -> tuple[str, bool]:
    ok = True
    lines = []

    version = ".".join(map(str, sys.version_info[:3]))
    in_venv = Path(sys.prefix).resolve() == (PROJECT_ROOT / ".venv").resolve()
    python_ok = sys.version_info >= (3, 12)
    ok &= python_ok
    lines.append(f"Python {version}: {'OK' if python_ok else 'needs 3.12 or newer'}"
                 f"{'' if in_venv else ' (not the project environment in .venv)'}")

    try:
        config = load_config(config_file)
        lines.append(f"config.yaml: OK (spec drafting model: {config.models.spec_drafting})")
    except Exception as exc:  # report any problem in plain words rather than crash
        ok = False
        lines.append(f"config.yaml: problem - {exc}")

    lines.append(f"ANTHROPIC_API_KEY in .env: {_KEY_TEXT[key_status('ANTHROPIC_API_KEY', env_file)]}"
                 " (needed for the request step)")
    comtrade = key_status("COMTRADE_API_KEY", env_file)
    lines.append(f"COMTRADE_API_KEY in .env: {'set' if comtrade == 'set' else 'not set (optional)'}")

    r = find_rscript()
    lines.append(f"R: {r if r else 'not found (needed to run load_and_check.R and growth_accounting.R)'}")
    stata_on = False
    try:
        stata_on = load_config(config_file).outputs.stata
    except Exception:
        pass
    if stata_on:
        stata = find_stata()
        lines.append(f"Stata: {stata if stata else 'not found'} (Stata outputs are on in config.yaml)")
    return "\n".join(lines), ok
