#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from convert_mn_legacy_results_to_openelections_precinct import (
    load_candidate_overrides,
    resolve_override_candidate,
)


STATEWIDE_OFFICES = {
    "PRESIDENT",
    "U.S. SENATE",
    "UNITED STATES SENATOR",
    "GOVERNOR",
    "GOVERNOR & LT GOVERNOR",
    "ATTORNEY GENERAL",
    "SECRETARY OF STATE",
    "STATE AUDITOR",
}


def clean(value: object) -> str:
    return str(value or "").strip()


def parse_int(value: object) -> int:
    token = clean(value).replace(",", "")
    return int(float(token)) if token else 0


def build_county_file(
    precinct_path: Path,
    output_path: Path,
    candidate_overrides: dict[tuple[int, str, str, str], str],
) -> int:
    year = int(precinct_path.name[:4])
    totals: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    with precinct_path.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            office = clean(row.get("office"))
            if office.upper() not in STATEWIDE_OFFICES:
                continue
            county = clean(row.get("county"))
            if not county:
                continue
            district = clean(row.get("district"))
            party = clean(row.get("party"))
            candidate = resolve_override_candidate(
                candidate_overrides,
                year=year,
                office=office,
                district=district,
                party=party,
            ) or clean(row.get("candidate"))
            key = (
                county,
                office,
                district,
                party,
                candidate,
            )
            totals[key] += parse_int(row.get("votes"))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(["county", "office", "district", "party", "candidate", "votes"])
        for key in sorted(totals, key=lambda item: (item[0], item[1], item[2], item[3], item[4])):
            writer.writerow([*key, totals[key]])
    return len(totals)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate corrected MN precinct OpenElections files into statewide-office county CSVs; "
            "historical U.S. House, State House, and State Senate races are excluded."
        )
    )
    parser.add_argument("--data-dir", type=Path, default=Path("Data"))
    parser.add_argument("--out-dir", type=Path, default=Path("Data/statewide_county_contests"))
    parser.add_argument("--year-min", type=int, default=2000)
    parser.add_argument("--year-max", type=int, default=2008)
    parser.add_argument(
        "--candidate-overrides",
        type=Path,
        default=Path("Data/candidate_overrides_legacy_statewide_corrections.csv"),
    )
    args = parser.parse_args()

    files: list[Path] = []
    for path in sorted(args.data_dir.glob("*__mn__general__precinct.csv")):
        if not path.name[:4].isdigit():
            continue
        year = int(path.name[:4])
        if args.year_min <= year <= args.year_max:
            files.append(path)
    if not files:
        raise SystemExit("No precinct OpenElections files matched the requested year range.")

    candidate_overrides = load_candidate_overrides(args.candidate_overrides)
    total_rows = 0
    for precinct_path in files:
        county_name = precinct_path.name.replace("__precinct.csv", "__county.csv")
        row_count = build_county_file(
            precinct_path,
            args.out_dir / county_name,
            candidate_overrides,
        )
        total_rows += row_count
        print(f"{precinct_path.name} -> {county_name}: {row_count} rows")
    print(f"Wrote {len(files)} statewide county CSVs with {total_rows} contest rows to {args.out_dir}")


if __name__ == "__main__":
    main()
