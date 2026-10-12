"""An ordered record of every step the build takes, written out as data_log.md.

Entries never include the time of the build itself, only facts about the data,
so a rebuild from the same cached files gives an identical log.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    title: str
    variable: str | None
    lines: list[str] = field(default_factory=list)


class DataLog:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    def add(self, title: str, *lines: str, variable: str | None = None) -> None:
        self.steps.append(Step(title, variable, list(lines)))

    def markdown(self, heading: str) -> str:
        out = [f"# Data log: {heading}", "", "Every step of the build, in order.", ""]
        for n, step in enumerate(self.steps, start=1):
            prefix = f"`{step.variable}`: " if step.variable else ""
            out.append(f"{n}. {prefix}**{step.title}**")
            out += [f"   - {line}" for line in step.lines]
        out.append("")
        return "\n".join(out)
