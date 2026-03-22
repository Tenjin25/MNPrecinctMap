#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import shapefile
from shapely.geometry import Point
from shapely.geometry import shape as shapely_shape
from shapely.strtree import STRtree


@dataclass(frozen=True)
class VtdRef:
    countyfp: str
    vtdst20: str


def clean(value: str | None) -> str:
    return (value or "").strip()


def normalize_county_name(value: str) -> str:
    return clean(value).replace(" County", "").strip()


def load_precinct_key_map(precincts_geojson: Path) -> dict[tuple[str, str], str]:
    obj = json.loads(precincts_geojson.read_text(encoding="utf-8"))
    mapping: dict[tuple[str, str], str] = {}
    for feature in obj.get("features", []):
        props = feature.get("properties", {}) or {}
        countyfp = clean(props.get("COUNTYFP20"))
        vtd = clean(props.get("VTDST20") or props.get("vtdst20") or props.get("prec_id"))
        county = normalize_county_name(
            clean(props.get("county_nam"))
            or clean(props.get("COUNTYNAME"))
            or clean(props.get("NAME20"))
            or clean(props.get("NAMELSAD20"))
        )
        if countyfp and vtd and county:
            mapping[(countyfp, vtd)] = f"{county} - {vtd}"
    return mapping


def read_pipe_rows(zf: zipfile.ZipFile, member_name: str) -> list[dict[str, str]]:
    text = zf.read(member_name).decode("utf-8", errors="replace").splitlines()
    if not text:
        return []
    headers = [h.strip() for h in text[0].split("|")]
    rows: list[dict[str, str]] = []
    for line in text[1:]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        row = {headers[i]: parts[i] if i < len(parts) else "" for i in range(len(headers))}
        rows.append(row)
    return rows


def load_vtd_by_block(blockassign_zip: Path) -> dict[str, VtdRef]:
    with zipfile.ZipFile(blockassign_zip) as zf:
        vtd_rows = read_pipe_rows(zf, "BlockAssign_ST27_MN_VTD.txt")

    vtd_by_block: dict[str, VtdRef] = {}
    for row in vtd_rows:
        block = clean(row.get("BLOCKID"))
        countyfp = clean(row.get("COUNTYFP"))
        vtd = clean(row.get("DISTRICT"))
        if block and countyfp and vtd:
            vtd_by_block[block] = VtdRef(countyfp=countyfp, vtdst20=vtd)

    return vtd_by_block


@dataclass(frozen=True)
class DistrictIndex:
    geometries: list[object]
    districts: list[str]
    tree: STRtree


def normalize_district_value(value: str, mode: str) -> str:
    token = clean(value)
    if token == "":
        return ""
    if mode == "int":
        return str(int(token))
    if mode == "upper":
        return token.upper()
    return token


def load_district_index(shapefile_zip: Path, district_field: str, normalize_mode: str) -> DistrictIndex:
    reader = shapefile.Reader(str(shapefile_zip))
    fields = [f[0] for f in reader.fields[1:]]
    if district_field not in fields:
        raise ValueError(f"{district_field} not found in {shapefile_zip}")
    dist_idx = fields.index(district_field)

    geometries: list[object] = []
    districts: list[str] = []
    for shape_rec in reader.iterShapeRecords():
        district = normalize_district_value(str(shape_rec.record[dist_idx]), normalize_mode)
        if district == "":
            continue
        geom = shapely_shape(shape_rec.shape.__geo_interface__)
        if geom.is_empty:
            continue
        geometries.append(geom)
        districts.append(district)
    return DistrictIndex(geometries=geometries, districts=districts, tree=STRtree(geometries))


def locate_district(point: Point, index: DistrictIndex) -> str:
    for idx in index.tree.query(point):
        i = int(idx)
        if index.geometries[i].covers(point):
            return index.districts[i]
    nearest = index.tree.query_nearest(point)
    if getattr(nearest, "size", 0) > 0:
        return index.districts[int(nearest[0])]
    return ""


def load_district_maps_from_tabblocks(
    tabblocks_zip: Path,
    vtd_by_block: dict[str, VtdRef],
    cd_index: DistrictIndex,
    house_index: DistrictIndex,
    senate_index: DistrictIndex,
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, int]]:
    reader = shapefile.Reader(str(tabblocks_zip))
    fields = [f[0] for f in reader.fields[1:]]
    geoid_idx = fields.index("GEOID20")
    lon_idx = fields.index("INTPTLON20")
    lat_idx = fields.index("INTPTLAT20")

    cd_by_block: dict[str, str] = {}
    house_by_block: dict[str, str] = {}
    senate_by_block: dict[str, str] = {}
    misses = {"cd": 0, "house": 0, "senate": 0}

    for rec in reader.iterRecords():
        block = clean(str(rec[geoid_idx]))
        if block == "" or block not in vtd_by_block:
            continue
        try:
            x = float(rec[lon_idx])
            y = float(rec[lat_idx])
        except (TypeError, ValueError):
            continue

        pt = Point(x, y)

        cd = locate_district(pt, cd_index)
        if cd:
            cd_by_block[block] = cd
        else:
            misses["cd"] += 1

        house = locate_district(pt, house_index)
        if house:
            house_by_block[block] = house
        else:
            misses["house"] += 1

        senate = locate_district(pt, senate_index)
        if senate:
            senate_by_block[block] = senate
        else:
            misses["senate"] += 1

    return cd_by_block, house_by_block, senate_by_block, misses


def load_nhgis_2020_block_set(nhgis_zip: Path) -> set[str]:
    if not nhgis_zip.exists():
        return set()
    with zipfile.ZipFile(nhgis_zip) as zf:
        csv_member = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if not csv_member:
            return set()
        reader = csv.DictReader(io.StringIO(zf.read(csv_member).decode("utf-8", errors="replace")))
        out: set[str] = set()
        for row in reader:
            b = clean(row.get("blk2020ge"))
            if b.startswith("27"):
                out.add(b)
    return out


def district_sort_key(value: str) -> tuple[int, str]:
    s = clean(value).upper()
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits:
        return int(digits), s
    return 9999, s


def build_crosswalk_rows(
    vtd_by_block: dict[str, VtdRef],
    district_by_block: dict[str, str],
    precinct_key_map: dict[tuple[str, str], str],
) -> list[dict[str, str]]:
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[tuple[str, str], int] = defaultdict(int)

    for block_id, vtd_ref in vtd_by_block.items():
        district = district_by_block.get(block_id, "")
        if not district:
            continue
        key = (vtd_ref.countyfp, vtd_ref.vtdst20)
        counts[key][district] += 1
        totals[key] += 1

    rows: list[dict[str, str]] = []
    for key in sorted(counts.keys()):
        countyfp, vtdst20 = key
        total_blocks = totals[key]
        precinct_key = precinct_key_map.get(key, f"{countyfp} - {vtdst20}")
        for district in sorted(counts[key].keys(), key=district_sort_key):
            block_count = counts[key][district]
            weight = block_count / total_blocks if total_blocks else 0.0
            rows.append(
                {
                    "precinct_key": precinct_key,
                    "district_num": district,
                    "district_code": district,
                    "area_weight": f"{weight:.10f}",
                    "block_count": str(block_count),
                    "total_blocks": str(total_blocks),
                    "countyfp": countyfp,
                    "vtdst20": vtdst20,
                }
            )
    return rows


def write_crosswalk(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "precinct_key",
        "district_num",
        "district_code",
        "area_weight",
        "block_count",
        "total_blocks",
        "countyfp",
        "vtdst20",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_weights(rows: list[dict[str, str]]) -> tuple[int, int]:
    sums: dict[str, float] = defaultdict(float)
    for row in rows:
        sums[row["precinct_key"]] += float(row["area_weight"])
    ok = sum(1 for v in sums.values() if abs(v - 1.0) < 1e-6)
    bad = len(sums) - ok
    return ok, bad


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build MN precinct-to-district carryover crosswalk CSVs using BlockAssign for block->VTD "
            "and current district geometries for district assignment."
        )
    )
    parser.add_argument("--blockassign-zip", type=Path, default=Path("Data/BlockAssign_ST27_MN.zip"))
    parser.add_argument("--tabblocks-zip", type=Path, default=Path("Data/tl_2020_27_tabblock20.zip"))
    parser.add_argument("--cd-shapefile", type=Path, default=Path("Data/tl_2022_27_cd118.zip"))
    parser.add_argument("--state-house-shapefile", type=Path, default=Path("Data/tl_2024_27_sldl.zip"))
    parser.add_argument("--state-senate-shapefile", type=Path, default=Path("Data/tl_2024_27_sldu.zip"))
    parser.add_argument("--precincts-geojson", type=Path, default=Path("Data/precincts.geojson"))
    parser.add_argument("--nhgis-2020-2010-zip", type=Path, default=Path("Data/nhgis_blk2020_blk2010_27.zip"))
    parser.add_argument("--out-dir", type=Path, default=Path("Data/crosswalks"))
    args = parser.parse_args()

    precinct_key_map = load_precinct_key_map(args.precincts_geojson)
    vtd_by_block = load_vtd_by_block(args.blockassign_zip)
    cd_index = load_district_index(args.cd_shapefile, "CD118FP", "int")
    house_index = load_district_index(args.state_house_shapefile, "SLDLST", "upper")
    senate_index = load_district_index(args.state_senate_shapefile, "SLDUST", "int")
    cd_by_block, sldl_by_block, sldu_by_block, misses = load_district_maps_from_tabblocks(
        tabblocks_zip=args.tabblocks_zip,
        vtd_by_block=vtd_by_block,
        cd_index=cd_index,
        house_index=house_index,
        senate_index=senate_index,
    )

    cd_rows = build_crosswalk_rows(vtd_by_block, cd_by_block, precinct_key_map)
    house_rows = build_crosswalk_rows(vtd_by_block, sldl_by_block, precinct_key_map)
    senate_rows = build_crosswalk_rows(vtd_by_block, sldu_by_block, precinct_key_map)

    write_crosswalk(args.out_dir / "precinct_to_cd118.csv", cd_rows)
    write_crosswalk(args.out_dir / "precinct_to_2022_state_house.csv", house_rows)
    write_crosswalk(args.out_dir / "precinct_to_2024_state_house.csv", house_rows)
    write_crosswalk(args.out_dir / "precinct_to_2022_state_senate.csv", senate_rows)
    write_crosswalk(args.out_dir / "precinct_to_2024_state_senate.csv", senate_rows)

    cd_ok, cd_bad = validate_weights(cd_rows)
    h_ok, h_bad = validate_weights(house_rows)
    s_ok, s_bad = validate_weights(senate_rows)
    nhgis_blocks = load_nhgis_2020_block_set(args.nhgis_2020_2010_zip)
    nhgis_note = ""
    if nhgis_blocks:
        block_ids = set(vtd_by_block.keys())
        shared = len(block_ids & nhgis_blocks)
        nhgis_note = (
            f"\n  NHGIS 2020-block coverage check: "
            f"shared={shared} blockassign={len(block_ids)} nhgis={len(nhgis_blocks)}"
        )
    print(
        "Wrote crosswalks:\n"
        f"  precinct_to_cd118.csv rows={len(cd_rows)} precincts_ok={cd_ok} precincts_bad={cd_bad}\n"
        f"  precinct_to_2022_state_house.csv rows={len(house_rows)} precincts_ok={h_ok} precincts_bad={h_bad}\n"
        f"  precinct_to_2024_state_house.csv rows={len(house_rows)} precincts_ok={h_ok} precincts_bad={h_bad}\n"
        f"  precinct_to_2022_state_senate.csv rows={len(senate_rows)} precincts_ok={s_ok} precincts_bad={s_bad}\n"
        f"  precinct_to_2024_state_senate.csv rows={len(senate_rows)} precincts_ok={s_ok} precincts_bad={s_bad}\n"
        f"  block-centroid misses: cd={misses['cd']} house={misses['house']} senate={misses['senate']}"
        f"{nhgis_note}"
    )


if __name__ == "__main__":
    main()
