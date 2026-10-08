"""Read keys from .env without ever printing or logging them.

Error messages name the variable and the line to fill in, never the value.
"""

from pathlib import Path
from typing import Literal

from .paths import ENV_FILE

PLACEHOLDER = "paste-your-key-here"

KeyStatus = Literal["set", "placeholder", "missing", "no_env_file"]


class MissingKeyError(RuntimeError):
    pass


def _parse(path: Path) -> dict[str, tuple[int, str]]:
    """Return {NAME: (line_number, value)} for each NAME=value line."""
    entries: dict[str, tuple[int, str]] = {}
    # utf-8-sig tolerates the byte-order mark some editors add.
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        entries[name.strip()] = (number, value)
    return entries


def key_status(name: str, path: Path = ENV_FILE) -> KeyStatus:
    if not path.exists():
        return "no_env_file"
    entry = _parse(path).get(name)
    if entry is None:
        return "missing"
    value = entry[1]
    if not value or value == PLACEHOLDER:
        return "placeholder"
    return "set"


def get_key(name: str, path: Path = ENV_FILE) -> str:
    if not path.exists():
        raise MissingKeyError(
            f"There is no .env file in {path.parent}. "
            "Copy the lines from .env.example into a new file named .env, then paste your key."
        )
    entry = _parse(path).get(name)
    if entry is None:
        raise MissingKeyError(f".env has no {name} line. Add a line that reads {name}= followed by your key.")
    number, value = entry
    if not value or value == PLACEHOLDER:
        raise MissingKeyError(
            f"{name} on line {number} of .env is still the placeholder. "
            f"Open .env in Notepad, paste your key right after {name}= and press Ctrl+S."
        )
    return value
