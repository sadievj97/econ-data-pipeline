"""Seed a spec's known-values file from official files the pipeline does NOT use.

The pipeline reads WDI through its JSON API, ILOSTAT through rplumber.ilo.org
and PWT from the Stata file. This script reads the same numbers from different
official downloads (the World Bank's per-indicator CSV download, ILO's SDMX
service at sdmx.ilo.org, and PWT's Excel workbook), so a match is an
independent check. Numbers are copied by this code, never typed by hand.
Downloads go into data/raw/ like every other raw file.

Usage (from the project folder):
    .\\.venv\\Scripts\\python.exe scripts\\seed_known_values.py specs\\uzb_kaz_sectoral_2000_2023.yaml

Replace or extend the result with values you have checked yourself.
"""

from __future__ import annotations

import csv
import hashlib
import io
import sys
import zipfile
from pathlib import Path

import yaml
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import net  # noqa: E402
from pipeline.cache import RawCache, Snapshot  # noqa: E402
from pipeline.config import load_config  # noqa: E402
from pipeline.connectors.pwt import DATAVERSE, DOI  # noqa: E402
from pipeline.known_values import known_values_path  # noqa: E402
from pipeline.spec import load_spec  # noqa: E402

# (variable in the spec, source, code, country, year). For ILO, code is (SDMX dataflow, series key).
# Two PWT picks (labsh KAZ 2010, emp UZB 2019) were replaced on 2026-10-12 while the PWT
# Dataverse was down; "pwt" picks still work if added back.
PICKS = [
    ("va_agr", "wdi", "NV.AGR.TOTL.KD", "UZB", 2015),
    ("va_ser", "wdi", "NV.SRV.TOTL.KD", "KAZ", 2020),
    ("gfcf", "wdi", "NE.GDI.FTOT.KD", "UZB", 2023),
    ("va_ind", "wdi", "NV.IND.TOTL.KD", "KAZ", 2010),
    ("emp_agr", "ilo_sdmx", ("DF_EMP_2EMP_SEX_ECO_NB", "UZB.A..SEX_T.ECO_SECTOR_AGR"), "UZB", 2019),
]
ILO_SDMX = "https://sdmx.ilo.org/rest/data"


def ilo_sdmx_value(cache: RawCache, client, flow: str, key: str, year: int) -> tuple[float, str]:
    cache_key = f"ilostat/sdmx/{flow}/{key}"
    snap = cache.latest(cache_key)
    if snap is None:
        url = f"{ILO_SDMX}/ILO,{flow}/{key}"
        response = net.get(client, url, {"format": "csv"})
        if not response.content.decode("utf-8-sig", errors="replace").startswith("DATAFLOW"):
            raise SystemExit(f"ILO SDMX sent an unexpected reply for {flow} {key}")
        snap = cache.store(cache_key, "data.csv", response.content,
                           {"source": "ilostat", "url": url, "params": {"format": "csv"}})
    rows = [r for r in csv.DictReader(io.StringIO(snap.read_bytes().decode("utf-8-sig"))) if r["TIME_PERIOD"] == str(year)]
    if len(rows) != 1:
        raise SystemExit(f"ILO SDMX {flow} {key} has {len(rows)} values for {year}")
    unit = f"unit multiplier 10^{rows[0].get('UNIT_MULT')}" if rows[0].get("UNIT_MULT") else "units"
    return float(rows[0]["OBS_VALUE"]), f"ILO SDMX service, dataflow {flow}, series {key} ({unit}), {snap.relative_file()}"


def wdi_csv_zip(cache: RawCache, client, code: str) -> Snapshot:
    key = f"wdi/bulk_csv/{code}"
    snap = cache.latest(key)
    if snap is None:
        url = f"https://api.worldbank.org/v2/en/indicator/{code}"
        response = net.get(client, url, {"downloadformat": "csv"})
        zipfile.ZipFile(io.BytesIO(response.content)).testzip()
        snap = cache.store(key, f"{code}.zip", response.content,
                           {"source": "wdi", "url": url, "params": {"downloadformat": "csv"}})
    return snap


def wdi_csv_value(snap: Snapshot, code: str, iso3: str, year: int) -> tuple[float, str]:
    with zipfile.ZipFile(io.BytesIO(snap.read_bytes())) as z:
        name = next(n for n in z.namelist() if n.startswith(f"API_{code}_"))
        lines = z.read(name).decode("utf-8-sig").splitlines()
    updated = next((row[1] for row in csv.reader(lines[:4]) if row and row[0] == "Last Updated Date"), "?")
    reader = csv.reader(lines[4:])
    header = next(reader)
    for row in reader:
        if len(row) > 1 and row[1] == iso3:
            text = row[header.index(str(year))]
            if not text:
                raise SystemExit(f"{code} {iso3} {year} is empty in {name}")
            return float(text), f"World Bank WDI CSV download {name} (last updated {updated}), {snap.relative_file()}"
    raise SystemExit(f"{iso3} not found in {name}")


def pwt_workbook(cache: RawCache, client) -> Snapshot:
    key = "pwt/11.0/pwt110.xlsx"
    snap = cache.latest(key)
    if snap is None:
        listing = net.get(client, f"{DATAVERSE}/api/datasets/:persistentId/", {"persistentId": DOI}).json()
        entry = next(f["dataFile"] for f in listing["data"]["latestVersion"]["files"]
                     if f["dataFile"]["filename"] == "pwt110.xlsx")
        url = f"{DATAVERSE}/api/access/datafile/{entry['id']}"
        content = net.get(client, url).content
        if hashlib.sha1(content).hexdigest() != entry["checksum"]["value"]:
            raise SystemExit("pwt110.xlsx does not match the fingerprint published on the Dataverse")
        snap = cache.store(key, "pwt110.xlsx", content,
                           {"source": "pwt", "url": url, "doi": DOI, "published_sha1": entry["checksum"]["value"]})
    return snap


def pwt_values(snap: Snapshot, wanted: list[tuple[str, str, int]]) -> dict[tuple[str, str, int], float]:
    snap.verify()
    book = load_workbook(snap.file, read_only=True, data_only=True)
    found = {}
    for sheet in book.worksheets:
        rows = sheet.iter_rows(values_only=True)
        header = next(rows, None)
        if not header or "countrycode" not in header or "year" not in header:
            continue
        col = {name: i for i, name in enumerate(header)}
        for row in rows:
            for code, iso3, year in wanted:
                if row[col["countrycode"]] == iso3 and row[col["year"]] == year:
                    found[(code, iso3, year)] = float(row[col[code]])
        break
    book.close()
    return found


def main(spec_file: str) -> None:
    spec_path = Path(spec_file)
    spec = load_spec(spec_path)
    names = {v.name for v in spec.variables}
    missing = [p[0] for p in PICKS if p[0] not in names]
    if missing:
        raise SystemExit(f"{spec_path.name} has no variable(s) {', '.join(missing)}; edit PICKS in this script")
    out = known_values_path(spec_path)
    if out.exists() and "seed_known_values.py" not in out.read_text(encoding="utf-8"):
        raise SystemExit(f"{out.name} was edited by hand, so it is left as it is")
    config = load_config()
    cache = RawCache(config.folder("raw_cache"))
    client = net.make_client(config.http.timeout_seconds)
    values = []
    try:
        pwt_wanted = [(code, iso3, year) for _, src, code, iso3, year in PICKS if src == "pwt"]
        pwt_found: dict = {}
        pwt_note = ""
        if pwt_wanted:
            try:
                snap = pwt_workbook(cache, client)
            except net.DownloadError as exc:
                print(f"Skipping the PWT values for now: {exc}. Run this script again later to add them.")
            else:
                pwt_found = pwt_values(snap, pwt_wanted)
                pwt_note = f"Penn World Table 11.0 Excel workbook pwt110.xlsx (doi:10.34894/FABVLR), {snap.relative_file()}"
        for variable, src, code, iso3, year in PICKS:
            if src == "wdi":
                value, note = wdi_csv_value(wdi_csv_zip(cache, client, code), code, iso3, year)
            elif src == "ilo_sdmx":
                value, note = ilo_sdmx_value(cache, client, code[0], code[1], year)
                code = code[0]
            elif (code, iso3, year) in pwt_found:
                value, note = pwt_found[(code, iso3, year)], pwt_note
            else:
                continue
            values.append({"variable": variable, "iso3": iso3, "year": year, "value": value,
                           "source": f"{code}: {note}"})
    finally:
        client.close()

    header = ("# Known values for " + spec_path.name + ". The build checks that the panel matches each one exactly\n"
              "# (at the source's own precision). These were copied by scripts/seed_known_values.py from\n"
              "# official files the pipeline does not otherwise read, as an independent check.\n"
              "# Replace or extend them with values you have checked yourself, each with its source\n"
              "# (once you edit this file, the script will no longer overwrite it).\n\n")
    out.write_text(header + yaml.safe_dump({"values": values}, sort_keys=False, allow_unicode=True, width=120),
                   encoding="utf-8", newline="\n")
    print(f"Wrote {len(values)} known values to {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
