import copy
from pathlib import Path

import pytest
import yaml

from pipeline.paths import SPECS_DIR

EXAMPLE_SPEC = SPECS_DIR / "example_uzb_kaz.yaml"


@pytest.fixture
def example_data() -> dict:
    """The example spec as plain data, fresh for each test so tests can edit it."""
    return copy.deepcopy(yaml.safe_load(EXAMPLE_SPEC.read_text(encoding="utf-8")))


@pytest.fixture
def write_spec(tmp_path: Path):
    def _write(data: dict, name: str = "spec.yaml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    return _write
