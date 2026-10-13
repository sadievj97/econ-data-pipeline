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
- `ilostat/`: ILOSTAT indicator list (3 entries) and employment-by-sector replies
  for UZB and KAZ, kept to the broad-sector rows (ILO, rplumber.ilo.org,
  retrieved 2026-10-12).
- `imf/`: the IMF WEO structure with its indicator code list cut to four codes,
  and the WEO NGDP_RPCH replies for UZB and KAZ, unchanged (api.imf.org,
  retrieved 2026-10-12). Source: International Monetary Fund. IMF statistical data may be copied and redistributed with this credit: https://www.imf.org/en/About/copyright-and-terms
- `faostat/`: the FAOSTAT dataset list cut to CS, and the CS capital stock zip
  cut to Uzbekistan and Kazakhstan (FAO, bulks-faostat.fao.org, CC BY 4.0).
- `siat/`: the Statistics Agency of Uzbekistan indicator tree cut to the branch
  holding tables 548, 1209 and 1584, and those three tables, unchanged
  (api.siat.stat.uz, retrieved 2026-10-12).
