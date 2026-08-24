#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import shapefile
from shapely.geometry import Point
from shapely.geometry import shape as shapely_shape
from shapely.strtree import STRtree

from build_mn_district_carry_crosswalks import (
    DistrictIndex,
    load_district_index,
    load_vtd_by_block,
    locate_district,
)


@dataclass(frozen=True)
class Vtd00Index:
    geometries: list[object]
    vtd_keys: list[str]
    countyfps: list[str]
    vtdst00s: list[str]
    tree: STRtree


def clean(value: object) -> str:
    return ("" if value is None else str(value)).strip()


def district_sort_key(value: str) -> tuple[int, str]:
    token = clean(value).upper()
    digits = "".join(ch for ch in token if ch.isdigit())
    return (int(digits), token) if digits else (9999, token)


def load_vtd00_indices(path: Path) -> tuple[dict[str, Vtd00Index], int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    buckets: dict[str, list[tuple[object, str, str, str]]] = defaultdict(list)

    for feature in payload.get("features", []):
        props = feature.get("properties", {}) or {}
        countyfp = clean(props.get("COUNTYFP00"))
        vtdst00 = clean(props.get("VTDST00") or props.get("prec_id"))
        county = clean(props.get("county_nam"))
        geometry = feature.get("geometry")
        if not countyfp or not vtdst00 or not county or not geometry:
            continue
        geom = shapely_shape(geometry)
        if geom.is_empty:
            continue
        key = f"{county} - {vtdst00}".upper()
        buckets[countyfp].append((geom, key, countyfp, vtdst00))

    indices: dict[str, Vtd00Index] = {}
    count = 0
    for countyfp, rows in buckets.items():
        geometries = [row[0] for row in rows]
        indices[countyfp] = Vtd00Index(
            geometries=geometries,
            vtd_keys=[row[1] for row in rows],
            countyfps=[row[2] for row in rows],
            vtdst00s=[row[3] for row in rows],
            tree=STRtree(geometries),
        )
        count += len(rows)
    return indices, count


def locate_vtd00(point: Point, index: Vtd00Index) -> tuple[int | None, bool]:
    for raw_idx in index.tree.query(point):
        idx = int(raw_idx)
        if index.geometries[idx].covers(point):
            return idx, False
    nearest = index.tree.query_nearest(point)
    if getattr(nearest, "size", 0) > 0:
        return int(nearest[0]), True
    return None, False


def build_rows(
    counts: dict[str, dict[str, int]],
    key_meta: dict[str, tuple[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for vtd_key in sorted(counts):
        total_blocks = sum(counts[vtd_key].values())
        countyfp, vtdst00 = key_meta[vtd_key]
        for district in sorted(counts[vtd_key], key=district_sort_key):
            block_count = counts[vtd_key][district]
            rows.append(
                {
                    "vtd00_key": vtd_key,
                    "district_num": district,
                    "district_code": district,
                    "area_weight": f"{block_count / total_blocks:.10f}",
                    "block_count": str(block_count),
                    "total_blocks": str(total_blocks),
                    "countyfp": countyfp,
                    "vtdst00": vtdst00,
                    "weight_method": "2020_block_count_centroids",
                }
            )
    return rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "vtd00_key",
        "district_num",
        "district_code",
        "area_weight",
        "block_count",
        "total_blocks",
        "countyfp",
        "vtdst00",
        "weight_method",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_rows(rows: list[dict[str, str]]) -> tuple[int, int, int]:
    sums: dict[str, float] = defaultdict(float)
    splits: dict[str, int] = defaultdict(int)
    for row in rows:
        sums[row["vtd00_key"]] += float(row["area_weight"])
        splits[row["vtd00_key"]] += 1
    bad = sum(abs(value - 1.0) > 1e-6 for value in sums.values())
    split = sum(value > 1 for value in splits.values())
    return len(sums), split, bad


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build historical MN VTD00-to-current-district crosswalks by assigning "
            "2020 census block internal points to VTD00 and current district polygons."
        )
    )
    parser.add_argument("--vtd00-geojson", type=Path, default=Path("Data/vtds_2000.geojson"))
    parser.add_argument("--blockassign-zip", type=Path, default=Path("Data/BlockAssign_ST27_MN.zip"))
    parser.add_argument("--tabblocks-zip", type=Path, default=Path("Data/tl_2020_27_tabblock20.zip"))
    parser.add_argument("--cd-shapefile", type=Path, default=Path("Data/tl_2022_27_cd118.zip"))
    parser.add_argument("--state-house-shapefile", type=Path, default=Path("Data/tl_2024_27_sldl.zip"))
    parser.add_argument("--state-senate-shapefile", type=Path, default=Path("Data/tl_2024_27_sldu.zip"))
    parser.add_argument("--out-dir", type=Path, default=Path("Data/crosswalks"))
    args = parser.parse_args()

    vtd00_indices, source_vtds = load_vtd00_indices(args.vtd00_geojson)
    eligible_blocks = set(load_vtd_by_block(args.blockassign_zip))
    district_indices: dict[str, DistrictIndex] = {
        "congressional": load_district_index(args.cd_shapefile, "CD118FP", "int"),
        "state_house": load_district_index(args.state_house_shapefile, "SLDLST", "upper"),
        "state_senate": load_district_index(args.state_senate_shapefile, "SLDUST", "int"),
    }

    counts: dict[str, dict[str, dict[str, int]]] = {
        scope: defaultdict(lambda: defaultdict(int)) for scope in district_indices
    }
    key_meta: dict[str, tuple[str, str]] = {}
    reader = shapefile.Reader(str(args.tabblocks_zip))
    fields = [field[0] for field in reader.fields[1:]]
    geoid_idx = fields.index("GEOID20")
    lon_idx = fields.index("INTPTLON20")
    lat_idx = fields.index("INTPTLAT20")

    processed = 0
    nearest_vtd00 = 0
    missing_vtd00 = 0
    district_misses = defaultdict(int)

    for record in reader.iterRecords():
        geoid = clean(record[geoid_idx])
        if geoid not in eligible_blocks:
            continue
        countyfp = geoid[2:5]
        vtd_index = vtd00_indices.get(countyfp)
        if vtd_index is None:
            missing_vtd00 += 1
            continue
        try:
            point = Point(float(record[lon_idx]), float(record[lat_idx]))
        except (TypeError, ValueError):
            continue
        vtd_idx, used_nearest = locate_vtd00(point, vtd_index)
        if vtd_idx is None:
            missing_vtd00 += 1
            continue
        if used_nearest:
            nearest_vtd00 += 1
        vtd_key = vtd_index.vtd_keys[vtd_idx]
        key_meta[vtd_key] = (vtd_index.countyfps[vtd_idx], vtd_index.vtdst00s[vtd_idx])

        for scope, district_index in district_indices.items():
            district = locate_district(point, district_index)
            if not district:
                district_misses[scope] += 1
                continue
            counts[scope][vtd_key][district] += 1
        processed += 1

    output_names = {
        "congressional": ["vtd00_to_cd118.csv"],
        "state_house": ["vtd00_to_2022_state_house.csv", "vtd00_to_2024_state_house.csv"],
        "state_senate": ["vtd00_to_2022_state_senate.csv", "vtd00_to_2024_state_senate.csv"],
    }
    summaries = []
    for scope, names in output_names.items():
        rows = build_rows(counts[scope], key_meta)
        for name in names:
            write_rows(args.out_dir / name, rows)
        vtds, split_vtds, bad_sums = validate_rows(rows)
        summaries.append(
            f"{scope}: rows={len(rows)} vtds={vtds} split_vtds={split_vtds} bad_weight_sums={bad_sums}"
        )

    print(
        "Built VTD00 district crosswalks\n"
        f"  source_vtds={source_vtds} eligible_blocks={len(eligible_blocks)} processed_blocks={processed}\n"
        f"  nearest_vtd00_assignments={nearest_vtd00} missing_vtd00={missing_vtd00}\n"
        f"  district_misses={dict(district_misses)}\n  "
        + "\n  ".join(summaries)
    )


if __name__ == "__main__":
    main()
