# Econ data pipeline

Describe the data you need in plain English. The pipeline turns that into a
**spec** you review and confirm, downloads the series from official sources,
harmonizes them into a tidy country-year panel, checks it, and writes CSV and
parquet files, a codebook, a log of every step, and R scripts that reload and
recheck the panel. An optional module computes TFP growth as a Solow residual,
with an R script that reproduces its tables. R is the analysis language;
Stata files can be switched on in `config.yaml` (`outputs: stata: true`).

Built for productivity and growth research on Central Asia, but any country
works for the international sources.

## The five rules

1. **The model never produces or edits numbers.** Claude only reads your request
   and picks indicator codes. Units, base years, currencies and definitions are
   copied from the source catalogs by Python, and any number Claude writes that is
   not in your request or the catalog's own wording is rejected.
2. **No silent fixes.** Interpolation, extrapolation, splicing, rebasing and
   rescaling happen only when a spec lists them; every value they create is flagged,
   and every step is in `data_log.md`.
3. **Every variable records** its source, indicator code, unit, base year, currency,
   price basis, retrieval date and raw file (see `codebook.md`).
4. **Raw downloads are cached and never modified.** Each is saved once with a
   timestamp and a SHA-256 fingerprint, made read-only, and checked on every read.
   Rebuilding from the cache gives byte-identical outputs.
5. **Keys live in `.env`**, which git ignores.

## Setup (once)

Everything lives in this folder. To set it up:

1. Install Python 3.12 or newer from python.org (tick "Add python.exe to PATH").
2. Open PowerShell in this folder (in File Explorer, click the address bar, type
   `powershell`, press Enter) and run:

```powershell
py -3 -m venv .venv
```
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

3. Copy `.env.example` to a new file named `.env`. Right-click `.env`, choose
   Open with, then Notepad, paste your Anthropic key after `ANTHROPIC_API_KEY=`
   (no spaces, no quotes) and press Ctrl+S.
4. Check everything with:

```powershell
.\pipeline check-setup
```

It reports Python, the settings file, whether your key is set (never the key
itself), and whether R is installed.

### What you supply yourself

This repository holds the code, example specs and small test samples only.
No downloaded data, results or keys are included:

- **Your Anthropic API key** goes in `.env` (step 3 above). It is needed only
  for the `request` step; every other command works without it.
- **Downloaded data** (`data\raw\`) and **results** (`out\`) are created on
  your computer the first time you build a spec.
- **Hand-downloaded files** (for example from a statistics office website)
  go in `data\manual\` with a note on where each came from; see
  `data\manual\README.md`.
- `specs\uzb_kaz_sectoral_2000_2023.lock.json` pins the exact files the
  original author downloaded. You will not have those files, so the first
  time you build that spec, add `--refresh` to download your own copies
  (in the window, tick *Download fresh data*); the lock file is then updated
  to match them. If a source has revised its data since, a value in
  `specs\uzb_kaz_sectoral_2000_2023.known_values.yaml` may no longer match;
  the validation report says which one.

## The window (easiest)

Double-click **`Econ Pipeline.cmd`** in this folder (you can also make a
desktop shortcut to it). The window has four tabs that follow the workflow:

1. **Ask for data**: type your request and click *Draft a spec*.
2. **Review and build**: your specs with their status. *Open in Notepad* to
   review or edit, then *Check*, *Confirm* and *Build*. Tick *Download fresh
   data* only when you want newer data.
3. **Results**: buttons that open the data in RStudio, the validation report,
   the codebook, growth accounting, the data log, or the results folder.
4. **Find indicators**: search a source, or list the filter choices of a code.

Messages at the bottom show exactly what each step is doing; *Stop* cancels a
step. Every button runs the same command as the command line below.

## Workflow (command line)

**1. Ask for data.** Claude searches the catalogs and drafts a spec:

```powershell
.\pipeline request "Uzbekistan and Kazakhstan, 2000-2023: real value added and employment by broad sector, gross fixed capital formation, labor share"
```

This writes `specs\<name>.yaml` (the draft) and `specs\<name>.review.md`
(each chosen code with its official definition, plus alternatives). Anything
the catalogs do not state clearly is marked `FILL_ME`. Questions Claude could
not settle are listed at the top of the draft.

**2. Review and confirm.** Open the draft in Notepad, fill any `FILL_ME`,
change what you like, and set `confirmed: true`. Then check it:

```powershell
.\pipeline check-spec specs\uzb_kaz_sectoral_2000_2023.yaml
```

**3. Build.**

```powershell
.\pipeline build specs\uzb_kaz_sectoral_2000_2023.yaml
```

Results go to `out\<spec name>\`. If validation finds an error, no panel files
are written and `validation_report.md` says why.

The first build writes `specs\<name>.lock.json`, which pins the exact raw files
used. Later builds reuse them, so results never change behind your back. To
download fresh data, add `--refresh` (results may then change; the lock file
records the new files).

## Commands

| command | what it does |
|---|---|
| `.\pipeline check-setup` | Python, settings, key status (never the key), R |
| `.\pipeline request "<text>"` | Claude drafts a spec (uses your Anthropic key; a few cents to about 50 cents) |
| `.\pipeline search <source> "<words>"` | search a catalog: `wdi`, `pwt`, `ilostat`, `imf`, `faostat`, `nso_uzb` |
| `.\pipeline describe <source> <code> [--dataset D] [--country ISO3]` | the filter choices an indicator offers for a country |
| `.\pipeline check-spec <spec>` | check a spec and summarize it |
| `.\pipeline confirm <spec>` | mark a reviewed spec as confirmed (only if it passes every check) |
| `.\pipeline app` | open the window |
| `.\pipeline fetch <spec> [--refresh]` | download (or reuse) the raw data and compare units with the spec |
| `.\pipeline build <spec> [--refresh]` | fetch, harmonize, validate, write all outputs |

Run the tests (offline, about 20 seconds) with:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Sources

| source | how a spec points to a series | notes |
|---|---|---|
| `wdi` World Bank WDI | `code: NV.AGR.TOTL.KD` | constant US$ series use 2015 prices; constant LCU series use each country's own base year |
| `pwt` Penn World Table 11.0 | `dataset: "11.0"`, `code: rnna` | stored as 4-byte floats (about 7 digits); no `hc` or `rtfpna` for Uzbekistan |
| `ilostat` ILOSTAT | `code: EMP_2EMP_SEX_ECO_NB_A`, `filters: {sex: SEX_T, classif1: ECO_SECTOR_AGR}` | survey series (`EMP_TEMP_...`) have gaps and may combine several surveys (flagged; add `source: ...` to use one); ILO modelled estimates (`EMP_2EMP_...`) cover every year |
| `imf` IMF | `dataset: IMF.RES,WEO`, `code: NGDP_RPCH` | WEO values after the latest actual year are projections and are marked; use a vintage such as `IMF.RES,WEO_2026_APR_VINTAGE` to fix the release; other dimensions are filters |
| `faostat` FAOSTAT | `dataset: CS`, `code: "22034"`, `filters: {element: "6184"}` | bulk downloads (the query API needs a login); FAO flags such as imputed values are kept as notes |
| `nso_uzb` Statistics Agency of Uzbekistan | `code: "548"`, `filters: {row: A}` | Uzbekistan only, annual tables only; "constant prices" tables are often in previous year's prices, which is not a constant-price series (flagged) |
| `manual` files you download | `file: uzstat/gva.csv`, `code: gva` | CSV or xlsx in `data\manual\` with columns iso3, year, one per series, optional note; each needs `<file>.source.md` saying where it came from; units are as you declare them |

Not connected: UN Comtrade (needs a key), and statistics offices other than
Uzbekistan's (use `manual` files). Use `search` and `describe` to find codes and
filter values.

## The spec file

See `specs\example_uzb_kaz.yaml` and `specs\example_growth.yaml`. The main parts:

- `countries` (ISO3), `years`, `sector_classification` (`isic4_3sector`,
  `isic4_3sector_mfg` or `isic4_sections`), `source_choice` (`same_for_all`: one
  source for every country; `per_country`: each country may differ, but units
  must match).
- `variables`: each has a `name`, `label`, `measure` (`level`, `share`,
  `percent`, `growth_rate`, `index`), optional `sector`, and `sources` (the
  first is preferred; the others are fallbacks in the same unit). Each source
  states its `unit`, `currency`, `price_basis` and `base_year`; the build checks
  them against the source and stops if they disagree.
- `transforms` (only if you want them): `interpolate` (linear or log-linear,
  with `max_gap`), `extrapolate` (with a proxy's growth), `rebase` (to an index or
  another reference year), `splice` (with a fallback source at a link year),
  `scale` (for example thousands to units). Each is logged and flagged.
- `comparisons`: pairs of variables whose growth (or level, for identical units)
  is compared; years above the threshold are reported.
- `growth_accounting` (optional): `output`, `capital`, `labor`, optional `hours`
  and `human_capital`, `labor_share` (a variable or a number you choose),
  optional `sensitivity`, `periods`, `compare_with` and `sectors`.

Next to a spec you can add `<spec>.known_values.yaml`: values you trust, with
sources, that the panel must match exactly (at the source's own precision).
`scripts\seed_known_values.py` fills one from official files the pipeline does
not otherwise read.

`known_events.yaml` lists currency regime changes and national accounts
revisions (each with a source); the validation report shows them next to any
jumps.

## Outputs (`out\<spec name>\`)

| file | contents |
|---|---|
| `panel_wide.csv` | one row per country-year, one column per variable |
| `panel_long.csv`, `panel.parquet` | one row per country-year-variable, with `flag` (value created by a transform) and `source_note` (the source's own remark: projection, break in series, imputed) |
| `codebook.md`, `codebook.csv` | every variable's source, code, definition, unit, prices, base year, retrieval date, raw files |
| `validation_report.md`, `missing_data.csv` | errors, flags (source comparisons, jumps, repeated values, source notes, known events), known values, every missing country-year |
| `data_log.md` | every step in order, including what was left out and why |
| `load_and_check.R` | loads the panel, applies labels and units, reruns the checks |
| `growth_accounting.md`, `growth_accounting_annual.csv`, `growth_accounting_summary.csv`, `growth_accounting.R` | growth accounting tables, and an R script that recomputes them from the panel and confirms they match |

With `outputs: stata: true` in `config.yaml`, a build also writes `panel.dta`
(Stata 14+, with labels), `load_and_check.do` and `growth_accounting.do`. They
are off by default; switching them off again removes them from `out\`.

## Using the results in R

In RStudio: File > Open File, pick `out\<spec name>\load_and_check.R`, and click
**Source**. When it finishes you have three data frames:

- `panel`: one row per country and year, one column per variable, each with a
  `label` and `unit` attribute;
- `panel_long`: one row per country, year and variable, with `flag` and
  `source_note`;
- `codebook`: one row per variable with its source, code, units and raw files.

Then open `growth_accounting.R` the same way (if the spec has growth
accounting) for `growth_annual` and `growth_summary`. Both scripts use base R
only, so no packages need installing. From PowerShell, the same checks run with
`Rscript out\<spec name>\load_and_check.R`.

## Growth accounting method

TFP growth is the Solow residual g_A = g_Y - (1 - s) g_K - s g_L, with log
growth rates in percent. A labor share taken from a variable is averaged over
years t-1 and t; a constant share is one you choose. Labor input is workers,
times hours and the human capital index when given. Labor productivity growth
is output per worker (or per hour). Period figures average the annual rates and
are left empty when any year is missing. Sectors without a capital series get
labor productivity only.

## Troubleshooting

- **"ANTHROPIC_API_KEY ... is still the placeholder"**: open `.env` in Notepad,
  paste the key after the `=`, and press Ctrl+S (a dot next to the tab name means
  it is not saved yet).
- **A source is down** (for example the PWT Dataverse returning errors): builds
  that already have the raw files cached still work; try `--refresh` later.
- **"pins ... no longer in data\raw"**: the cached raw files were removed. Restore
  them, or rebuild with `--refresh`.
- **"data/manual/... has changed since it was first used"**: rebuild with
  `--refresh` to use the new version on purpose.
- **An R script stops with "CHECK FAILED"**: a file in `out\` was changed or is
  incomplete. Rebuild the spec; the message names the check that failed.
- **Stata** (only with `outputs: stata: true`): the .do files are written for
  Stata 14 or newer but have not been tested in Stata yet.

## License

The code is released under the MIT License (see `LICENSE`). The data you
download stays under its sources' terms: World Bank WDI, PWT, ILOSTAT and
FAOSTAT are CC BY 4.0, and IMF data may be reused with the credit "Source:
International Monetary Fund". The small samples in `tests/fixtures/` carry the
same terms and credits (see `tests/fixtures/README.md`).
