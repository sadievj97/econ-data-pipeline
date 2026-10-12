"""Fixed locations inside the project folder."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config.yaml"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"
SPECS_DIR = PROJECT_ROOT / "specs"
EVENTS_FILE = PROJECT_ROOT / "known_events.yaml"
