#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


CONTEST_OFFICES = {
    "PRESIDENT": "president",
    "U.S. SENATE": "us_senate",
    "UNITED STATES SENATOR": "us_senate",
    "GOVERNOR": "governor",
    "GOVERNOR & LT GOVERNOR": "governor",
    "ATTORNEY GENERAL": "attorney_general",
    "SECRETARY OF STATE": "secretary_of_state",
    "STATE AUDITOR": "auditor",
}
DEM_PARTIES = {"DFL", "DEM", "D"}
REP_PARTIES = {"R", "REP"}
EXCLUDED_DISTRICT_CONTESTS = {"us_house", "state_house", "state_senate"}
EXPECTED_DISTRICTS = {"congressional": 8, "state_house": 134, "state_senate": 67}
EXPECTED_CANDIDATES = {
    ("attorney_general", 2002): {"rep": "Tom Kelly"},
    ("secretary_of_state", 2002): {"dem": 'Hubert H. "Buck" Humphrey'},
    ("auditor", 2002): {"dem": "Carol Johnson"},
}


def clean(value: object) -> str:
    return str(value or "").strip()


def parse_int(value: object) -> int:
    token = clean(value).replace(",", "")
    return int(float(token)) if token else 0


def county_key(value: object) -> str:
    token = clean(value).split(" - ", 1)[0].upper().replace(" COUNTY", "")
    token = token.replace("&", " AND ")
    return re.sub(r"[^A-Z0-9]+", "", token)


def party_bucket(party: object) -> str:
    token = clean(party).upper()
    if token in DEM_PARTIES:
        return "dem"
    if token in REP_PARTIES:
        return "rep"
    return "other"


def load_manifest(directory: Path) -> list[dict[str, object]]:
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    files = payload.get("files", [])
    if not isinstance(files, list):
        raise ValueError(f"Manifest files must be a list: {directory / 'manifest.json'}")
    return [entry for entry in files if isinstance(entry, dict)]


def load_county_reference(data_dir: Path, year_min: int, year_max: int) -> dict[tuple[str, int], dict[str, dict[str, int]]]:
    out: dict[tuple[str, int], dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    )
    for path in sorted(data_dir.glob("*__mn__general__precinct.csv")):
        if not path.name[:4].isdigit():
            continue
        year = int(path.name[:4])
        if year < year_min or year > year_max:
            continue
        with path.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                contest_type = CONTEST_OFFICES.get(clean(row.get("office")).upper())
                county = county_key(row.get("county"))
                if not contest_type or not county:
                    continue
                out[(contest_type, year)][county][party_bucket(row.get("party"))] += parse_int(row.get("votes"))
    return out


def load_county_exports(directory: Path, year_min: int, year_max: int) -> dict[tuple[str, int], dict[str, dict[str, int]]]:
    out: dict[tuple[str, int], dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    )
    for path in sorted(directory.glob("*__mn__general__county.csv")):
        if not path.name[:4].isdigit():
            continue
        year = int(path.name[:4])
        if year < year_min or year > year_max:
            continue
        with path.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                contest_type = CONTEST_OFFICES.get(clean(row.get("office")).upper())
                county = county_key(row.get("county"))
                if not contest_type or not county:
                    continue
                out[(contest_type, year)][county][party_bucket(row.get("party"))] += parse_int(row.get("votes"))
    return out


def aggregate_contest_rows(rows: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
    for row in rows:
        county = county_key(row.get("county"))
        if not county:
            continue
        dem = parse_int(row.get("dem_votes"))
        rep = parse_int(row.get("rep_votes"))
        other = parse_int(row.get("other_votes"))
        total = parse_int(row.get("total_votes"))
        if total != dem + rep + other:
            raise ValueError(f"Row total mismatch for {row.get('county')}: {total} != {dem + rep + other}")
        out[county]["dem"] += dem
        out[county]["rep"] += rep
        out[county]["other"] += other
    return out


def statewide_totals(counties: dict[str, dict[str, int]]) -> tuple[int, int, int]:
    return tuple(sum(node[bucket] for node in counties.values()) for bucket in ("dem", "rep", "other"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the promoted 2000-2008 MN statewide contest slices.")
    parser.add_argument("--data-dir", type=Path, default=Path("Data"))
    parser.add_argument("--statewide-county-dir", type=Path, default=Path("Data/statewide_county_contests"))
    parser.add_argument("--contests-dir", type=Path, default=Path("Data/contests"))
    parser.add_argument("--district-contests-dir", type=Path, default=Path("Data/district_contests"))
    parser.add_argument("--year-min", type=int, default=2000)
    parser.add_argument("--year-max", type=int, default=2008)
    args = parser.parse_args()

    errors: list[str] = []
    reference = load_county_reference(args.data_dir, args.year_min, args.year_max)
    expected_pairs = set(reference)
    county_exports = load_county_exports(args.statewide_county_dir, args.year_min, args.year_max)
    if county_exports != reference:
        errors.append("Generated statewide county CSVs do not reconcile to the precinct OpenElections inputs.")
    contest_entries = load_manifest(args.contests_dir)
    contest_window = [
        entry
        for entry in contest_entries
        if args.year_min <= int(entry.get("year", 0)) <= args.year_max
        and str(entry.get("contest_type", "")) in set(CONTEST_OFFICES.values())
    ]
    contest_keys = [(str(entry.get("contest_type", "")), int(entry.get("year", 0))) for entry in contest_window]
    if len(contest_keys) != len(set(contest_keys)):
        errors.append("Duplicate contest manifest keys in the requested year window.")
    if set(contest_keys) != expected_pairs:
        errors.append(f"Contest manifest keys differ: expected {sorted(expected_pairs)}, got {sorted(set(contest_keys))}")

    reference_totals: dict[tuple[str, int], tuple[int, int, int]] = {}
    county_counts: list[int] = []
    for key in sorted(expected_pairs):
        reference_counties = reference[key]
        reference_totals[key] = statewide_totals(reference_counties)
        matching = [entry for entry in contest_window if (entry.get("contest_type"), int(entry.get("year", 0))) == key]
        if len(matching) != 1:
            continue
        entry = matching[0]
        path = args.contests_dir / str(entry.get("file", ""))
        if not path.exists():
            errors.append(f"Missing contest file: {path}")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload_rows = payload.get("rows", [])
        actual_counties = aggregate_contest_rows(payload_rows)
        county_counts.append(len(actual_counties))
        if actual_counties != reference_counties:
            bad = sorted(set(actual_counties) | set(reference_counties))
            bad = [county for county in bad if actual_counties.get(county) != reference_counties.get(county)]
            errors.append(f"County reconciliation failed for {key}: {bad[:10]}")
        manifest_totals = (
            parse_int(entry.get("dem_total")),
            parse_int(entry.get("rep_total")),
            parse_int(entry.get("other_total")),
        )
        if manifest_totals != reference_totals[key]:
            errors.append(f"Contest manifest totals failed for {key}: {manifest_totals} != {reference_totals[key]}")
        for bucket, expected_name in EXPECTED_CANDIDATES.get(key, {}).items():
            field = f"{bucket}_candidate"
            labels = {clean(row.get(field)) for row in payload_rows if clean(row.get(field))}
            if labels != {expected_name}:
                errors.append(f"Candidate correction failed for {key} {bucket}: {sorted(labels)}")

    district_entries = load_manifest(args.district_contests_dir)
    district_window = [
        entry for entry in district_entries if args.year_min <= int(entry.get("year", 0)) <= args.year_max
    ]
    excluded = [entry for entry in district_window if str(entry.get("contest_type", "")) in EXCLUDED_DISTRICT_CONTESTS]
    if excluded:
        errors.append(f"Excluded historical district contests are still manifested: {len(excluded)}")

    district_keep = [
        entry
        for entry in district_window
        if str(entry.get("contest_type", "")) in set(CONTEST_OFFICES.values())
    ]
    expected_district_keys = {
        (scope, contest_type, year)
        for scope in EXPECTED_DISTRICTS
        for contest_type, year in expected_pairs
    }
    actual_district_keys = [
        (str(entry.get("scope", "")), str(entry.get("contest_type", "")), int(entry.get("year", 0)))
        for entry in district_keep
    ]
    if len(actual_district_keys) != len(set(actual_district_keys)):
        errors.append("Duplicate district manifest keys in the requested year window.")
    if set(actual_district_keys) != expected_district_keys:
        errors.append(
            f"District manifest keys differ: expected {len(expected_district_keys)}, got {len(set(actual_district_keys))}"
        )

    for entry in district_keep:
        scope = str(entry.get("scope", ""))
        contest_type = str(entry.get("contest_type", ""))
        year = int(entry.get("year", 0))
        key = (contest_type, year)
        path = args.district_contests_dir / str(entry.get("file", ""))
        if not path.exists():
            errors.append(f"Missing district file: {path}")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows_node = payload.get("general", {}).get("results", {})
        rows = list(rows_node.values()) if isinstance(rows_node, dict) else []
        if len(rows) != EXPECTED_DISTRICTS.get(scope, -1):
            errors.append(f"District row count failed for {(scope, contest_type, year)}: {len(rows)}")
        row_totals = (
            sum(parse_int(row.get("dem_votes")) for row in rows),
            sum(parse_int(row.get("rep_votes")) for row in rows),
            sum(parse_int(row.get("other_votes")) for row in rows),
        )
        if row_totals != reference_totals.get(key):
            errors.append(f"District total reconciliation failed for {(scope, contest_type, year)}")
        if abs(float(entry.get("match_coverage_pct", 0)) - 100.0) > 0.0001:
            errors.append(f"District coverage is not 100% for {(scope, contest_type, year)}")
        if float(entry.get("legacy_manual_review_conflict_pct", 0)) != 0.0:
            errors.append(f"Manual-review conflict votes remain for {(scope, contest_type, year)}")
        if parse_int(payload.get("meta", {}).get("vote_conservation_delta")) != 0:
            errors.append(f"Vote conservation failed for {(scope, contest_type, year)}")
        for bucket, expected_name in EXPECTED_CANDIDATES.get(key, {}).items():
            field = f"{bucket}_candidate"
            labels = {clean(row.get(field)) for row in rows if clean(row.get(field))}
            if labels != {expected_name}:
                errors.append(
                    f"District candidate correction failed for {(scope, contest_type, year)} {bucket}: {sorted(labels)}"
                )

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)

    print(
        f"Validated 5 county CSVs, {len(contest_window)} county/precinct slices, and {len(district_keep)} district slices; "
        f"county counts {min(county_counts)}-{max(county_counts)}, zero reconciliation errors."
    )


if __name__ == "__main__":
    main()
