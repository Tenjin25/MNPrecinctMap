#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


RESULT_FIELD_COUNT = 16


def clean(value: object) -> str:
    return str(value or "").strip()


def load_counties(path: Path) -> dict[str, str]:
    counties: dict[str, str] = {}
    with path.open(newline="", encoding="cp1252") as source:
        for line_number, row in enumerate(csv.reader(source, delimiter=";"), 1):
            if len(row) < 2:
                raise ValueError(f"{path}:{line_number}: expected at least 2 fields, got {len(row)}")
            counties[clean(row[0])] = clean(row[1])
    return counties


def load_precincts(path: Path) -> dict[tuple[str, str], str]:
    precincts: dict[tuple[str, str], str] = {}
    with path.open(newline="", encoding="cp1252") as source:
        for line_number, row in enumerate(csv.reader(source, delimiter=";"), 1):
            if len(row) < 3:
                raise ValueError(f"{path}:{line_number}: expected at least 3 fields, got {len(row)}")
            precincts[(clean(row[0]), clean(row[1]))] = clean(row[2])
    return precincts


def normalize_office(office: str) -> str:
    if office == "U.S. Senator":
        return "U.S. Senate"
    if office.startswith("U.S. Representative District "):
        return "U.S. House"
    if office.startswith("State Senator District "):
        return "State Senate"
    if office.startswith("State Representative District "):
        return "State House"
    return office


def normalize_candidate(office: str, candidate: str) -> str:
    if office == "Governor & Lt Governor" and candidate.upper() != "WRITE-IN":
        return candidate.split(" and ", 1)[0].strip()
    return candidate


def normalize_district(office: str, district: str) -> str:
    district = clean(district)
    if office in {"U.S. House", "State Senate"} and district:
        return str(int(district))
    if office == "State House" and district:
        return district.upper().lstrip("0") or "0"
    return district or "NA"


def convert(
    results_path: Path,
    counties_path: Path,
    precincts_path: Path,
    precinct_output_path: Path,
    county_output_path: Path,
    crosswalk_output_path: Path,
) -> tuple[int, int, int, int]:
    counties = load_counties(counties_path)
    precincts = load_precincts(precincts_path)
    county_totals: dict[tuple[str, str, str, str, str, str], int] = defaultdict(int)
    precinct_rows = 0
    source_vote_total = 0

    precinct_output_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        results_path.open(newline="", encoding="cp1252") as source,
        precinct_output_path.open("w", newline="", encoding="utf-8") as target,
    ):
        reader = csv.reader(source, delimiter=";")
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(["county", "precinct", "office", "district", "candidate", "party", "votes"])

        for line_number, row in enumerate(reader, 1):
            if len(row) != RESULT_FIELD_COUNT:
                raise ValueError(
                    f"{results_path}:{line_number}: expected {RESULT_FIELD_COUNT} fields, got {len(row)}"
                )

            state, county_id, precinct_id = map(clean, row[:3])
            if state != "MN":
                raise ValueError(f"{results_path}:{line_number}: unexpected state {state!r}")
            if county_id not in counties:
                raise ValueError(f"{results_path}:{line_number}: unknown county ID {county_id!r}")
            precinct_key = (county_id, precinct_id)
            if precinct_key not in precincts:
                raise ValueError(f"{results_path}:{line_number}: unknown precinct key {precinct_key!r}")

            source_office = clean(row[4])
            office = normalize_office(source_office)
            district = normalize_district(office, row[5])
            candidate = normalize_candidate(source_office, clean(row[7]))
            party = clean(row[10])
            reporting, total_precincts = clean(row[11]), clean(row[12])
            if reporting != total_precincts:
                raise ValueError(
                    f"{results_path}:{line_number}: incomplete result ({reporting} of {total_precincts})"
                )
            votes = int(clean(row[13]) or 0)
            county = counties[county_id]
            precinct = precincts[precinct_key]

            writer.writerow([county, precinct, office, district, candidate, party, votes])
            precinct_rows += 1
            source_vote_total += votes

            county_key = (county_id, county, office, district, party, candidate)
            county_totals[county_key] += votes
    county_contest_totals: dict[tuple[str, str, str], int] = defaultdict(int)
    for (county_id, _county, office, district, _party, _candidate), votes in county_totals.items():
        county_contest_totals[(county_id, office, district)] += votes

    county_output_path.parent.mkdir(parents=True, exist_ok=True)
    with county_output_path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(["county_code", "county", "office", "district", "party", "candidate", "votes", "pct"])
        for key in sorted(county_totals):
            county_id, county, office, district, party, candidate = key
            votes = county_totals[key]
            # Office titles are unique for the state/federal races consumed by the atlas.
            # For local races, sum all matching office-title/district rows in the county.
            denominator = county_contest_totals[(county_id, office, district)]
            pct = (votes / denominator * 100.0) if denominator else 0.0
            county_district = "" if district == "NA" else district
            writer.writerow([county_id, county, office, county_district, party, candidate, votes, f"{pct:.2f}"])

    crosswalk_output_path.parent.mkdir(parents=True, exist_ok=True)
    with crosswalk_output_path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(["year", "county", "source_precinct", "target_precinct"])
        for (county_id, precinct_id), precinct_name in sorted(precincts.items()):
            writer.writerow([2026, counties[county_id], precinct_name, precinct_id.zfill(6)])

    if sum(county_totals.values()) != source_vote_total:
        raise AssertionError("County aggregation did not conserve votes")
    return precinct_rows, len(county_totals), source_vote_total, len(precincts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Minnesota's 2026 semicolon-delimited precinct export to OpenElections CSVs."
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("Data/20261103__mn__general__precinct_raw.txt"),
    )
    parser.add_argument(
        "--counties",
        type=Path,
        default=Path("Data/20261103__mn__counties_raw.txt"),
    )
    parser.add_argument(
        "--precincts",
        type=Path,
        default=Path("Data/20261103__mn__precincts_raw.txt"),
    )
    parser.add_argument(
        "--precinct-output",
        type=Path,
        default=Path("Data/20261103__mn__general__precinct.csv"),
    )
    parser.add_argument(
        "--county-output",
        type=Path,
        default=Path("Data/20261103__mn__general__county.csv"),
    )
    parser.add_argument(
        "--crosswalk-output",
        type=Path,
        default=Path("Data/20261103__mn__general__precinct_crosswalk.csv"),
    )
    args = parser.parse_args()

    precinct_rows, county_rows, vote_total, crosswalk_rows = convert(
        args.results,
        args.counties,
        args.precincts,
        args.precinct_output,
        args.county_output,
        args.crosswalk_output,
    )
    print(
        f"Wrote {precinct_rows:,} precinct rows and {county_rows:,} county rows; "
        f"wrote {crosswalk_rows:,} precinct crosswalk rows, and conserved "
        f"{vote_total:,} candidate/response votes."
    )


if __name__ == "__main__":
    main()
