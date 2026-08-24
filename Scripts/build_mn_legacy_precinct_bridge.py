#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import shapefile

from build_mn_district_contests_from_precinct_and_baf import (
    build_alias_map,
    clean,
    load_crosswalk,
    make_alias_key,
    normalize_county_token,
    resolve_precinct_key,
)
from convert_mn_legacy_results_to_openelections_precinct import load_counties_map


@dataclass(frozen=True)
class SourceSpec:
    year: int
    results: Path
    counties: Path
    header_rows: int
    precinct_idx: int
    prct_idx: int
    county_code_idx: int


@dataclass(frozen=True)
class ManualOverride:
    target_kind: str
    target_key: str
    resolution: str
    evidence: str
    confidence: str


@dataclass(frozen=True)
class BridgeChoice:
    target_kind: str
    target_key: str
    resolution: str
    had_conflict: bool
    conflict_status: str
    resolution_source: str
    confidence: str
    evidence: str = ""


SOURCE_SPECS = [
    SourceSpec(2000, Path("Data/full_00results.csv"), Path("Data/2002_general_results - Counties.csv"), 1, 1, 17, 18),
    SourceSpec(
        2002,
        Path("Data/2002_general_results - Results.csv"),
        Path("Data/2002_general_results - Counties.csv"),
        3,
        1,
        15,
        16,
    ),
    SourceSpec(2004, Path("Data/2004_general_results.csv"), Path("Data/2002_general_results - Counties.csv"), 1, 1, 15, 16),
    SourceSpec(
        2006,
        Path("Data/2006_general_results - Results.csv"),
        Path("Data/2002_general_results - Counties.csv"),
        1,
        0,
        9,
        10,
    ),
    SourceSpec(
        2008,
        Path("Data/2008_general_results - Results.csv"),
        Path("Data/2002_general_results - Counties.csv"),
        1,
        0,
        1,
        13,
    ),
]


def load_vtd00_maps(path: Path) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    code_map: dict[tuple[str, str], str] = {}
    code_collisions: set[tuple[str, str]] = set()
    name_map: dict[str, str] = {}
    name_collisions: set[str] = set()

    def add_name_alias(alias: str, target: str) -> None:
        if not alias or alias in name_collisions:
            return
        existing = name_map.get(alias)
        if existing is None:
            name_map[alias] = target
        elif existing != target:
            name_collisions.add(alias)
            name_map.pop(alias, None)

    for feature in payload.get("features", []):
        props = feature.get("properties", {}) or {}
        county = clean(props.get("county_nam"))
        vtdst00 = clean(props.get("VTDST00") or props.get("prec_id"))
        if not county or not vtdst00:
            continue
        key = (normalize_county_token(county), vtdst00.zfill(4))
        target = f"{county} - {vtdst00}".upper()
        existing = code_map.get(key)
        if existing is None:
            code_map[key] = target
        elif existing != target:
            code_collisions.add(key)
            code_map.pop(key, None)

        for raw_name in (props.get("NAME00"), props.get("NAMELSAD00")):
            name = clean(raw_name)
            if not name:
                continue
            name = re.sub(r"\s+Voting District$", "", name, flags=re.IGNORECASE)
            add_name_alias(make_alias_key(county, name), target)

    return code_map, name_map


def load_county_name_by_fips(path: Path) -> dict[str, str]:
    county_by_code = load_counties_map(path)
    out: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    for row in rows[1:]:
        if len(row) < 4:
            continue
        county_code = clean(row[0]).zfill(2)
        county = county_by_code.get(county_code, "")
        countyfp = clean(row[3])[-3:]
        if county and countyfp:
            out[countyfp] = county
    return out


def load_vtd10_maps(
    path: Path,
    counties_path: Path,
) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    county_by_fips = load_county_name_by_fips(counties_path)
    reader = shapefile.Reader(str(path))
    fields = [field[0] for field in reader.fields[1:]]
    required = {"COUNTYFP10", "VTDST10", "NAME10"}
    if not required.issubset(fields):
        raise ValueError(f"Missing {sorted(required - set(fields))} in {path}")
    county_idx = fields.index("COUNTYFP10")
    vtd_idx = fields.index("VTDST10")
    name_idx = fields.index("NAME10")

    code_map: dict[tuple[str, str], str] = {}
    name_map: dict[str, str] = {}
    name_collisions: set[str] = set()
    for record in reader.iterRecords():
        countyfp = clean(record[county_idx])
        county = county_by_fips.get(countyfp, "")
        vtdst10 = clean(record[vtd_idx])
        name = clean(record[name_idx])
        if not county or not vtdst10:
            continue
        target = f"{county} - {vtdst10}".upper()
        code_map[(normalize_county_token(county), vtdst10.zfill(4))] = target
        alias = make_alias_key(county, name)
        if not alias or alias in name_collisions:
            continue
        existing = name_map.get(alias)
        if existing is None:
            name_map[alias] = target
        elif existing != target:
            name_collisions.add(alias)
            name_map.pop(alias, None)
    return code_map, name_map


def load_overrides(path: Path) -> dict[tuple[int, str], ManualOverride]:
    if not path.exists():
        return {}
    out: dict[tuple[int, str], ManualOverride] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            year = int(clean(row.get("year")) or 0)
            source_alias = clean(row.get("source_alias"))
            target_kind = clean(row.get("target_kind")).lower()
            target_key = clean(row.get("target_key")).upper()
            if not source_alias or target_kind not in {"current_vtd", "vtd00", "vtd10"} or not target_key:
                continue
            out[(year, source_alias)] = ManualOverride(
                target_kind=target_kind,
                target_key=target_key,
                resolution=clean(row.get("resolution")) or "manual_override",
                evidence=clean(row.get("evidence")),
                confidence=clean(row.get("confidence")) or "medium",
            )
    return out


def choose_target(
    year: int,
    name_key: str,
    code_key: str,
    vtd00_key: str,
    vtd00_name_key: str,
    vtd10_key: str,
    vtd10_name_key: str,
    override: ManualOverride | None,
) -> BridgeChoice:
    current_conflict = bool(name_key and code_key and name_key != code_key)
    vtd00_conflict = bool(vtd00_key and vtd00_name_key and vtd00_key != vtd00_name_key)
    vtd10_conflict = bool(vtd10_key and vtd10_name_key and vtd10_key != vtd10_name_key)
    had_conflict = vtd00_conflict if year == 2000 else (vtd10_conflict or current_conflict)

    if override is not None:
        return BridgeChoice(
            override.target_kind,
            override.target_key,
            override.resolution,
            True,
            "resolved_manual_override",
            "manual_override",
            override.confidence,
            override.evidence,
        )

    if year == 2000 and vtd00_key:
        return BridgeChoice(
            "vtd00",
            vtd00_key,
            "vtd00_code_primary",
            had_conflict,
            "resolved_vtd00_code" if vtd00_conflict else "no_conflict",
            "vtd00",
            "high",
        )
    if year == 2000 and vtd00_name_key:
        return BridgeChoice("vtd00", vtd00_name_key, "vtd00_name_primary", False, "no_conflict", "vtd00", "medium")

    if year > 2000 and vtd10_key and vtd10_name_key and vtd10_key == vtd10_name_key:
        return BridgeChoice(
            "vtd10",
            vtd10_key,
            "vtd10_name_code_agree",
            had_conflict,
            "resolved_vtd10_agreement" if current_conflict else "no_conflict",
            "vtd10",
            "high",
        )
    if year > 2000 and vtd10_conflict:
        return BridgeChoice(
            "vtd10",
            vtd10_name_key,
            "vtd10_name_over_code_unreviewed",
            True,
            "manual_review",
            "vtd10",
            "low",
        )
    if year > 2000 and vtd10_key:
        return BridgeChoice("vtd10", vtd10_key, "vtd10_code_only", had_conflict, "no_conflict", "vtd10", "medium")
    if year > 2000 and vtd10_name_key:
        return BridgeChoice("vtd10", vtd10_name_key, "vtd10_name_only", had_conflict, "no_conflict", "vtd10", "medium")

    if name_key and code_key and name_key == code_key:
        return BridgeChoice("current_vtd", name_key, "current_name_code_agree", had_conflict, "no_conflict", "current_vtd", "medium")
    if current_conflict:
        return BridgeChoice(
            "current_vtd",
            name_key,
            "current_name_over_code_conflict",
            True,
            "manual_review",
            "current_vtd",
            "low",
        )
    if code_key:
        return BridgeChoice("current_vtd", code_key, "current_code_only", had_conflict, "no_conflict", "current_vtd", "low")
    if name_key:
        return BridgeChoice("current_vtd", name_key, "current_name_only", had_conflict, "no_conflict", "current_vtd", "medium")
    if vtd00_key:
        return BridgeChoice("vtd00", vtd00_key, "vtd00_code_recovery", had_conflict, "no_conflict", "vtd00", "low")
    if vtd00_name_key:
        return BridgeChoice("vtd00", vtd00_name_key, "vtd00_name_recovery", had_conflict, "no_conflict", "vtd00", "low")
    return BridgeChoice("", "", "unresolved", had_conflict, "manual_review" if had_conflict else "unresolved", "", "low")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a year-aware bridge from legacy MN precinct labels and PRCT codes "
            "to current VTD, VTD10, or VTD00 allocation keys."
        )
    )
    parser.add_argument("--precincts-geojson", type=Path, default=Path("Data/precincts.geojson"))
    parser.add_argument("--vtd00-geojson", type=Path, default=Path("Data/vtds_2000.geojson"))
    parser.add_argument("--vtd10-shapefile", type=Path, default=Path("Data/tl_2012_27_vtd10.zip"))
    parser.add_argument("--counties", type=Path, default=Path("Data/2002_general_results - Counties.csv"))
    parser.add_argument("--current-crosswalk", type=Path, default=Path("Data/crosswalks/precinct_to_cd118.csv"))
    parser.add_argument(
        "--overrides",
        type=Path,
        default=Path("Data/crosswalks/legacy_precinct_overrides_2000_2008.csv"),
    )
    parser.add_argument("--out", type=Path, default=Path("Data/crosswalks/legacy_precinct_bridge_2000_2008.csv"))
    args = parser.parse_args()

    current = load_crosswalk("congressional", args.current_crosswalk)
    alias_map = build_alias_map(
        canonical_precinct_keys=set(current.by_precinct),
        key_to_county=current.precinct_key_to_county,
        key_to_vtd=current.precinct_key_to_vtd,
        tuple_to_precinct_key=current.tuple_to_precinct_key,
        precincts_geojson=args.precincts_geojson,
    )
    vtd00_by_code, vtd00_by_name = load_vtd00_maps(args.vtd00_geojson)
    vtd10_by_code, vtd10_by_name = load_vtd10_maps(args.vtd10_shapefile, args.counties)
    overrides = load_overrides(args.overrides)

    output_rows: list[dict[str, str]] = []
    alias_targets: dict[tuple[int, str], set[tuple[str, str]]] = defaultdict(set)

    for spec in SOURCE_SPECS:
        county_by_code = load_counties_map(spec.counties)
        with spec.results.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))[spec.header_rows :]

        seen_source_rows: set[tuple[str, str, str]] = set()
        for row in rows:
            if max(spec.precinct_idx, spec.prct_idx, spec.county_code_idx) >= len(row):
                continue
            precinct = clean(row[spec.precinct_idx])
            source_prct = clean(row[spec.prct_idx])
            source_county_code = clean(row[spec.county_code_idx]).zfill(2)
            county = county_by_code.get(source_county_code, "")
            if not precinct or not county or not source_prct.isdigit():
                continue
            if "STATEWIDE" in precinct.upper() or "STATE TOTAL" in precinct.upper():
                continue

            source_row_key = (county.upper(), precinct.upper(), source_prct)
            if source_row_key in seen_source_rows:
                continue
            seen_source_rows.add(source_row_key)

            source_alias = make_alias_key(county, precinct)
            name_key = resolve_precinct_key(county, precinct, alias_map)
            code_key = resolve_precinct_key(county, source_prct, alias_map)
            vtd00_key = vtd00_by_code.get(
                (normalize_county_token(county), str(int(source_prct)).zfill(4)),
                "",
            )
            vtd00_name_key = vtd00_by_name.get(source_alias, "")
            vtd10_key = vtd10_by_code.get(
                (normalize_county_token(county), str(int(source_prct)).zfill(4)),
                "",
            )
            vtd10_name_key = vtd10_by_name.get(source_alias, "")
            override = overrides.get((spec.year, source_alias)) or overrides.get((0, source_alias))
            choice = choose_target(
                spec.year,
                name_key,
                code_key,
                vtd00_key,
                vtd00_name_key,
                vtd10_key,
                vtd10_name_key,
                override,
            )
            alias_targets[(spec.year, source_alias)].add((choice.target_kind, choice.target_key))
            output_rows.append(
                {
                    "year": str(spec.year),
                    "county": county,
                    "precinct": precinct,
                    "source_county_code": source_county_code,
                    "source_prct": source_prct,
                    "source_alias": source_alias,
                    "current_name_key": name_key,
                    "current_code_key": code_key,
                    "vtd00_key": vtd00_key,
                    "vtd00_name_key": vtd00_name_key,
                    "vtd10_key": vtd10_key,
                    "vtd10_name_key": vtd10_name_key,
                    "target_kind": choice.target_kind,
                    "target_key": choice.target_key,
                    "resolution": choice.resolution,
                    "conflict": "1" if choice.had_conflict else "0",
                    "conflict_status": choice.conflict_status,
                    "resolution_source": choice.resolution_source,
                    "confidence": choice.confidence,
                    "evidence": choice.evidence,
                    "alias_collision": "0",
                }
            )

    collision_keys = {key for key, targets in alias_targets.items() if len(targets) > 1}
    for row in output_rows:
        key = (int(row["year"]), row["source_alias"])
        if key in collision_keys:
            row["alias_collision"] = "1"
            row["target_kind"] = ""
            row["target_key"] = ""
            row["resolution"] = "source_alias_collision"
            row["conflict"] = "1"
            row["conflict_status"] = "manual_review"
            row["resolution_source"] = ""
            row["confidence"] = "low"

    output_rows.sort(
        key=lambda row: (
            int(row["year"]),
            row["county"].upper(),
            row["precinct"].upper(),
            row["source_prct"],
        )
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "year",
        "county",
        "precinct",
        "source_county_code",
        "source_prct",
        "source_alias",
        "current_name_key",
        "current_code_key",
        "vtd00_key",
        "vtd00_name_key",
        "vtd10_key",
        "vtd10_name_key",
        "target_kind",
        "target_key",
        "resolution",
        "conflict",
        "conflict_status",
        "resolution_source",
        "confidence",
        "evidence",
        "alias_collision",
    ]
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    resolution_counts = Counter(row["resolution"] for row in output_rows)
    print(
        f"Wrote {args.out} with {len(output_rows)} rows\n"
        f"  resolutions={dict(sorted(resolution_counts.items()))}\n"
        f"  conflicts={sum(row['conflict'] == '1' for row in output_rows)} "
        f"manual_review={sum(row['conflict_status'] == 'manual_review' for row in output_rows)} "
        f"alias_collisions={len(collision_keys)}"
    )


if __name__ == "__main__":
    main()
