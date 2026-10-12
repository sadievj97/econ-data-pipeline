"""The build: fetch, harmonize, validate, and write the reports for a confirmed spec.

The first build of a spec writes <spec>.lock.json next to it, naming the exact
raw snapshot of every file used. Later builds use those snapshots, so the same
spec always gives the same results, until you rebuild with --refresh.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .cache import Snapshot
from .config import Config
from .connectors import Sources
from .datalog import DataLog
from .events import Event
from .fetching import fetch_spec
from .harmonize import Panel, harmonize
from .known_values import known_values_path, load_known_values
from .outputs import remove_stale_outputs, write_outputs
from .report import missing_csv, validation_markdown
from .spec import Spec
from .validate import Finding, ValidationReport, validate

LOCK_VERSION = 1


def lock_path(spec_path: Path) -> Path:
    return spec_path.with_name(f"{spec_path.stem}.lock.json")


def read_lock(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: entry["snapshot"] for key, entry in data["snapshots"].items()}


def lock_text(spec: Spec, used: dict[str, Snapshot]) -> str:
    data = {
        "lock_version": LOCK_VERSION,
        "spec": spec.name,
        "about": "Raw snapshots this spec was built from. Rebuild with --refresh to download again.",
        "snapshots": {key: {"snapshot": s.id, "retrieved_at": s.retrieved_at, "sha256": s.sha256}
                      for key, s in sorted(used.items())},
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


@dataclass
class BuildResult:
    spec: Spec
    panel: Panel
    report: ValidationReport
    out_dir: Path
    written: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    lock_file: Path | None = None
    lock_changed: bool = False


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def run_build(spec_path: Path, spec: Spec, sources: Sources, config: Config, events: list[Event],
              refresh: bool = False, progress: Callable[[str], None] = lambda _: None) -> BuildResult:
    log = DataLog()
    progress("Fetching (reusing cached raw files)..." if not refresh else "Fetching (downloading again)...")
    reports = fetch_spec(spec, sources, refresh, progress)

    fetched, extra = {}, []
    for r in reports:
        if r.skipped:
            log.add("Source not available yet", f"{r.ref.source} {r.ref.code}: {r.skipped}", variable=r.variable)
        for e in r.errors:
            extra.append(Finding("error", "fetch", r.variable, None, f"{r.ref.source} {r.ref.code}: {e}"))
        for w in r.warnings:
            extra.append(Finding("flag", "units", r.variable, None, f"{r.ref.source} {r.ref.code}: {w}"))
        if r.series is not None and not r.errors:
            fetched[(r.variable, r.index)] = r.series

    used = dict(sorted(sources.cache.used.items()))
    log.add("Raw files used", *[f"{s.relative_file()} (snapshot {s.id}, retrieved {s.retrieved_at}, "
                                f"sha256 {s.sha256[:12]}...)" for s in used.values()])

    progress("Harmonizing...")
    panel = harmonize(spec, fetched, log)

    progress("Validating...")
    known = load_known_values(known_values_path(spec_path))
    report = validate(panel, config, events, known, extra)
    log.add("Validation", f"{len(report.errors)} error(s), {len(report.flags)} flag(s)",
            f"known values checked: {len(report.known)}, matched: {sum(k.matched for k in report.known)}",
            f"missing country-years across all variables: {len(report.missing)}")

    out_dir = config.folder("outputs") / spec.name
    out_dir.mkdir(parents=True, exist_ok=True)
    retrieved = sorted({s.retrieved_at for s in used.values()}) or ["2000-01-01T00:00:00Z"]
    result = BuildResult(spec, panel, report, out_dir)
    result.written += [
        _write(out_dir / "validation_report.md", validation_markdown(panel, report, sorted({r[:10] for r in retrieved}))),
        _write(out_dir / "missing_data.csv", missing_csv(report)),
    ]
    if report.errors:
        result.removed = remove_stale_outputs(out_dir)
        log.add("Outputs", "not written, because validation found errors"
                + (f"; removed panel files left by an earlier build: {', '.join(p.name for p in result.removed)}"
                   if result.removed else ""))
    else:
        progress("Writing outputs...")
        result.written += write_outputs(panel, report, config, out_dir, retrieved, log)
    result.written.append(_write(out_dir / "data_log.md", log.markdown(spec.title)))

    if not any(f.check == "fetch" for f in report.errors):
        path = lock_path(spec_path)
        text = lock_text(spec, used)
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if text != old:
            _write(path, text)
            result.lock_changed = True
        result.lock_file = path
    return result
