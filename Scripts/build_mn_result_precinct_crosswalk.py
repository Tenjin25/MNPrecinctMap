#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_county(value: object) -> str:
    token = clean(value).upper().replace(" COUNTY", "").replace("&", " AND ")
    return re.sub(r"[^A-Z0-9]+", "", token)


def normalize_precinct(value: object) -> str:
    token = clean(value).upper().replace("&", " AND ")
    token = token.replace("TOWNSHIP", "TWP").replace("TWP.", "TWP")
    token = token.replace("UNORGANIZED", "UNORG")
    token = re.sub(r"\bWARD\b", "W", token)
    token = re.sub(r"\bPRECINCT\b", "P", token)
    token = re.sub(r"\bPCT\b", "P", token)
    token = re.sub(r"\bP\s*[-.]?\s*0*([0-9]+)\b", lambda m: f"P{int(m.group(1))}", token)
    token = re.sub(r"\bW\s*[-.]?\s*0*([0-9]+)\b", lambda m: f"W{int(m.group(1))}", token)
    return re.sub(r"[^A-Z0-9]+", "", token)


def read_official_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".xlsx":
        import openpyxl

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet = workbook["Precinct-Results"]
        values = sheet.iter_rows(values_only=True)
        headers = [clean(value) for value in next(values)]
        return [dict(zip(headers, (clean(value) for value in row))) for row in values]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, skipinitialspace=True)
        return [{clean(key): clean(value) for key, value in row.items() if key is not None} for row in reader]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a year-specific MN result-name to geometry-key crosswalk.")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--official-csv", type=Path, required=True)
    parser.add_argument("--geometry", type=Path, default=Path("Data/precincts_2026.geojson"))
    parser.add_argument("--legacy-bridge", type=Path)
    parser.add_argument("--overrides", type=Path, default=Path("Data/crosswalks/precinct_result_overrides.csv"))
    parser.add_argument("--output", type=Path, default=Path("Data/crosswalks/precinct_result_key_crosswalk.csv"))
    args = parser.parse_args()

    geometry = json.loads(args.geometry.read_text(encoding="utf-8"))
    codes_by_county: dict[str, set[str]] = defaultdict(set)
    names: dict[tuple[str, str], list[str]] = defaultdict(list)
    for feature in geometry.get("features", []):
        props = feature.get("properties", {}) or {}
        county = normalize_county(props.get("county_nam") or props.get("County") or props.get("COUNTYNAME"))
        code = clean(props.get("prec_id") or props.get("VTDST20"))
        if not county or not code:
            continue
        codes_by_county[county].add(code)
        for field in ("Precinct", "precinct_full_name", "NAME20", "NAMELSAD20"):
            name = normalize_precinct(props.get(field))
            if name and code not in names[(county, name)]:
                names[(county, name)].append(code)

    rows: list[dict[str, object]] = []
    for row in read_official_rows(args.official_csv):
        county_label = row.get("COUNTYNAME", "")
        precinct_label = row.get("PCTNAME", "")
        county = normalize_county(county_label)
        source = normalize_precinct(precinct_label)
        raw_code = re.sub(r"\D", "", row.get("PCTCODE", ""))
        code = raw_code.zfill(6) if raw_code else ""
        method = ""
        target = ""
        candidates: list[str] = []
        if county and code and code in codes_by_county.get(county, set()):
            target = code
            method = "official_code"
        elif county and source:
            candidates = names.get((county, source), [])
            if len(candidates) == 1:
                target = candidates[0]
                method = "unique_name"
        if not county_label or not precinct_label:
            continue
        rows.append({
            "year": args.year,
            "county": county_label,
            "source_precinct": precinct_label,
            "target_precinct": target,
            "method": method or "unresolved",
            "candidate_count": len(candidates),
        })

    if args.legacy_bridge and args.legacy_bridge.exists():
        with args.legacy_bridge.open(newline="", encoding="utf-8-sig") as handle:
            for bridge in csv.DictReader(handle):
                target_key = clean(bridge.get("target_key"))
                county = clean(bridge.get("county"))
                source = clean(bridge.get("precinct"))
                if not target_key or " - " not in target_key or not county or not source:
                    continue
                _, target = target_key.split(" - ", 1)
                rows.append({
                    "year": int(clean(bridge.get("year")) or 0),
                    "county": county,
                    "source_precinct": source,
                    "target_precinct": target,
                    "method": f"legacy_bridge_{clean(bridge.get('target_kind'))}",
                    "candidate_count": 1,
                })

    deduped: dict[tuple[int, str, str], dict[str, object]] = {}
    for row in rows:
        key = (int(row["year"]), normalize_county(row["county"]), normalize_precinct(row["source_precinct"]))
        current = deduped.get(key)
        if current is None or (not current["target_precinct"] and row["target_precinct"]):
            deduped[key] = row
    if args.overrides.exists():
        with args.overrides.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                normalized = {
                    "year": int(clean(row.get("year")) or 0),
                    "county": clean(row.get("county")),
                    "source_precinct": clean(row.get("source_precinct")),
                    "target_precinct": clean(row.get("target_precinct")),
                    "method": clean(row.get("method")) or "manual_override",
                    "candidate_count": int(clean(row.get("candidate_count")) or 1),
                }
                key = (normalized["year"], normalize_county(normalized["county"]), normalize_precinct(normalized["source_precinct"]))
                deduped[key] = normalized
    rows = sorted(deduped.values(), key=lambda row: (int(row["year"]), normalize_county(row["county"]), normalize_precinct(row["source_precinct"])))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["year", "county", "source_precinct", "target_precinct", "method", "candidate_count"])
        writer.writeheader()
        writer.writerows(rows)

    resolved = sum(bool(row["target_precinct"]) for row in rows)
    print(f"Wrote {len(rows)} rows to {args.output}; resolved {resolved} ({resolved / len(rows) * 100:.2f}%).")


if __name__ == "__main__":
    main()
