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


class HttpSettings(_Strict):
    timeout_seconds: float = Field(120.0, gt=0)
    attempts: int = Field(4, ge=1)


class Thresholds(_Strict):
    growth: float = Field(1.0, gt=0)
    level: float = Field(5.0, gt=0)


class PlausibleRanges(_Strict):
    share: tuple[float, float] = (0.0, 1.0)
    percent: tuple[float, float] = (0.0, 100.0)
    growth_rate: tuple[float, float] = (-50.0, 100.0)


class JumpThresholds(_Strict):
    level_log_change: float = Field(0.4, gt=0)
    percent_points: float = Field(10.0, gt=0)
    share_points: float = Field(0.1, gt=0)


class CheckSettings(_Strict):
    comparison_threshold: Thresholds = Thresholds()
    plausible_range: PlausibleRanges = PlausibleRanges()
    jump: JumpThresholds = JumpThresholds()
    repeated_run: int = Field(3, ge=2)


class GrowthSettings(_Strict):
    sensitivity_shares: list[float] = Field(default_factory=lambda: [0.5, 0.6, 0.7])


class OutputSettings(_Strict):
    stata: bool = False  # also write panel.dta and the .do scripts


class PathSettings(_Strict):
    raw_cache: str = "data/raw"
    manual: str = "data/manual"
    outputs: str = "out"


class Config(_Strict):
    models: ModelSettings
    http: HttpSettings = HttpSettings()
    checks: CheckSettings = CheckSettings()
    growth_accounting: GrowthSettings = GrowthSettings()
    outputs: OutputSettings = OutputSettings()
    paths: PathSettings = PathSettings()

    def folder(self, name: Literal["raw_cache", "manual", "outputs"]) -> Path:
        return PROJECT_ROOT / getattr(self.paths, name)


def load_config(path: Path = CONFIG_FILE) -> Config:
    with path.open(encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
