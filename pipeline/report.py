"""Write validation_report.md and missing_data.csv.

Nothing written here depends on when the build runs, so a rebuild from the
same raw files gives byte-identical files.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict

from .harmonize import Panel, year_ranges
from .numbers import fmt, plural
from .validate import ValidationReport


def _range(pair) -> str:
    return f"[{pair[0]:g}, {pair[1]:g}]"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    def clean(cell: str) -> str:
        return str(cell).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(clean(c) for c in row) + " |" for row in rows]
    return out


def validation_markdown(panel: Panel, report: ValidationReport, retrieved: list[str]) -> str:
    spec = panel.spec
    errors, flags = report.errors, report.flags
    status = (f"**FAILED**: {len(errors)} error(s) must be fixed before outputs are written."
              if errors else f"**PASSED**: no errors; {len(flags)} flag(s) to look at.")
    out = [f"# Validation report: {spec.title}", "",
           f"Spec `{spec.name}`: {', '.join(panel.countries)}, {panel.years[0]}-{panel.years[-1]}, "
           f"{len(spec.variables)} variables.",
           f"Raw files retrieved: {', '.join(retrieved) if retrieved else 'none'}.", "", status, ""]

    out += ["## Errors", ""]
    if errors:
        out += _table(["check", "variable", "country", "problem"],
                      [[f.check, f.variable or "", f.iso3 or "", f.message] for f in errors])
    else:
        out.append("None.")
    out.append("")

    out += ["## Known values", ""]
    if report.known:
        out += _table(["variable", "country", "year", "expected", "panel", "result", "source"],
                      [[k.known.variable, k.known.iso3, str(k.known.year), fmt(k.known.value),
                        fmt(k.actual, panel.results[k.known.variable].precision) if k.actual is not None else "missing",
                        ("OK, " if k.matched else "MISMATCH, ") + k.note, k.known.source] for k in report.known])
    else:
        out.append("No known-values file next to the spec, so none were checked.")
    out.append("")

    out += ["## Source comparisons", ""]
    if report.comparisons:
        rows = []
        for c in report.comparisons:
            unit = "pp" if c.compare == "growth" else "%"
            rows.append([f"{c.a} vs {c.b}", c.origin, c.compare, f"{c.threshold:g} {unit}", str(c.overlap),
                         f"{c.max_abs:.3f} {unit}" if c.max_abs is not None else "no overlap",
                         str(len(c.above))])
        out += _table(["comparison", "from", "compares", "threshold", "years compared", "largest difference",
                       "years above threshold"], rows)
        out += ["", "Years above the threshold are listed under Flags."]
    else:
        out.append("No comparisons: the spec lists none, and no variable has a fallback source with data.")
    out.append("")

    out += ["## Flags", "", "Possible breaks and differences between sources. Values are never changed.", ""]
    if flags:
        out += _table(["check", "variable", "country", "what"],
                      [[f.check, f.variable or "", f.iso3 or "", f.message] for f in flags])
    else:
        out.append("None.")
    out.append("")

    out += ["## Known events in these countries and years", ""]
    if report.events:
        out += _table(["country", "date", "kind", "event", "source"],
                      [[e.iso3, e.date.isoformat(), e.kind, e.description, e.source] for e in report.events])
    else:
        out.append("None listed in known_events.yaml.")
    out.append("")

    out += ["## Missing data", ""]
    missing = report.missing
    rows = []
    for name, r in panel.results.items():
        for c in r.countries:
            years = missing[(missing["variable"] == name) & (missing["iso3"] == c)]["year"].tolist()
            if years:
                rows.append([name, c, f"{len(years)} of {len(r.years)}", year_ranges(sorted(years))])
    not_built = [v.name for v in spec.variables if v.name not in panel.results]
    if rows:
        out += _table(["variable", "country", "missing", "years"], rows)
        out += ["", "Every other variable has a value for every country and year."]
    elif panel.results:
        out.append("No missing values: every variable has a value for every country and year.")
    if not_built:
        out += ["", f"Not built at all (see Errors): {', '.join(not_built)}."]
    by_year: dict[int, int] = defaultdict(int)
    for year in missing["year"].tolist():
        by_year[int(year)] += 1
    cells = len(panel.results) * len(panel.countries)
    out += ["", "Missing country-variable cells by year: "
            + (", ".join(f"{y}: {by_year[y]} of {cells}" for y in panel.years if by_year[y]) or "none") + ".", ""]

    s = report.settings
    out += ["## Checks run", "",
            "- Unique country-year keys in every source and in the panel.",
            "- One unit, currency, price basis and base year per variable.",
            f"- Plausible ranges: shares {_range(s['plausible_range']['share'])}, "
            f"percents {_range(s['plausible_range']['percent'])}, "
            f"growth rates {_range(s['plausible_range']['growth_rate'])}; levels not negative; indexes above zero; "
            "a variable's valid_range overrides these.",
            f"- Comparisons: fallback sources are compared with the source used, with a growth threshold of "
            f"{plural(s['comparison_threshold']['growth'], 'percentage point')}; spec comparisons use their own thresholds.",
            f"- Jumps: |log change| above {s['jump']['level_log_change']:g} for levels and indexes, "
            f"{s['jump']['percent_points']:g} points for percents, {s['jump']['share_points']:g} for shares.",
            f"- Runs of {s['repeated_run']} or more identical values.",
            "- Known values (exact, at the source's own precision) and known events from known_events.yaml.", ""]
    return "\n".join(out)


def missing_csv(report: ValidationReport) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["variable", "iso3", "year"])
    for row in report.missing.itertuples(index=False):
        writer.writerow([row.variable, row.iso3, int(row.year)])
    return buffer.getvalue()
