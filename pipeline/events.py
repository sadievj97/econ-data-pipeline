"""Load the list of known events (currency regime changes, national accounts revisions)."""

from __future__ import annotations

from datetime import date as Date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .paths import EVENTS_FILE


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iso3: str = Field(pattern=r"^[A-Z]{3}$")
    date: Date
    kind: Literal["currency_regime", "national_accounts_revision", "other"]
    description: str = Field(min_length=1)
    source: str = Field(min_length=1)


class EventList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[Event] = Field(default_factory=list)


def load_events(path: Path = EVENTS_FILE) -> list[Event]:
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    return sorted(EventList.model_validate(data).events, key=lambda e: (e.iso3, e.date))
