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


def load_blockassign_maps(blockassign_zip: Path) -> tuple[dict[str, VtdRef], dict[str, str], dict[str, str], dict[str, str]]:
    with zipfile.ZipFile(blockassign_zip) as zf:
        vtd_rows = read_pipe_rows(zf, "BlockAssign_ST27_MN_VTD.txt")
        cd_rows = read_pipe_rows(zf, "BlockAssign_ST27_MN_CD.txt")
        sldl_rows = read_pipe_rows(zf, "BlockAssign_ST27_MN_SLDL.txt")
        sldu_rows = read_pipe_rows(zf, "BlockAssign_ST27_MN_SLDU.txt")

    vtd_by_block: dict[str, VtdRef] = {}
    for row in vtd_rows:
        block = clean(row.get("BLOCKID"))
        countyfp = clean(row.get("COUNTYFP"))
        vtd = clean(row.get("DISTRICT"))
        if block and countyfp and vtd:
            vtd_by_block[block] = VtdRef(countyfp=countyfp, vtdst20=vtd)

    def dist_map(rows: list[dict[str, str]], normalize: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in rows:
            block = clean(row.get("BLOCKID"))
            district = clean(row.get("DISTRICT"))
            if not block or not district:
                continue
            if normalize == "int":
                district = str(int(district))
            elif normalize == "upper":
                district = district.upper()
            out[block] = district
        return out

    cd_by_block = dist_map(cd_rows, "int")
    sldl_by_block = dist_map(sldl_rows, "upper")
    sldu_by_block = dist_map(sldu_rows, "int")
    return vtd_by_block, cd_by_block, sldl_by_block, sldu_by_block


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
        description="Build MN precinct-to-district carryover crosswalk CSVs from Census BlockAssign files."
    )
    parser.add_argument("--blockassign-zip", type=Path, default=Path("Data/BlockAssign_ST27_MN.zip"))
    parser.add_argument("--precincts-geojson", type=Path, default=Path("Data/precincts.geojson"))
    parser.add_argument("--nhgis-2020-2010-zip", type=Path, default=Path("Data/nhgis_blk2020_blk2010_27.zip"))
    parser.add_argument("--out-dir", type=Path, default=Path("Data/crosswalks"))
    args = parser.parse_args()

    precinct_key_map = load_precinct_key_map(args.precincts_geojson)
    vtd_by_block, cd_by_block, sldl_by_block, sldu_by_block = load_blockassign_maps(args.blockassign_zip)

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
        f"  precinct_to_2024_state_senate.csv rows={len(senate_rows)} precincts_ok={s_ok} precincts_bad={s_bad}"
        f"{nhgis_note}"
    )


if __name__ == "__main__":
    main()
