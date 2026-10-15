"""Builds backend/data/ports_global.csv from two public datasets (re-run to refresh):
  * UN/LOCODE code list (function '1' = port) -> sea ports      https://github.com/datasets/un-locode  (UNECE)
  * OurAirports airports.csv (scheduled service, IATA code)     https://ourairports.com/data/           (public domain)
Usage: python scripts/build_ports_data.py <folder with locode.csv, airports.csv, countries.csv>
"""
import csv
import sys
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "scratch")
out = Path(__file__).resolve().parents[1] / "backend" / "data" / "ports_global.csv"
names = {r["code"]: r["name"] for r in csv.DictReader(open(src / "countries.csv", encoding="utf-8-sig"))}
names.update({"AX": "Aland Islands", "XZ": "International Waters", "SJ": "Svalbard and Jan Mayen"})
rows = {}
for r in csv.DictReader(open(src / "locode.csv", encoding="utf-8-sig")):
    if not (r["Function"] or "").startswith("1") or not r["Location"].strip() or r["Status"].strip() == "XX":
        continue
    code = r["Country"] + r["Location"]
    name = " ".join((r["NameWoDiacritics"] or r["Name"]).split())
    if name.endswith(" Pt"):  # UN/LOCODE abbreviates "Port"
        name = name[:-3] + " Port"
    rows[code] = (code, code, "Sea Port", name[:160], name[:80], names.get(r["Country"], r["Country"])[:80])
for r in csv.DictReader(open(src / "airports.csv", encoding="utf-8-sig")):
    iata = r["iata_code"].strip()
    if iata and r["scheduled_service"] == "yes" and r["type"] in ("large_airport", "medium_airport", "small_airport") and iata not in rows:
        rows[iata] = (iata, "", "Airport", " ".join(r["name"].split())[:160], " ".join((r["municipality"] or "").split())[:80], names.get(r["iso_country"], r["iso_country"])[:80])
with open(out, "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["code", "unlocode", "port_type", "name", "city", "country"])
    for k in sorted(rows):
        w.writerow(rows[k])
print(len(rows), "ports written to", out)
