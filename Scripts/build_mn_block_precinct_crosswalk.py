#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import shapefile
from shapely.geometry import Point, shape
from shapely.strtree import STRtree


def load_layer(path: Path, county_fields: tuple[str, ...], code_fields: tuple[str, ...], county_fips_by_name=None):
    obj = json.loads(path.read_text(encoding="utf-8"))
    grouped = defaultdict(list)
    for feature in obj.get("features", []):
        props = feature.get("properties", {}) or {}
        county = ""
        if county_fips_by_name:
            county_name = str(props.get("County") or props.get("county_nam") or "").strip().upper()
            county = county_fips_by_name.get(county_name, "")
        if not county:
            county = next((str(props.get(k)).zfill(3) for k in county_fields if props.get(k) not in (None, "")), "")
        code = next((str(props.get(k)).strip() for k in code_fields if props.get(k) not in (None, "")), "")
        if county and code and feature.get("geometry"):
            grouped[county].append((shape(feature["geometry"]), code))
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser(description="Crosswalk Minnesota VTD20 precincts to current precincts using 2020 Census blocks.")
    parser.add_argument("--blocks", type=Path, default=Path("Data/tl_2020_27_tabblock20.zip"))
    parser.add_argument("--counties", type=Path, default=Path("Data/tl_2020_27_county20.geojson"))
    parser.add_argument("--source", type=Path, default=Path("Data/precincts.geojson"))
    parser.add_argument("--target", type=Path, default=Path("Data/precincts_2026.geojson"))
    parser.add_argument("--output", type=Path, default=Path("Data/crosswalks/vtd20_to_current_precinct_blocks.csv"))
    args = parser.parse_args()

    county_obj = json.loads(args.counties.read_text(encoding="utf-8"))
    county_fips_by_name = {}
    for feature in county_obj.get("features", []):
        props = feature.get("properties", {}) or {}
        name = str(props.get("NAME20") or props.get("NAME") or "").strip().upper()
        fips = str(props.get("COUNTYFP20") or props.get("COUNTYFP") or "").zfill(3)
        if name and fips:
            county_fips_by_name[name] = fips
    source = load_layer(args.source, ("COUNTYFP20",), ("prec_id", "VTDST20"))
    target = load_layer(args.target, ("COUNTYFP20",), ("prec_id",), county_fips_by_name)
    source_trees = {county: (STRtree([g for g, _ in items]), items) for county, items in source.items()}
    target_trees = {county: (STRtree([g for g, _ in items]), items) for county, items in target.items()}
    weights = defaultdict(lambda: [0, 0])

    reader = shapefile.Reader(str(args.blocks))
    fields = [field[0] for field in reader.fields[1:]]
    idx = {name: fields.index(name) for name in ("COUNTYFP20", "INTPTLAT20", "INTPTLON20", "POP20")}
    for record in reader.iterRecords():
        county = str(record[idx["COUNTYFP20"]]).zfill(3)
        if county not in source_trees or county not in target_trees:
            continue
        point = Point(float(record[idx["INTPTLON20"]]), float(record[idx["INTPTLAT20"]]))
        source_tree, source_items = source_trees[county]
        target_tree, target_items = target_trees[county]
        source_hits = [int(i) for i in source_tree.query(point, predicate="intersects")]
        target_hits = [int(i) for i in target_tree.query(point, predicate="intersects")]
        if len(source_hits) != 1 or len(target_hits) != 1:
            continue
        source_code = source_items[source_hits[0]][1]
        target_code = target_items[target_hits[0]][1]
        population = int(record[idx["POP20"]] or 0)
        item = weights[(county, source_code, target_code)]
        item[0] += population
        item[1] += 1

    totals = defaultdict(int)
    for (county, source_code, _), (population, blocks) in weights.items():
        totals[(county, source_code)] += population if population > 0 else blocks

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["county_fips", "source_precinct", "target_precinct", "population", "block_count", "weight_share"])
        writer.writeheader()
        for (county, source_code, target_code), (population, blocks) in sorted(weights.items()):
            numerator = population if population > 0 else blocks
            denominator = totals[(county, source_code)]
            writer.writerow({
                "county_fips": county,
                "source_precinct": source_code,
                "target_precinct": target_code,
                "population": population,
                "block_count": blocks,
                "weight_share": f"{numerator / denominator:.12f}" if denominator else "0",
            })
    print(f"Wrote {len(weights)} block-weighted precinct links to {args.output}.")


if __name__ == "__main__":
    main()
