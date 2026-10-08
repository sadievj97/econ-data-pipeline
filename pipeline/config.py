"""Load config.yaml: model IDs and folder locations."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .paths import CONFIG_FILE, PROJECT_ROOT


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSettings(_Strict):
    spec_drafting: str = Field(min_length=1)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_tokens: int = Field(16000, gt=0)


class PathSettings(_Strict):
    raw_cache: str = "data/raw"
    manual: str = "data/manual"
    outputs: str = "out"


class Config(_Strict):
    models: ModelSettings
    paths: PathSettings = PathSettings()

    def folder(self, name: Literal["raw_cache", "manual", "outputs"]) -> Path:
        return PROJECT_ROOT / getattr(self.paths, name)


def load_config(path: Path = CONFIG_FILE) -> Config:
    with path.open(encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
