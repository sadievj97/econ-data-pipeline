"""Growth accounting: TFP growth as a Solow residual.

For each country and year t, with log growth rates in percent:
    g_X = 100 x (ln X_t - ln X_t-1)
    labor input L = workers x hours x human capital (whichever the spec gives)
    s = labor share: a variable averaged over t-1 and t (Tornqvist), or a constant you choose
    TFP growth                  g_A = g_Y - (1 - s) g_K - s g_L
    labor productivity growth   g_Y - g_N, with N = workers (x hours, if given), not adjusted for human capital
Period averages are the mean of the annual rates for the years after the
period's first year through its last; a period with any missing year is left
empty rather than averaged over a gap. Sectors without a capital series get
labor productivity only. Nothing here changes the panel's values.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .harmonize import Panel
from .numbers import fmt
from .spec import GrowthAccounting, country_name

ANNUAL_COLUMNS = ["scope_id", "scope", "rule_id", "share_rule", "iso3", "country", "year", "labor_share",
                  "g_output", "g_capital", "g_labor", "contribution_capital", "contribution_labor",
                  "tfp_growth", "labor_productivity_growth"]
SUMMARY_COLUMNS = ["scope_id", "scope", "rule_id", "share_rule", "iso3", "country", "period", "years",
                   "g_output", "contribution_capital", "contribution_labor", "tfp_growth", "labor_productivity_growth"]
MEASURES = ["g_output", "contribution_capital", "contribution_labor", "tfp_growth", "labor_productivity_growth"]


@dataclass
class Scope:
    id: int
    name: str  # "economy" or a sector code
    output: str
    labor: str
    capital: str | None
    hours: str | None
    human_capital: str | None
    share: str | float


@dataclass
class Rule:
    id: int
    label: str
    share: str | float  # a variable name or a constant


@dataclass
class GrowthResult:
    scopes: list[Scope]
    rules: dict[int, list[Rule]]  # scope id -> rules (the first is the scope's main rule)
    annual: list[dict] = field(default_factory=list)
    summary: list[dict] = field(default_factory=list)
    periods: list[tuple[int, int]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def log_growth(row: np.ndarray) -> np.ndarray:
    g = np.full(len(row), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        g[1:] = 100.0 * (np.log(row[1:]) - np.log(row[:-1]))
    return g


def _rule_label(share: str | float) -> str:
    return f"{share} (average of t-1 and t)" if isinstance(share, str) else f"constant {share:g}"


def plan(ga: GrowthAccounting, config: Config) -> tuple[list[Scope], dict[int, list[Rule]]]:
    scopes = [Scope(1, "economy", ga.output, ga.labor, ga.capital, ga.hours, ga.human_capital, ga.labor_share)]
    for i, s in enumerate(ga.sectors, start=2):
        scopes.append(Scope(i, s.sector, s.output, s.labor, s.capital, None, None,
                            s.labor_share if s.labor_share is not None else ga.labor_share))
    sensitivity = ga.sensitivity or config.growth_accounting.sensitivity_shares
    rules = {}
    for scope in scopes:
        shares: list[str | float] = [scope.share]
        if scope.capital is not None:
            shares += [c for c in sensitivity if c != scope.share]
        rules[scope.id] = [Rule(k, _rule_label(sh), sh) for k, sh in enumerate(shares, start=1)]
    return scopes, rules


def run(panel: Panel, ga: GrowthAccounting, config: Config) -> GrowthResult:
    scopes, rules = plan(ga, config)
    result = GrowthResult(scopes, rules, periods=[(panel.years[0], panel.years[-1])] + [tuple(p) for p in ga.periods])
    missing = [name for _, name in ga.variables_used() if name not in panel.results]
    if missing:
        result.errors.append(f"growth accounting needs {', '.join(missing)}, which could not be built")
        return result
    years = panel.years
    for scope in scopes:
        names = [n for n in (scope.output, scope.labor, scope.capital, scope.hours, scope.human_capital) if n]
        for name in names:
            values = panel.results[name].values
            if (values[~np.isnan(values)] <= 0).any():
                result.errors.append(f"growth accounting: {name} has values of zero or below, so log growth is undefined")
        if result.errors:
            continue
        for iso3 in panel.countries:
            empty = [n for n in names if np.isnan(panel.results[n].row(iso3)).all()]
            if empty:
                where = "the whole economy" if scope.name == "economy" else f"sector {scope.name}"
                result.notes.append(f"{iso3}, {where}: {', '.join(empty)} has no values, so the results that "
                                    "need it are left empty")
            y = panel.results[scope.output].row(iso3)
            workers = panel.results[scope.labor].row(iso3).copy()
            if scope.hours:
                workers = workers * panel.results[scope.hours].row(iso3)
            labor = workers * panel.results[scope.human_capital].row(iso3) if scope.human_capital else workers
            g_y, g_l = log_growth(y), log_growth(labor)
            g_w = log_growth(workers)  # labor productivity is output per worker (or per hour), not per unit of human capital
            g_k = log_growth(panel.results[scope.capital].row(iso3)) if scope.capital else np.full(len(years), np.nan)
            for rule in rules[scope.id]:
                if isinstance(rule.share, str):
                    s = panel.results[rule.share].row(iso3)
                    s_bar = np.full(len(years), np.nan)
                    s_bar[1:] = (s[1:] + s[:-1]) / 2.0
                else:
                    s_bar = np.full(len(years), float(rule.share))
                    s_bar[0] = np.nan
                if scope.capital:
                    c_k = (1.0 - s_bar) * g_k
                    c_l = s_bar * g_l
                    tfp = g_y - c_k - c_l
                else:
                    c_k = c_l = tfp = np.full(len(years), np.nan)
                lp = g_y - g_w
                for t in range(1, len(years)):
                    result.annual.append({
                        "scope_id": scope.id, "scope": scope.name, "rule_id": rule.id, "share_rule": rule.label,
                        "iso3": iso3, "year": years[t], "labor_share": s_bar[t], "g_output": g_y[t],
                        "g_capital": g_k[t], "g_labor": g_l[t], "contribution_capital": c_k[t],
                        "contribution_labor": c_l[t], "tfp_growth": tfp[t], "labor_productivity_growth": lp[t]})
    _summarize(result)
    if ga.compare_with and not result.errors:
        _compare(result, panel, ga)
    return result


def _summarize(result: GrowthResult) -> None:
    by_key: dict[tuple, list[dict]] = {}
    for row in result.annual:
        by_key.setdefault((row["scope_id"], row["rule_id"], row["iso3"]), []).append(row)
    for (scope_id, rule_id, iso3), rows in by_key.items():
        for start, end in result.periods:
            window = [r for r in rows if start < r["year"] <= end]
            out = {"scope_id": scope_id, "scope": rows[0]["scope"], "rule_id": rule_id,
                   "share_rule": rows[0]["share_rule"], "iso3": iso3, "period": f"{start}-{end}",
                   "years": len(window)}
            for m in MEASURES:
                values = np.array([r[m] for r in window], dtype="float64")
                out[m] = float(values.mean()) if len(values) and not np.isnan(values).any() else np.nan
            result.summary.append(out)


def _compare(result: GrowthResult, panel: Panel, ga: GrowthAccounting) -> None:
    for iso3 in panel.countries:
        other = log_growth(panel.results[ga.compare_with].row(iso3))
        ours = np.array([r["tfp_growth"] for r in result.annual
                         if r["scope_id"] == 1 and r["rule_id"] == 1 and r["iso3"] == iso3])
        theirs = other[1:]
        ok = ~np.isnan(ours) & ~np.isnan(theirs)
        if ok.sum() >= 3:
            diff = np.abs(ours[ok] - theirs[ok])
            corr = float(np.corrcoef(ours[ok], theirs[ok])[0, 1])
            result.notes.append(f"{iso3}: TFP growth here vs growth of {ga.compare_with}: {int(ok.sum())} years, "
                                f"mean absolute difference {diff.mean():.2f} percentage points, correlation {corr:.2f}")


# --------------------------------------------------------------- outputs


def _csv(columns: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        cells = []
        for c in columns:
            v = row.get(c, "")
            if c == "country":
                v = country_name(row["iso3"]) or ""
            if isinstance(v, float):
                v = "" if np.isnan(v) else fmt(v)
            cells.append(v)
        writer.writerow(cells)
    return buffer.getvalue()


def annual_csv(result: GrowthResult) -> str:
    return _csv(ANNUAL_COLUMNS, result.annual)


def summary_csv(result: GrowthResult) -> str:
    return _csv(SUMMARY_COLUMNS, result.summary)


def _pct(v: float) -> str:
    return "" if np.isnan(v) else f"{v:.2f}"


def markdown(result: GrowthResult, panel: Panel, ga: GrowthAccounting, stata: bool = False) -> str:
    units = {name: panel.results[name].unit["unit"] for _, name in ga.variables_used() if name in panel.results}
    out = [f"# Growth accounting: {panel.spec.title}", "",
           "TFP growth is the Solow residual g_A = g_Y - (1 - s) g_K - s g_L, with log growth rates in percent "
           "(100 x the change in natural logs) and labor share s. A labor share taken from a variable is averaged "
           "over years t-1 and t. Period figures are averages of the annual rates over the years after the "
           "period's first year; a period with any missing year is left empty.", "",
           "## Inputs", "", "| role | variable | unit |", "|---|---|---|"]
    for role, name in ga.variables_used():
        out.append(f"| {role} | `{name}` | {units.get(name, '')} |")
    out.append("")
    if result.errors:
        out += ["## Not computed", ""] + [f"- {e}" for e in result.errors] + [""]
        return "\n".join(out)
    for scope in result.scopes:
        title = "Whole economy" if scope.name == "economy" else f"Sector {scope.name}"
        rule = result.rules[scope.id][0]
        out += [f"## {title} ({rule.label} labor share)", "",
                "| country | period | output growth | capital contribution | labor contribution | TFP growth | "
                "labor productivity growth |", "|---|---|---|---|---|---|---|"]
        for row in result.summary:
            if row["scope_id"] == scope.id and row["rule_id"] == 1:
                out.append(f"| {row['iso3']} | {row['period']} | {_pct(row['g_output'])} | "
                           f"{_pct(row['contribution_capital'])} | {_pct(row['contribution_labor'])} | "
                           f"{_pct(row['tfp_growth'])} | {_pct(row['labor_productivity_growth'])} |")
        if scope.capital is None:
            out += ["", "No capital series for this sector, so only labor productivity growth is shown."]
        out.append("")
        if len(result.rules[scope.id]) > 1:
            whole = f"{result.periods[0][0]}-{result.periods[0][1]}"
            out += [f"Sensitivity of average TFP growth ({whole}) to the labor share:", "",
                    "| country | " + " | ".join(r.label for r in result.rules[scope.id]) + " |",
                    "|---|" + "---|" * len(result.rules[scope.id])]
            for iso3 in panel.countries:
                cells = []
                for r in result.rules[scope.id]:
                    match = [s for s in result.summary if s["scope_id"] == scope.id and s["rule_id"] == r.id
                             and s["iso3"] == iso3 and s["period"] == whole]
                    cells.append(_pct(match[0]["tfp_growth"]) if match else "")
                out.append(f"| {iso3} | " + " | ".join(cells) + " |")
            out.append("")
    if result.notes:
        out += ["## Notes", ""] + [f"- {n}" for n in result.notes] + [""]
    out += ["## Files", "",
            "- growth_accounting_annual.csv: every country, year, scope and labor-share rule.",
            "- growth_accounting_summary.csv: the period averages shown above, for every rule.",
            "- growth_accounting.R recomputes both tables from panel_wide.csv, checks that they match, and leaves "
            "them in R as `growth_annual` and `growth_summary`"
            + ("; growth_accounting.do does the same check in Stata." if stata else "."), ""]
    return "\n".join(out)


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def r_script(result: GrowthResult, panel: Panel, ga: GrowthAccounting) -> str:
    out = [f"# growth_accounting.R for spec {panel.spec.name}: written by the econ data pipeline. Do not edit.",
           "# Recomputes growth_accounting_annual.csv and growth_accounting_summary.csv from panel_wide.csv",
           "# with base R, and stops if any number differs by more than 1e-9.",
           "", "script_dir <- function() {",
           "  args <- commandArgs(trailingOnly = FALSE)",
           "  file <- sub(\"^--file=\", \"\", args[grepl(\"^--file=\", args)])",
           "  if (length(file) == 1) return(dirname(normalizePath(file)))",
           "  for (i in rev(seq_len(sys.nframe()))) { f <- sys.frame(i)$ofile; if (!is.null(f)) return(dirname(normalizePath(f))) }",
           "  getwd()", "}", "here <- script_dir()",
           "panel <- read.csv(file.path(here, \"panel_wide.csv\"), na.strings = \"\", check.names = FALSE,",
           "                  colClasses = c(iso3 = \"character\", country = \"character\", year = \"integer\"))",
           "panel <- panel[order(panel$iso3, panel$year), ]",
           "lg <- function(x) c(NA, 100 * diff(log(x)))",
           "rows <- list()", ""]
    for scope in result.scopes:
        for rule in result.rules[scope.id]:
            out.append(f"for (c in sort(unique(panel$iso3))) {{")
            out.append("  d <- panel[panel$iso3 == c, ]")
            workers = f"d${scope.labor}" + (f" * d${scope.hours}" if scope.hours else "")
            labor = workers + (f" * d${scope.human_capital}" if scope.human_capital else "")
            out.append(f"  gy <- lg(d${scope.output}); gl <- lg({labor}); gw <- lg({workers})")
            out.append(f"  gk <- {'lg(d$' + scope.capital + ')' if scope.capital else 'rep(NA_real_, nrow(d))'}")
            if isinstance(rule.share, str):
                out.append(f"  s <- c(NA, (head(d${rule.share}, -1) + tail(d${rule.share}, -1)) / 2)")
            else:
                out.append(f"  s <- c(NA, rep({rule.share!r}, nrow(d) - 1))")
            if scope.capital:
                out.append("  ck <- (1 - s) * gk; cl <- s * gl; tfp <- gy - ck - cl")
            else:
                out.append("  ck <- cl <- tfp <- rep(NA_real_, nrow(d))")
            out.append(f"  rows[[length(rows) + 1]] <- data.frame(scope_id = {scope.id}L, rule_id = {rule.id}L, iso3 = c,")
            out.append("    year = d$year, labor_share = s, g_output = gy, g_capital = gk, g_labor = gl,")
            out.append("    contribution_capital = ck, contribution_labor = cl, tfp_growth = tfp,")
            out.append("    labor_productivity_growth = gy - gw)[-1, ]")
            out.append("}")
    measures = ["labor_share", "g_output", "g_capital", "g_labor", "contribution_capital", "contribution_labor",
                "tfp_growth", "labor_productivity_growth"]
    out += ["mine <- do.call(rbind, rows)",
            "theirs <- read.csv(file.path(here, \"growth_accounting_annual.csv\"), na.strings = \"\",",
            "                   colClasses = c(scope = \"character\", share_rule = \"character\", iso3 = \"character\", country = \"character\"))",
            "key <- function(x) paste(x$scope_id, x$rule_id, x$iso3, x$year)",
            "mine <- mine[order(key(mine)), ]; theirs <- theirs[order(key(theirs)), ]",
            "if (!identical(key(mine), key(theirs))) stop(\"CHECK FAILED: annual table rows differ\", call. = FALSE)",
            f"for (m in c({', '.join(_q(m) for m in measures)})) {{",
            "  a <- mine[[m]]; b <- theirs[[m]]",
            "  if (!identical(is.na(a), is.na(b)) || any(abs(a - b) > 1e-9, na.rm = TRUE))",
            "    stop(paste(\"CHECK FAILED: annual\", m, \"differs\"), call. = FALSE)",
            "}",
            "cat(\"ok   annual growth accounting table reproduced\\n\")",
            "summ <- read.csv(file.path(here, \"growth_accounting_summary.csv\"), na.strings = \"\",",
            "                 colClasses = c(scope = \"character\", share_rule = \"character\", iso3 = \"character\", country = \"character\", period = \"character\"))",
            "for (i in seq_len(nrow(summ))) {",
            "  p <- as.integer(strsplit(summ$period[i], \"-\")[[1]])",
            "  w <- mine[mine$scope_id == summ$scope_id[i] & mine$rule_id == summ$rule_id[i] & mine$iso3 == summ$iso3[i] &",
            "            mine$year > p[1] & mine$year <= p[2], ]",
            f"  for (m in c({', '.join(_q(m) for m in MEASURES)})) {{",
            "    v <- if (nrow(w) && !anyNA(w[[m]])) mean(w[[m]]) else NA_real_",
            "    if (!identical(is.na(v), is.na(summ[[m]][i])) || isTRUE(abs(v - summ[[m]][i]) > 1e-9))",
            "      stop(paste(\"CHECK FAILED: summary\", m, \"row\", i), call. = FALSE)",
            "  }",
            "}",
            "cat(\"ok   summary table reproduced\\n\")",
            "growth_annual <- theirs[order(theirs$scope_id, theirs$rule_id, theirs$iso3, theirs$year), ]",
            "growth_summary <- summ",
            "rm(mine, theirs, summ, rows, key, lg)",
            f"cat(\"\\nGrowth accounting reproduced for {panel.spec.name}. Tables ready: growth_annual, growth_summary.\\n\")",
            ""]
    return "\n".join(out)


def stata_script(result: GrowthResult, panel: Panel, ga: GrowthAccounting) -> str:
    out = [f"* growth_accounting.do for spec {panel.spec.name}: written by the econ data pipeline. Do not edit.",
           "* Recomputes growth_accounting_annual.csv from panel.dta and stops if any number differs by more than 1e-9.",
           "* Run it from this folder:   do growth_accounting.do",
           "", "version 14", "clear", "set more off",
           "tempfile annual mine",
           "import delimited using \"growth_accounting_annual.csv\", asdouble varnames(1) stringcols(2 4 5 6) clear",
           "save `annual'", ""]
    first = True
    for scope in result.scopes:
        for rule in result.rules[scope.id]:
            workers = scope.labor + (f" * {scope.hours}" if scope.hours else "")
            labor = workers + (f" * {scope.human_capital}" if scope.human_capital else "")
            out += [f"* scope {scope.name}, rule {rule.label}",
                    "use \"panel.dta\", clear",
                    "sort iso3 year",
                    f"by iso3: generate double gy = 100 * (ln({scope.output}) - ln({scope.output}[_n-1]))",
                    f"generate double lab = {labor}",
                    f"generate double wrk = {workers}",
                    "by iso3: generate double gl = 100 * (ln(lab) - ln(lab[_n-1]))",
                    "by iso3: generate double gw = 100 * (ln(wrk) - ln(wrk[_n-1]))"]
            if scope.capital:
                out.append(f"by iso3: generate double gk = 100 * (ln({scope.capital}) - ln({scope.capital}[_n-1]))")
            else:
                out.append("generate double gk = .")
            if isinstance(rule.share, str):
                out.append(f"by iso3: generate double s = ({rule.share} + {rule.share}[_n-1]) / 2")
            else:
                out.append(f"by iso3: generate double s = cond(_n > 1, {rule.share!r}, .)")
            if scope.capital:
                out += ["generate double ck = (1 - s) * gk", "generate double cl = s * gl",
                        "generate double tfp = gy - ck - cl"]
            else:
                out += ["generate double ck = .", "generate double cl = .", "generate double tfp = ."]
            out += ["generate double lp = gy - gw",
                    "by iso3: drop if _n == 1",
                    f"generate scope_id = {scope.id}", f"generate rule_id = {rule.id}",
                    "keep scope_id rule_id iso3 year s gy gk gl ck cl tfp lp"]
            if not first:
                out.append("append using `mine'")
            out += ["save `mine', replace", ""]
            first = False
    out += ["use `mine', clear",
            "merge 1:1 scope_id rule_id iso3 year using `annual'",
            "assert _merge == 3"]
    pairs = [("s", "labor_share"), ("gy", "g_output"), ("gk", "g_capital"), ("gl", "g_labor"),
             ("ck", "contribution_capital"), ("cl", "contribution_labor"), ("tfp", "tfp_growth"),
             ("lp", "labor_productivity_growth")]
    for mine, theirs in pairs:
        out.append(f"assert missing({mine}) == missing({theirs})")
        out.append(f"assert abs({mine} - {theirs}) < 1e-9 if !missing({mine})")
    out += ["", f"display as result \"Growth accounting reproduced for {panel.spec.name}.\"", ""]
    return "\n".join(out)
