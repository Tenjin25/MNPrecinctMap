#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

from build_mn_contests_from_precinct_csv import compute_color, make_alias_key


def allocate(total: int, weighted_targets: list[tuple[str, float]]) -> dict[str, int]:
    raw = [(target, total * weight) for target, weight in weighted_targets]
    out = {target: math.floor(value) for target, value in raw}
    remaining = total - sum(out.values())
    for target, _ in sorted(raw, key=lambda item: (-(item[1] - math.floor(item[1])), item[0]))[:remaining]:
        out[target] += 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply block-population fallback to unmatched MN precinct contest rows.")
    parser.add_argument("--contest-dir", type=Path, default=Path("Data/contests"))
    parser.add_argument("--geometry", type=Path, default=Path("Data/precincts_2026.geojson"))
    parser.add_argument("--vtd20", type=Path, default=Path("Data/precincts.geojson"))
    parser.add_argument("--blocks-crosswalk", type=Path, default=Path("Data/crosswalks/vtd20_to_current_precinct_blocks.csv"))
    parser.add_argument("--years", nargs="+", type=int, default=[2022, 2024])
    args = parser.parse_args()

    current = json.loads(args.geometry.read_text(encoding="utf-8"))
    current_keys = {str(f.get("properties", {}).get("precinct_name", "")).upper() for f in current.get("features", [])}
    county_name_by_fips: dict[str, str] = {}
    current_props = {}
    for feature in current.get("features", []):
        props = feature.get("properties", {}) or {}
        county = str(props.get("county_nam") or props.get("County") or "").strip()
        code = str(props.get("prec_id") or "").strip()
        if county and code:
            current_props[f"{county} - {code}".upper()] = (county, code)

    old = json.loads(args.vtd20.read_text(encoding="utf-8"))
    old_by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for feature in old.get("features", []):
        props = feature.get("properties", {}) or {}
        county = str(props.get("county_nam") or "").strip()
        county_fips = str(props.get("COUNTYFP20") or "").zfill(3)
        code = str(props.get("prec_id") or props.get("VTDST20") or "").strip()
        county_name_by_fips[county_fips] = county
        for field in ("NAME20", "NAMELSAD20", "precinct_full_name"):
            name = str(props.get(field) or "").strip()
            alias = make_alias_key(county, name)
            if alias and (county_fips, code) not in old_by_name[alias]:
                old_by_name[alias].append((county_fips, code))

    links: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    with args.blocks_crosswalk.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            county_fips = str(row["county_fips"]).zfill(3)
            county = county_name_by_fips.get(county_fips, "")
            target_code = row["target_precinct"].strip()
            target_key = f"{county} - {target_code}".upper()
            if target_key in current_keys:
                links[(county_fips, row["source_precinct"].strip())].append((target_key, float(row["weight_share"])))

    manifest_updates: dict[tuple[str, int], tuple[int, float]] = {}
    for path in sorted(args.contest_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("year") or 0) not in args.years:
            continue
        combined: dict[str, dict[str, object]] = {}
        applied = 0
        for row in payload.get("rows", []):
            key = str(row.get("county") or "").upper()
            targets = [(key, 1.0)]
            if key not in current_keys and " - " in key:
                county, precinct = key.split(" - ", 1)
                old_hits = old_by_name.get(make_alias_key(county, precinct), [])
                if len(old_hits) == 1:
                    candidates = links.get(old_hits[0], [])
                    total_weight = sum(weight for _, weight in candidates)
                    if candidates and total_weight > 0:
                        targets = [(target, weight / total_weight) for target, weight in candidates]
                        applied += 1
            allocations = {field: allocate(int(row.get(field) or 0), targets) for field in ("dem_votes", "rep_votes", "other_votes")}
            for target, _ in targets:
                if target not in combined:
                    combined[target] = {**row, "county": target.title(), "dem_votes": 0, "rep_votes": 0, "other_votes": 0}
                for field in allocations:
                    combined[target][field] = int(combined[target][field]) + allocations[field][target]

        output_rows = []
        for key in sorted(combined):
            row = combined[key]
            dem, rep, other = (int(row[field]) for field in ("dem_votes", "rep_votes", "other_votes"))
            total = dem + rep + other
            signed = rep - dem
            margin_pct = signed / total * 100 if total else 0.0
            winner_code = "R" if signed > 0 else ("D" if signed < 0 else "T")
            row.update({
                "total_votes": total,
                "margin": abs(signed),
                "margin_pct": round(margin_pct, 6),
                "winner": "REPUBLICAN" if winner_code == "R" else ("DEMOCRAT" if winner_code == "D" else "TIE"),
                "color": compute_color(abs(margin_pct), winner_code),
            })
            output_rows.append(row)
        payload["rows"] = output_rows
        payload.setdefault("meta", {})["block_fallback_rows"] = applied
        matched_rows = sum(str(row.get("county") or "").upper() in current_keys for row in output_rows)
        payload["meta"]["geometry_match_coverage_pct"] = round(matched_rows / len(output_rows) * 100, 4) if output_rows else 0.0
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        manifest_updates[(str(payload.get("contest_type") or ""), int(payload.get("year") or 0))] = (
            len(output_rows),
            payload["meta"]["geometry_match_coverage_pct"],
        )
        print(f"{path.name}: block fallback applied to {applied} source rows")

    manifest_path = args.contest_dir / "manifest.json"
    if manifest_path.exists() and manifest_updates:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("files", []):
            update = manifest_updates.get((str(entry.get("contest_type") or ""), int(entry.get("year") or 0)))
            if update:
                entry["rows"], entry["geometry_match_coverage_pct"] = update
        manifest_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
