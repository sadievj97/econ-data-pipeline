"""Write the panel files, the codebook, and the R and Stata scripts for a validated build.

Everything here is deterministic: the same harmonized panel always gives the
same bytes. The one thing a library would otherwise vary, the "saved at" stamp
inside a .dta file, is set to the retrieval time of the newest raw file used.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyreadstat

from .config import Config
from .datalog import DataLog
from .harmonize import Panel
from .numbers import fmt
from .sectors import sector_label
from .spec import country_name
from .validate import ValidationReport, range_rule

PANEL_FILES = ("panel_long.csv", "panel_wide.csv", "panel.parquet", "codebook.md", "codebook.csv", "load_and_check.R")
GROWTH_FILES = ("growth_accounting.md", "growth_accounting_annual.csv", "growth_accounting_summary.csv",
                "growth_accounting.R")
STATA_FILES = ("panel.dta", "load_and_check.do", "growth_accounting.do")  # only when outputs.stata is true
KEY_LABELS = {"iso3": "ISO 3166-1 alpha-3 country code", "country": "Country name", "year": "Year"}
SOURCE_NAMES = {
    "wdi": "World Bank, World Development Indicators",
    "pwt": "Penn World Table 11.0, Groningen Growth and Development Centre",
}
CITATIONS = {
    "wdi": "World Bank, World Development Indicators, via the World Bank API (api.worldbank.org/v2). "
           "License: CC BY 4.0.",
    "pwt": "Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer (2015), \"The Next Generation of the Penn "
           "World Table\", American Economic Review, 105(10), 3150-3182. Data: PWT 11.0, doi:10.34894/FABVLR. "
           "License: CC BY 4.0.",
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
CODEBOOK_COLUMNS = ["variable", "label", "sector", "sector_label", "measure", "unit", "currency", "price_basis",
                    "base_year", "precision", "source", "code", "dataset", "source_name", "definition",
                    "countries_from_source", "retrieved_at", "source_last_updated", "raw_files", "raw_sha256",
                    "transforms", "observations", "missing", "first_year", "last_year", "filled_by_transforms",
                    "source_notes"]


# ------------------------------------------------------------- tables


def _csv(header: list[str], rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _cell(value: float, precision: str) -> str:
    """The exact stored value, so the CSV, parquet and .dta files hold identical numbers."""
    return "" if np.isnan(value) else fmt(value)


def long_rows(panel: Panel) -> list[list[Any]]:
    names = {c: country_name(c) or "" for c in panel.countries}
    rows = []
    for name, r in panel.results.items():
        for ci, c in enumerate(r.countries):
            for yi, year in enumerate(r.years):
                rows.append([name, c, names[c], year, _cell(r.values[ci, yi], r.precision), r.flags[ci][yi],
                             r.note(ci, yi)])
    return rows


def wide_frame(panel: Panel) -> pd.DataFrame:
    keys = [(c, y) for c in panel.countries for y in panel.years]
    data: dict[str, Any] = {
        "iso3": [c for c, _ in keys],
        "country": [country_name(c) or "" for c, _ in keys],
        "year": np.array([y for _, y in keys], dtype="int32"),
    }
    for name, r in panel.results.items():
        data[name] = r.values.reshape(-1).astype("float64")  # rows are country-major, years ascending
    return pd.DataFrame(data)


def wide_csv(panel: Panel) -> str:
    frame = wide_frame(panel)
    names = list(panel.results)
    rows = []
    for i in range(len(frame)):
        row = [frame["iso3"][i], frame["country"][i], int(frame["year"][i])]
        row += [_cell(frame[n][i], panel.results[n].precision) for n in names]
        rows.append(row)
    return _csv(["iso3", "country", "year", *names], rows)


# ----------------------------------------------------------- codebook


def codebook_rows(panel: Panel) -> list[dict[str, str]]:
    out = []
    for name, r in panel.results.items():
        v = r.variable
        used = sorted({i for i in r.source_for.values() if i is not None}) or [0]
        sources, codes, datasets, names, definitions, scopes = [], [], [], [], [], []
        retrieved, updated, files, shas = set(), set(), [], []
        for i in used:
            ref = v.sources[i]
            series = panel.fetched.get((name, i))
            sources.append(ref.source)
            codes.append(ref.code)
            datasets.append(ref.dataset or "")
            scopes.append(f"{ref.source} {ref.code}: " + (", ".join(c for c in r.countries if r.source_for[c] == i)
                                                          or "no country (no data)"))
            if series is not None:
                names.append(series.info.name)
                definitions.append(series.info.definition)
                for snap in series.raw:
                    retrieved.add(snap.retrieved_at)
                    if snap.meta.get("source_last_updated"):
                        updated.add(snap.meta["source_last_updated"])
                    if snap.relative_file() not in files:
                        files.append(snap.relative_file())
                        shas.append(snap.sha256)
        present = ~np.isnan(r.values)
        years_with_data = [r.years[i] for i in np.flatnonzero(present.any(axis=0))]
        out.append({
            "variable": name,
            "label": v.label,
            "sector": v.sector or "",
            "sector_label": sector_label(v.sector) if v.sector else "economy-wide",
            "measure": v.measure,
            "unit": r.unit["unit"],
            "currency": r.unit["currency"] or "",
            "price_basis": r.unit["price_basis"],
            "base_year": "" if r.unit["base_year"] is None else str(r.unit["base_year"]),
            "precision": r.precision,
            "source": "; ".join(sources),
            "code": "; ".join(codes),
            "dataset": "; ".join(d for d in datasets if d),
            "source_name": "; ".join(names),
            "definition": " | ".join(dict.fromkeys(definitions)),
            "countries_from_source": "; ".join(scopes),
            "retrieved_at": "; ".join(sorted(retrieved)),
            "source_last_updated": "; ".join(sorted(updated)),
            "raw_files": "; ".join(files),
            "raw_sha256": "; ".join(shas),
            "transforms": "; ".join(r.transforms) or "none",
            "observations": str(int(present.sum())),
            "missing": str(int((~present).sum())),
            "first_year": str(years_with_data[0]) if years_with_data else "",
            "last_year": str(years_with_data[-1]) if years_with_data else "",
            "filled_by_transforms": str(sum(1 for row in r.flags for f in row if f)),
            "source_notes": "; ".join(f"{n} ({k})" for n, k in sorted(_note_counts(r).items())),
        })
    return out


def _note_counts(r) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ci in range(len(r.countries)):
        for yi in range(len(r.years)):
            note = r.note(ci, yi)
            if note and not np.isnan(r.values[ci, yi]):
                counts[note] = counts.get(note, 0) + 1
    return counts


def codebook_csv(rows: list[dict[str, str]]) -> str:
    return _csv(CODEBOOK_COLUMNS, [[row[c] for c in CODEBOOK_COLUMNS] for row in rows])


def codebook_markdown(panel: Panel, rows: list[dict[str, str]], report: ValidationReport, stata: bool = False) -> str:
    spec = panel.spec
    countries = ", ".join(f"{c} ({country_name(c)})" for c in panel.countries)
    out = [f"# Codebook: {spec.title}", "",
           f"Built from spec `{spec.name}`. Countries: {countries}. Years: {panel.years[0]}-{panel.years[-1]}. "
           f"{len(rows)} variables. The wide files have one row per country and year.", "",
           "## Files", "",
           "| file | contents |", "|---|---|",
           "| panel_wide.csv | one row per country-year, one column per variable |",
           "| panel_long.csv | one row per country-year-variable, with a `flag` column naming any transform that "
           "created the value (empty means exactly as published) and a `source_note` column with the source's own "
           "remark on the value (for example IMF projection, break in series, imputed value) |",
           "| panel.parquet | the long panel with typed columns; this codebook is stored in its metadata |"]
    if stata:
        out.append("| panel.dta | the wide panel for Stata 14 or newer, with variable labels |")
    out += ["| codebook.md, codebook.csv | this codebook, as text and as a table |",
           "| validation_report.md, missing_data.csv | the checks run and every missing country-year |",
           "| data_log.md | every step of the build, in order |",
           "| load_and_check.R | loads the panel in R as `panel` (wide), `panel_long` and `codebook`, applies labels "
           "and units, and reruns the checks |"]
    if stata:
        out.append("| load_and_check.do | the same for Stata |")
    out += ["",
           "## Keys", "",
           "- `iso3`: ISO 3166-1 alpha-3 country code. `country`: country name (ISO 3166). `year`: calendar year.",
           "- Each `iso3`-`year` pair appears once in the wide files; every spec country and year is present, "
           "with empty values where a source has no data.", "",
           "## Variables", ""]
    for row in rows:
        basis = row["price_basis"].replace("_", " ")
        if row["base_year"]:
            basis += f", base year {row['base_year']}"
        precision = ("4-byte float, about 7 significant digits (as the source stores it)"
                     if row["precision"] == "float32" else "8-byte double")
        out += [f"### `{row['variable']}`", "", row["label"], "",
                "| | |", "|---|---|",
                f"| Sector | {row['sector_label']} |",
                f"| Measure | {row['measure']} |",
                f"| Unit | {row['unit']} |",
                f"| Currency | {row['currency'] or 'none'} |",
                f"| Price basis | {basis} |",
                f"| Source | {row['countries_from_source']} |",
                f"| Name at the source | {row['source_name']} |",
                f"| Retrieved | {row['retrieved_at']}"
                + (f" (source last updated {row['source_last_updated']})" if row["source_last_updated"] else "") + " |",
                f"| Raw files | {row['raw_files'].replace('; ', '<br>')} |",
                f"| Transforms | {row['transforms']} |",
                f"| Source notes | {row['source_notes'] or 'none'} |",
                f"| Values | {row['observations']} present, {row['missing']} missing; years with data "
                f"{row['first_year'] or 'none'}-{row['last_year'] or 'none'}; {row['filled_by_transforms']} "
                "created by transforms |",
                f"| Precision | {precision} |", "",
                f"Definition (from the source): {row['definition'] or 'none given'}", ""]
    used_sources = sorted({s for row in rows for s in row["source"].split("; ") if s})
    out += ["## Sources and citations", ""]
    out += [f"- {CITATIONS.get(s, SOURCE_NAMES.get(s, s))}" for s in used_sources]
    out += ["", "## Notes", "",
            "- Values are exactly as the source published them unless a variable's Transforms line says otherwise.",
            "- Some sources (PWT) store values as 4-byte floats. Every output file holds the value exactly as stored, "
            "written as a double (for example 0.37945353984832764), so the CSV, parquet and .dta files agree; only "
            "about 7 significant digits of such values are meaningful (0.37945354).",
            f"- Validation: {len(report.errors)} errors, {len(report.flags)} flags; see validation_report.md.", ""]
    return "\n".join(out)


# ----------------------------------------------------------- binary files


def write_parquet(panel: Panel, rows: list[dict[str, str]], path: Path) -> None:
    names = {c: country_name(c) or "" for c in panel.countries}
    variables, iso3, countries, years, values, flags, notes = [], [], [], [], [], [], []
    for name, r in panel.results.items():
        for ci, c in enumerate(r.countries):
            for yi, year in enumerate(r.years):
                variables.append(name)
                iso3.append(c)
                countries.append(names[c])
                years.append(year)
                values.append(r.values[ci, yi])
                flags.append(r.flags[ci][yi] or None)
                notes.append(r.note(ci, yi) or None)
    vals = np.array(values, dtype="float64")
    table = pa.table({
        "variable": pa.array(variables, pa.string()),
        "iso3": pa.array(iso3, pa.string()),
        "country": pa.array(countries, pa.string()),
        "year": pa.array(years, pa.int32()),
        "value": pa.array(vals, pa.float64(), mask=np.isnan(vals)),
        "flag": pa.array(flags, pa.string()),
        "source_note": pa.array(notes, pa.string()),
    })
    meta = {"spec": panel.spec.name, "title": panel.spec.title, "codebook": rows}
    table = table.replace_schema_metadata({"econ_pipeline": json.dumps(meta, sort_keys=True, ensure_ascii=False)})
    pq.write_table(table, path, compression="snappy")


def dta_timestamp(retrieved: list[str]) -> str:
    """'dd Mon yyyy hh:mm' from the newest retrieval time, e.g. '09 Oct 2026 18:49'."""
    when = datetime.strptime(max(retrieved), "%Y-%m-%dT%H:%M:%SZ")
    return f"{when.day:02d} {MONTHS[when.month - 1]} {when.year} {when.hour:02d}:{when.minute:02d}"


def _set_dta_timestamp(path: Path, stamp: str) -> None:
    data = path.read_bytes()
    tag, end = b"<timestamp>", b"</timestamp>"
    i = data.find(tag)
    start = i + len(tag)
    if i < 0 or data[start] != 17 or data[start + 18:start + 18 + len(end)] != end or len(stamp) != 17:
        raise RuntimeError(f"{path.name}: unexpected .dta header, so its timestamp cannot be made reproducible")
    path.write_bytes(data[:start + 1] + stamp.encode("ascii") + data[start + 18:])


def dta_label(title: str) -> str:
    return title if len(title) <= 80 else title[:77].rstrip() + "..."


def write_dta(panel: Panel, path: Path, stamp: str) -> None:
    frame = wide_frame(panel)
    labels = [KEY_LABELS["iso3"], KEY_LABELS["country"], KEY_LABELS["year"]]
    labels += [r.variable.label for r in panel.results.values()]
    pyreadstat.write_dta(frame, str(path), file_label=dta_label(panel.spec.title), column_labels=labels, version=14)
    _set_dta_timestamp(path, stamp)


# ------------------------------------------------------- R and Stata scripts


def _r_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _stata_str(s: str) -> str:
    """Compound quotes, with $ and ` escaped so Stata never expands them as macros."""
    return '`"' + s.replace("$", "\\$").replace("`", "\\`").replace("\n", " ") + '"\''


def _num(x: float) -> str:
    return repr(float(x))


def r_script(panel: Panel, rows: list[dict[str, str]], report: ValidationReport, config: Config) -> str:
    spec = panel.spec
    names = list(panel.results)
    missing = {row["variable"]: int(row["missing"]) for row in rows}
    col_classes = ", ".join([f"{n} = {_r_str(t)}" for n, t in
                             (("iso3", "character"), ("country", "character"), ("year", "integer"))]
                            + [f"{n} = \"numeric\"" for n in names])
    out = [
        f"# load_and_check.R for spec {spec.name}: written by the econ data pipeline. Do not edit; rebuild instead.",
        "# Loads panel_wide.csv into a data frame called `panel`, applies variable labels and units,",
        "# and reruns the validation checks. Each check stops with an error if it fails.",
        "# Run it with:  Rscript load_and_check.R   (or open it in RStudio and click Source).",
        "# Uses base R only.",
        "",
        "script_dir <- function() {",
        "  args <- commandArgs(trailingOnly = FALSE)",
        "  file <- sub(\"^--file=\", \"\", args[grepl(\"^--file=\", args)])",
        "  if (length(file) == 1) return(dirname(normalizePath(file)))",
        "  for (i in rev(seq_len(sys.nframe()))) {",
        "    f <- sys.frame(i)$ofile",
        "    if (!is.null(f)) return(dirname(normalizePath(f)))",
        "  }",
        "  getwd()",
        "}",
        "here <- script_dir()",
        "",
        "check <- function(ok, what) {",
        "  if (!isTRUE(ok)) stop(paste(\"CHECK FAILED:\", what), call. = FALSE)",
        "  cat(\"ok  \", what, \"\\n\", sep = \"\")",
        "}",
        "",
        f"panel <- read.csv(file.path(here, \"panel_wide.csv\"), colClasses = c({col_classes}),",
        "                  na.strings = \"\", check.names = FALSE, fileEncoding = \"UTF-8\")",
        "",
        "# Labels and units",
        f"attr(panel, \"label\") <- {_r_str(spec.title)}",
    ]
    for key, label in KEY_LABELS.items():
        out.append(f"attr(panel${key}, \"label\") <- {_r_str(label)}")
    for row in rows:
        n = row["variable"]
        out.append(f"attr(panel${n}, \"label\") <- {_r_str(row['label'])}")
        out.append(f"attr(panel${n}, \"unit\") <- {_r_str(row['unit'])}")
    out += ["", "# Structure",
            f"check(identical(names(panel), c({', '.join(_r_str(n) for n in ['iso3', 'country', 'year', *names])})),"
            " \"columns are as expected\")",
            "check(!anyDuplicated(panel[c(\"iso3\", \"year\")]), \"each country-year appears once\")",
            f"check(nrow(panel) == {len(panel.countries) * len(panel.years)}L, "
            f"\"{len(panel.countries)} countries x {len(panel.years)} years\")",
            f"check(identical(sort(unique(panel$iso3)), c({', '.join(_r_str(c) for c in panel.countries)})), "
            "\"countries are as expected\")",
            f"check(min(panel$year) == {panel.years[0]}L && max(panel$year) == {panel.years[-1]}L, "
            f"\"years {panel.years[0]}-{panel.years[-1]}\")",
            "", "# Plausible ranges (the same rules as validation_report.md)"]
    for name, r in panel.results.items():
        rule = range_rule(r.variable, config)
        if rule is None:
            continue
        conds = []
        if rule.low is not None:
            conds.append(f"x {'>' if rule.low_strict else '>='} {_num(rule.low)}")
        if rule.high is not None:
            conds.append(f"x <= {_num(rule.high)}")
        out.append(f"x <- panel${name}; check(all({' & '.join(conds)}, na.rm = TRUE), {_r_str(f'{name}: {rule.text}')})")
    out += ["", "# Missing values (as listed in missing_data.csv)"]
    for n in names:
        out.append(f"check(sum(is.na(panel${n})) == {missing[n]}L, \"{n}: {missing[n]} missing\")")
    if report.known:
        out += ["", "# Known values (validation_report.md checks these exactly; here to the precision of the CSV file)"]
        for k in report.known:
            kv = k.known
            tol = "6e-8" if panel.results[kv.variable].precision == "float32" else "1e-15"
            sel = f"panel${kv.variable}[panel$iso3 == {_r_str(kv.iso3)} & panel$year == {kv.year}L]"
            out.append(f"x <- {sel}; check(length(x) == 1 && !is.na(x) && abs(x - {_num(kv.value)}) <= {tol} * "
                       f"abs({_num(kv.value)}), \"{kv.variable} {kv.iso3} {kv.year} = {_num(kv.value)}\")")
    out += ["", "# The long file holds the same values as the wide file",
            "panel_long <- read.csv(file.path(here, \"panel_long.csv\"), colClasses = c(variable = \"character\", "
            "iso3 = \"character\", country = \"character\", year = \"integer\", value = \"numeric\", flag = \"character\", "
            "source_note = \"character\"),",
            "                       na.strings = \"\", fileEncoding = \"UTF-8\")",
            f"check(nrow(panel_long) == {len(names) * len(panel.countries) * len(panel.years)}L, "
            "\"panel_long.csv has every value\")",
            "for (v in setdiff(names(panel), c(\"iso3\", \"country\", \"year\"))) {",
            "  part <- panel_long[panel_long$variable == v, ]",
            "  part <- part[order(part$iso3, part$year), ]",
            "  check(identical(as.vector(part$value), as.vector(panel[[v]])), paste0(v, \": long and wide files agree\"))",
            "}",
            "",
            "# The codebook: source, code, unit, prices, base year, retrieval date and raw files of every variable",
            "codebook <- read.csv(file.path(here, \"codebook.csv\"), colClasses = \"character\", fileEncoding = \"UTF-8\")",
            f"check(identical(codebook$variable, c({', '.join(_r_str(n) for n in names)})), \"codebook lists every variable\")",
            "",
            "rm(list = intersect(c(\"x\", \"v\", \"part\", \"check\", \"script_dir\"), ls()))",
            f"cat(\"\\nAll checks passed for {spec.name}.\\n\")",
            "cat(\"Ready: panel (one row per country-year), panel_long (with flag and source_note), codebook.\\n\")",
            ""]
    return "\n".join(out)


def stata_script(panel: Panel, rows: list[dict[str, str]], report: ValidationReport, config: Config) -> str:
    spec = panel.spec
    names = list(panel.results)
    missing = {row["variable"]: int(row["missing"]) for row in rows}
    out = [
        f"* load_and_check.do for spec {spec.name}: written by the econ data pipeline. Do not edit; rebuild instead.",
        "* Loads panel.dta, applies variable labels, and reruns the validation checks.",
        "* Each assert stops the do-file if a check fails.",
        "* Run it from this folder:   cd \"<this folder>\"   then   do load_and_check.do",
        "",
        "version 14",
        "clear",
        "set more off",
        "capture confirm file \"panel.dta\"",
        "if _rc {",
        "    display as error \"panel.dta is not in the current folder. Use cd to change to the folder holding this do-file.\"",
        "    exit 601",
        "}",
        "use \"panel.dta\", clear",
        "",
        "* Labels (panel.dta already has them; set again so this file is complete on its own)",
        f"label data {_stata_str(dta_label(spec.title))}",
    ]
    for key, label in KEY_LABELS.items():
        out.append(f"label variable {key} {_stata_str(label)}")
    for row in rows:
        out.append(f"label variable {row['variable']} {_stata_str(row['label'])}")
    out += ["", "* Structure",
            f"confirm variable iso3 country year {' '.join(names)}",
            "quietly describe",
            f"assert r(k) == {3 + len(names)}",
            "isid iso3 year",
            f"assert _N == {len(panel.countries) * len(panel.years)}",
            "quietly levelsof iso3, local(found) clean",
            f"assert \"`found'\" == \"{' '.join(panel.countries)}\"",
            "summarize year, meanonly",
            f"assert r(min) == {panel.years[0]} & r(max) == {panel.years[-1]}",
            "", "* Plausible ranges (the same rules as validation_report.md)"]
    for name, r in panel.results.items():
        rule = range_rule(r.variable, config)
        if rule is None:
            continue
        conds = []
        if rule.low is not None:
            conds.append(f"{name} {'>' if rule.low_strict else '>='} {_num(rule.low)}")
        if rule.high is not None:
            conds.append(f"{name} <= {_num(rule.high)}")
        out.append(f"assert {' & '.join(conds)} if !missing({name})")
    out += ["", "* Missing values (as listed in missing_data.csv)"]
    for n in names:
        out += [f"quietly count if missing({n})", f"assert r(N) == {missing[n]}"]
    if report.known:
        out += ["", "* Known values (validation_report.md checks these exactly)"]
        for k in report.known:
            kv = k.known
            sel = f"iso3 == \"{kv.iso3}\" & year == {kv.year}"
            out.append(f"quietly count if {sel} & !missing({kv.variable})")
            out.append("assert r(N) == 1")
            if panel.results[kv.variable].precision == "float32":
                out.append(f"assert float({kv.variable}) == float({_num(kv.value)}) if {sel}")
            else:
                out.append(f"assert reldif({kv.variable}, {_num(kv.value)}) < 1e-15 if {sel}")
    out += ["", "* Declare the panel",
            "encode iso3, generate(country_id)",
            "label variable country_id \"Country (numeric id for xtset)\"",
            "xtset country_id year",
            "",
            f"display as result \"All checks passed for {spec.name}.\"", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- entry


def write_outputs(panel: Panel, report: ValidationReport, config: Config, out_dir: Path,
                  retrieved: list[str], log: DataLog, growth_result=None) -> list[Path]:
    rows = codebook_rows(panel)
    written = []

    def text(name: str, content: str) -> None:
        path = out_dir / name
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)

    stata = config.outputs.stata
    text("panel_long.csv", _csv(["variable", "iso3", "country", "year", "value", "flag", "source_note"], long_rows(panel)))
    text("panel_wide.csv", wide_csv(panel))
    write_parquet(panel, rows, out_dir / "panel.parquet")
    written.append(out_dir / "panel.parquet")
    text("codebook.csv", codebook_csv(rows))
    text("codebook.md", codebook_markdown(panel, rows, report, stata))
    text("load_and_check.R", r_script(panel, rows, report, config))

    n_rows = len(panel.countries) * len(panel.years)
    lines = [f"panel_wide.csv: {n_rows} rows (country-years) x {len(rows)} variables",
             f"panel_long.csv and panel.parquet: {n_rows * len(rows)} rows (country-year-variables)",
             "also written: codebook.md, codebook.csv, load_and_check.R"]
    if stata:
        stamp = dta_timestamp(retrieved)
        write_dta(panel, out_dir / "panel.dta", stamp)
        written.append(out_dir / "panel.dta")
        text("load_and_check.do", stata_script(panel, rows, report, config))
        lines.append(f"Stata files (outputs.stata is on): panel.dta in Stata 14+ format, saved-at stamp set to {stamp} "
                     "(newest raw file) so rebuilds are identical; load_and_check.do")
        if dta_label(panel.spec.title) != panel.spec.title:
            lines.append("panel.dta: dataset label shortened to Stata's 80-character limit; the full title is in codebook.md")
    if growth_result is not None:
        from . import growth

        ga = panel.spec.growth_accounting
        text("growth_accounting_annual.csv", growth.annual_csv(growth_result))
        text("growth_accounting_summary.csv", growth.summary_csv(growth_result))
        text("growth_accounting.md", growth.markdown(growth_result, panel, ga, stata))
        text("growth_accounting.R", growth.r_script(growth_result, panel, ga))
        if stata:
            text("growth_accounting.do", growth.stata_script(growth_result, panel, ga))
        n_rules = sum(len(r) for r in growth_result.rules.values())
        lines.append(f"growth accounting: {len(growth_result.scopes)} scope(s), {n_rules} labor-share rule(s), "
                     f"{len(growth_result.annual)} annual rows, {len(growth_result.summary)} period averages")
    keep = {p.name for p in written}
    stale = [out_dir / n for n in STATA_FILES + GROWTH_FILES if n not in keep and (out_dir / n).is_file()]
    for path in stale:
        path.unlink()
    if stale:
        lines.append(f"removed files an earlier build left that this build does not write: {', '.join(p.name for p in stale)}")
    log.add("Outputs", *lines)
    return written


def remove_stale_outputs(out_dir: Path) -> list[Path]:
    """After a failed build, remove panel files an earlier build left, so out/ never
    holds a panel that the current validation report does not vouch for."""
    removed = []
    for name in PANEL_FILES + GROWTH_FILES + STATA_FILES:
        path = out_dir / name
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed
