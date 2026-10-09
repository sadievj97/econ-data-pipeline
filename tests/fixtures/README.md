# Test fixtures

Small copies of real source files, so tests run without internet. Made on
2026-10-09 from the cached downloads in `data/raw/`.

- `wdi/data_<code>_<ISO3>.json`: World Bank API responses, unchanged.
- `wdi/catalog_sample.json`: 13 entries from the WDI indicator list (header
  adjusted to match the shorter list).
- `pwt/pwt_sample.dta`: Uzbekistan and Kazakhstan rows and 11 columns from
  Penn World Table 11.0 (`pwt110.dta`), re-saved with the original labels.
  PWT is licensed CC BY 4.0. Cite: Feenstra, Robert C., Robert Inklaar and
  Marcel P. Timmer (2015), "The Next Generation of the Penn World Table",
  American Economic Review, 105(10), 3150-3182. Data: doi:10.34894/FABVLR.
