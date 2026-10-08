You are building a data-pipeline agent for my economics research, in this folder. I describe the data I need in plain English; it turns that into a spec I confirm, fetches the series from official sources, harmonizes them into a tidy panel, and writes R and Stata code plus a log of every transformation. I work in R and Stata and study productivity and growth in Central Asia, so sectoral value added, employment, investment, and capital stocks matter most.

Before writing code: read this brief, ask me the questions at the end, and propose a phased plan. Wait for my approval.

## Non-negotiable rules (CLAUDE.md in this folder already contains these; keep it in sync)

1. The model never produces or edits numbers. It only parses my request into a spec and maps variable names to source indicator codes; all data work is deterministic Python.
2. No silent fixes: no interpolation, extrapolation, splicing, or rebasing unless the spec asks for it, and every such step is logged with its method.
3. Every variable records its source, indicator code, unit, base year, currency, price basis (current or constant), retrieval date, and the raw file it came from.
4. Raw downloads are cached with timestamps and never modified; reruns on the cache must give byte-identical outputs.
5. Any API keys go in .env (gitignored).

## Workflow

1. python -m pipeline request 'Uzbekistan and Kazakhstan, 2000-2023: real value added and employment by broad sector, gross fixed capital formation, labor share'
2. The model drafts spec.yaml (ISO3 countries, years, variables, preferred and fallback sources, units, base year). It searches indicator catalogs and shows candidate codes with their official definitions. I confirm or edit the spec.
3. python -m pipeline build spec.yaml fetches, validates, harmonizes, and writes the outputs.

## Sources (verify current endpoints and formats before relying on them)

World Bank WDI API; Penn World Table 11.0 (released October 2025, 185 countries through 2023; use the Stata file from the GGDC Dataverse); ILOSTAT; IMF data (confirm which API is current before using it); FAOSTAT; UN Comtrade if I add a key; national statistics offices, starting with the Statistics Agency of Uzbekistan. Series that need manual downloads go in data/manual/ with a note on where they came from.

## Validation (fail loudly)

- Unique country-year keys, no unit mixing, plausible ranges per variable.
- Overlap checks: when two sources cover the same concept (for example GDP growth in WDI and PWT), report differences above a threshold.
- Break detection: flag jumps and known events such as currency regime changes and national accounts revisions; never smooth them.
- A missing-data report by variable, country, and year.

## Outputs (out/<spec_name>/)

- panel_long.csv, panel_wide.csv, panel.parquet, and panel.dta with variable labels.
- codebook.md and codebook.csv.
- data_log.md: every step in order, including dropped observations and why.
- load_and_check.R and load_and_check.do, which load the panel, apply labels, and rerun the validation checks.
- Optional growth-accounting module: TFP as a Solow residual using a labor share I choose or the PWT labor share, with sensitivity to that share and sector-level results where the data allow. Output tables plus R and Stata code that reproduces them.

## Stack

Python 3.12+, httpx, pandas, pyarrow, pyreadstat, pydantic, pytest; the Anthropic Python SDK for the spec step only (model IDs in config.yaml).

## Build phases (stop for my review after each)

1. Skeleton, CLAUDE.md, spec schema, tests.
2. WDI and PWT connectors with caching.
3. Harmonization and validation.
4. Outputs including the .dta file and the R and Stata scripts.
5. ILOSTAT, IMF, FAOSTAT, and national sources; growth-accounting module; README.

## Acceptance tests

- Five known values that I supply with sources match exactly after harmonization.
- A rerun on cached data produces identical output hashes.
- A planted duplicate country-year fails validation.
- load_and_check.R and load_and_check.do run without errors on the output.

## Ask me before starting

- My first real data request.
- Which sector classification to use and my preferred base year.
- Whether I want the growth-accounting module now or later.
