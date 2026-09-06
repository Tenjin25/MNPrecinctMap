#!/usr/bin/env python3
"""Build precinct display names and 2020 Census population data for the map."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlretrieve


PL_ZIP = "https://www2.census.gov/programs-surveys/decennial/2020/data/01-Redistricting_File--PL_94-171/Minnesota/mn2020.pl.zip"
FIELDS = ["P1_001N", "P1_003N", "P1_004N", "P1_005N", "P1_006N", "P1_007N", "P1_009N", "P2_002N",
          "P3_001N", "P3_003N", "P3_004N", "P3_005N", "P3_006N", "P3_007N", "P3_009N", "P4_002N"]
COUNT_FIELDS = ["total_population", "vap_18plus", "white_pop", "black_pop", "native_pop", "asian_pop",
                "pacific_pop", "multiracial_pop", "hispanic_pop", "white_vap", "black_vap", "native_vap",
                "asian_vap", "pacific_vap", "multiracial_vap", "hispanic_vap"]


def clean_name(value: object) -> str:
    return " ".join(str(value or "").replace("_", " ").split())


def pct(value: int, total: int) -> str:
    return f"{(100.0 * value / total):.2f}" if total else "0.00"


def census_rows() -> dict[str, dict[str, int]]:
    archive = Path(tempfile.gettempdir()) / "mn2020.pl.zip"
    if not archive.exists():
        print(f"Downloading {PL_ZIP}")
        urlretrieve(PL_ZIP, archive)
    result: dict[str, dict[str, int]] = {}
    logrec_to_geoid: dict[str, str] = {}
    with zipfile.ZipFile(archive) as bundle:
        with bundle.open("mngeo2020.pl") as handle:
            for raw in handle:
                fields = raw.decode("latin-1").rstrip("\r\n").split("|")
                if fields[2] != "700":  # voting district summary level
                    continue
                geoid = fields[9]
                logrec_to_geoid[fields[7]] = geoid
                result[geoid] = {}
        with bundle.open("mn000012020.pl") as handle:
            for raw in handle:
                fields = raw.decode("latin-1").rstrip("\r\n").split("|")
                geoid = logrec_to_geoid.get(fields[4])
                if geoid:
                    result[geoid].update({
                        "P1_001N": int(fields[5] or 0), "P1_003N": int(fields[7] or 0),
                        "P1_004N": int(fields[8] or 0), "P1_005N": int(fields[9] or 0),
                        "P1_006N": int(fields[10] or 0), "P1_007N": int(fields[11] or 0),
                        "P1_009N": int(fields[13] or 0), "P2_002N": int(fields[77] or 0),
                    })
        with bundle.open("mn000022020.pl") as handle:
            for raw in handle:
                fields = raw.decode("latin-1").rstrip("\r\n").split("|")
                geoid = logrec_to_geoid.get(fields[4])
                if not geoid:
                    continue
                # Segment 2 starts with the 71 P3 cells, followed by P4.
                result[geoid].update({
                    "P3_001N": int(fields[5] or 0), "P3_003N": int(fields[7] or 0),
                    "P3_004N": int(fields[8] or 0), "P3_005N": int(fields[9] or 0),
                    "P3_006N": int(fields[10] or 0), "P3_007N": int(fields[11] or 0),
                    "P3_009N": int(fields[13] or 0), "P4_002N": int(fields[77] or 0),
                })
    return result


def summarized_row(key_name: str, key: str, totals: dict[str, float]) -> dict[str, object]:
    row: dict[str, object] = {key_name: key}
    row.update({field: round(value) for field, value in totals.items()})
    pop = int(row["total_population"])
    vap = int(row["vap_18plus"])
    for race in ("white", "black", "native", "asian", "pacific", "multiracial", "hispanic"):
        row[f"{race}_pop_pct"] = pct(int(row[f"{race}_pop"]), pop)
        row[f"{race}_vap_pct"] = pct(int(row[f"{race}_vap"]), vap)
    return row


def aggregate(rows: list[dict[str, object]], key_name: str, key_fn) -> list[dict[str, object]]:
    groups: dict[str, dict[str, float]] = {}
    for row in rows:
        key = key_fn(row)
        bucket = groups.setdefault(key, {field: 0.0 for field in COUNT_FIELDS})
        for field in COUNT_FIELDS:
            bucket[field] += float(row[field])
    return [summarized_row(key_name, key, groups[key]) for key in sorted(groups)]


def district_rows(precinct_rows: list[dict[str, object]], crosswalk_path: Path) -> list[dict[str, object]]:
    by_precinct = {str(row["precinct_id"]): row for row in precinct_rows}
    groups: dict[str, dict[str, float]] = {}
    with crosswalk_path.open(newline="", encoding="utf-8-sig") as handle:
        for link in csv.DictReader(handle):
            source = by_precinct.get(str(link["precinct_key"]).upper())
            if not source:
                continue
            district = str(link["district_num"]).strip()
            weight = float(link.get("area_weight") or 0)
            bucket = groups.setdefault(district, {field: 0.0 for field in COUNT_FIELDS})
            for field in COUNT_FIELDS:
                bucket[field] += float(source[field]) * weight
    return [summarized_row("district", key, groups[key]) for key in sorted(groups)]


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--precincts", type=Path, default=Path("Data/precincts.geojson"))
    parser.add_argument("--friendly-out", type=Path, default=Path("Data/precinct_friendly_names.json"))
    parser.add_argument("--population-out", type=Path, default=Path("Data/precinct_demographics_2020_vap.csv"))
    parser.add_argument("--county-out", type=Path, default=Path("Data/mn_county_demographics_2020.json"))
    parser.add_argument("--congressional-out", type=Path, default=Path("Data/mn_congressional_demographics_2020.csv"))
    parser.add_argument("--house-out", type=Path, default=Path("Data/mn_state_house_demographics_2020.csv"))
    parser.add_argument("--senate-out", type=Path, default=Path("Data/mn_state_senate_demographics_2020.csv"))
    args = parser.parse_args()

    features = json.loads(args.precincts.read_text(encoding="utf-8"))["features"]
    census = census_rows()
    friendly: dict[str, dict[str, str]] = {}
    population_rows: list[dict[str, object]] = []
    missing: list[str] = []

    for feature in features:
        props = feature.get("properties") or {}
        county = clean_name(props.get("county_nam"))
        code = clean_name(props.get("prec_id"))
        name = clean_name(props.get("NAME20") or props.get("NAMELSAD20") or code)
        geoid = clean_name(props.get("GEOID20"))
        if county and code and name:
            friendly.setdefault(county, {})[code] = name

        values = census.get(geoid)
        if not county or not code or values is None:
            missing.append(geoid or f"{county} - {code}")
            continue
        vap = values["P3_001N"]
        row = {
            "precinct_id": f"{county.upper()} - {code.upper()}", "county": county,
            "precinct_code": code, "precinct_name": name, "total_population": values["P1_001N"],
            "white_pop": values["P1_003N"], "black_pop": values["P1_004N"],
            "native_pop": values["P1_005N"], "asian_pop": values["P1_006N"],
            "pacific_pop": values["P1_007N"], "multiracial_pop": values["P1_009N"],
            "hispanic_pop": values["P2_002N"],
            "vap_18plus": vap, "white_vap": values["P3_003N"], "black_vap": values["P3_004N"],
            "native_vap": values["P3_005N"], "asian_vap": values["P3_006N"],
            "pacific_vap": values["P3_007N"], "multiracial_vap": values["P3_009N"],
            "hispanic_vap": values["P4_002N"],
        }
        for key in ("white", "black", "native", "asian", "pacific", "multiracial", "hispanic"):
            row[f"{key}_vap_pct"] = pct(int(row[f"{key}_vap"]), vap)
        population_rows.append(row)

    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "generated_from": ["Data/precincts.geojson"],
        "counties": {
            county.upper(): dict(sorted(codes.items()))
            for county, codes in sorted(friendly.items())
        },
    }
    args.friendly_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_csv(args.population_out, population_rows)
    county_rows = aggregate(population_rows, "county", lambda row: str(row["county"]).upper())
    args.county_out.write_text(json.dumps({"source": "2020 Census PL 94-171", "counties": {
        str(row["county"]): row for row in county_rows}}, indent=2) + "\n", encoding="utf-8")
    write_csv(args.congressional_out, district_rows(population_rows, Path("Data/crosswalks/precinct_to_cd118.csv")))
    write_csv(args.house_out, district_rows(population_rows, Path("Data/crosswalks/precinct_to_2024_state_house.csv")))
    write_csv(args.senate_out, district_rows(population_rows, Path("Data/crosswalks/precinct_to_2024_state_senate.csv")))
    print(f"Wrote {sum(len(v) for v in friendly.values()):,} friendly names")
    print(f"Wrote {len(population_rows):,} precinct population rows")
    print(f"Wrote {len(county_rows):,} county and three district demographic datasets")
    if missing:
        print(f"Missing Census rows: {len(missing):,} ({', '.join(missing[:10])})")


if __name__ == "__main__":
    main()
