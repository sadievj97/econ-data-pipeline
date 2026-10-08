import pytest
from pydantic import ValidationError

from pipeline.config import Config, load_config


def test_project_config_loads():
    config = load_config()
    assert config.models.spec_drafting
    assert config.folder("outputs").name == "out"


def test_config_rejects_unknown_settings():
    with pytest.raises(ValidationError):
        Config.model_validate({"models": {"spec_drafting": "m", "temprature": 0}})
