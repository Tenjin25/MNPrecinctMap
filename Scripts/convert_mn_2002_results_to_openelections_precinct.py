#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

METADATA_COLS = 18
FIRST_CONTEST_COL = 27


@dataclass
class ContestColumn:
    index: int
    office: str
    district_kind: str
    party: str
    candidate: str
    lookup_candidate: bool


def clean(value: str) -> str:
    return (value or "").strip()


def parse_int(value: str) -> int | None:
    token = clean(value).replace(",", "")
    if token == "":
        return None
    try:
        return int(token)
    except ValueError:
        return None


def strip_leading_zeroes(num_text: str) -> str:
    token = clean(num_text)
    if token == "":
        return ""
    try:
        return str(int(token))
    except ValueError:
        return token


def normalize_house_district(leg_value: str) -> str:
    token = clean(leg_value).upper()
    if token == "":
        return ""
    match = re.match(r"^0*([0-9]+)([A-Z]?)$", token)
    if not match:
        return token
    number = str(int(match.group(1)))
    suffix = match.group(2)
    return f"{number}{suffix}"


def normalize_senate_district(leg_value: str) -> str:
    token = clean(leg_value).upper()
    if token == "":
        return ""
    match = re.match(r"^0*([0-9]+)[A-Z]?$", token)
    if not match:
        return token
    return str(int(match.group(1)))


def district_value(kind: str, row: list[str]) -> str:
    if kind == "na":
        return "NA"
    if kind == "cg":
        return strip_leading_zeroes(row[3]) or "NA"
    if kind == "leg_house":
        return normalize_house_district(row[4]) or "NA"
    if kind == "leg_senate":
        return normalize_senate_district(row[4]) or "NA"
    if kind == "jd":
        jd = clean(row[13])
        return jd.zfill(2) if jd else "NA"
    return "NA"


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def parse_party_from_token(token: str) -> str:
    mapping = {
        "GP": "GP",
        "IP": "IND",
        "R": "R",
        "DFL": "DFL",
        "CP": "CP",
        "WI": "WI",
        "NNT": "NNT",
        "ICP": "ICP",
        "I": "I",
        "SW": "SWP",
    }
    return mapping.get(token.upper(), token.upper())


def parse_party_column(alias: str, prefix: str) -> tuple[str, str] | None:
    if not alias.startswith(prefix):
        return None
    token = alias[len(prefix) :]
    if token == "":
        return None
    return parse_party_from_token(token), token


def build_partisan_column(
    alias: str,
    prefix: str,
    office: str,
    district_kind: str,
    candidate_lookup_offices: set[str],
) -> ContestColumn | None:
    parsed = parse_party_column(alias, prefix)
    if parsed is None:
        return None
    party, token = parsed
    if token == "Wellstone":
        return None
    candidate = "WRITE-IN" if party == "WI" else ""
    lookup_candidate = office in candidate_lookup_offices and party != "WI"
    return ContestColumn(
        index=-1,
        office=office,
        district_kind=district_kind,
        party=party,
        candidate=candidate,
        lookup_candidate=lookup_candidate,
    )


def build_judicial_column(contest_group: str, alias: str) -> ContestColumn | None:
    if contest_group.startswith("ASSOCIATE JUSTICE-SUPREME CT"):
        seat = clean(contest_group.replace("ASSOCIATE JUSTICE-SUPREME CT", ""))
        office = f"Associate Justice - Supreme Court {seat}" if seat else "Associate Justice - Supreme Court"
        is_write_in = alias.upper().startswith("WRITE-IN")
        return ContestColumn(
            index=-1,
            office=office,
            district_kind="na",
            party="WI" if is_write_in else "NP",
            candidate="WRITE-IN" if is_write_in else alias,
            lookup_candidate=False,
        )

    if contest_group.startswith("APPEALS "):
        seat = clean(contest_group.replace("APPEALS ", ""))
        office = f"Judge - Court of Appeals {seat}" if seat else "Judge - Court of Appeals"
        is_write_in = alias.upper().startswith("WRITE-IN")
        return ContestColumn(
            index=-1,
            office=office,
            district_kind="na",
            party="WI" if is_write_in else "NP",
            candidate="WRITE-IN" if is_write_in else alias,
            lookup_candidate=False,
        )

    m = re.match(r"^([0-9]+)(?:ST|ND|RD|TH) DISTRICT COURT ([0-9]+)$", contest_group)
    if m:
        district_num = int(m.group(1))
        seat = m.group(2)
        office = f"Judge - {ordinal(district_num)} District Court {seat}"
        is_write_in = alias.upper().startswith("WRITE-IN")
        return ContestColumn(
            index=-1,
            office=office,
            district_kind="jd",
            party="WI" if is_write_in else "NP",
            candidate="WRITE-IN" if is_write_in else alias,
            lookup_candidate=False,
        )

    return None


def build_contest_column(contest_group: str, alias: str) -> ContestColumn | None:
    candidate_lookup_offices = {
        "U.S. Senate",
        "Governor",
        "Secretary of State",
        "Attorney General",
        "State Auditor",
    }

    if alias == "Wellstone":
        return None

    for prefix, office, district_kind in [
        ("USSen", "U.S. Senate", "na"),
        ("Cong", "U.S. House", "cg"),
        ("MNSen", "State Senate", "leg_senate"),
        ("MNleg", "State House", "leg_house"),
        ("Gov", "Governor", "na"),
        ("SOS", "Secretary of State", "na"),
        ("AG", "Attorney General", "na"),
        ("Aud", "State Auditor", "na"),
    ]:
        col = build_partisan_column(alias, prefix, office, district_kind, candidate_lookup_offices)
        if col is not None:
            return col

    return build_judicial_column(contest_group, alias)


def build_contest_columns(row0: list[str], row2: list[str]) -> list[ContestColumn]:
    columns: list[ContestColumn] = []
    active_group = ""
    for i in range(FIRST_CONTEST_COL, len(row2)):
        group = clean(row0[i]) if i < len(row0) else ""
        if group:
            active_group = group
        alias = clean(row2[i])
        if not alias:
            continue
        col = build_contest_column(active_group, alias)
        if col is None:
            continue
        col.index = i
        columns.append(col)
    return columns


def smart_title(name: str) -> str:
    words = clean(name).lower().split()
    if not words:
        return ""
    out = []
    for i, w in enumerate(words):
        if i > 0 and w in {"and", "of", "the"}:
            out.append(w)
        elif w.startswith("mc") and len(w) > 2:
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def load_counties_map(path: Path | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if path is None or not path.exists():
        return mapping
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 3:
                continue
            code = clean(row[0]).zfill(2)
            county = smart_title(row[2])
            if code and county:
                mapping[code] = county
    return mapping


def load_county_results_maps(path: Path | None) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    county_by_code: dict[str, str] = {}
    candidate_by_office_party: dict[tuple[str, str], str] = {}
    if path is None or not path.exists():
        return county_by_code, candidate_by_office_party
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            county_code = clean(row.get("county_code", "")).zfill(2)
            county_name = clean(row.get("county", ""))
            if county_code and county_name:
                county_by_code[county_code] = county_name

            office = clean(row.get("office", ""))
            party = clean(row.get("party", ""))
            candidate = clean(row.get("candidate", ""))
            if office and party and candidate and (office, party) not in candidate_by_office_party:
                candidate_by_office_party[(office, party)] = candidate
    return county_by_code, candidate_by_office_party


def convert(
    results_csv: Path,
    output_csv: Path,
    counties_csv: Path | None,
    county_results_csv: Path | None,
) -> tuple[int, int]:
    with results_csv.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if len(rows) < 4:
        raise ValueError(f"Expected at least 4 rows in {results_csv}, got {len(rows)}")

    row0 = rows[0]
    row2 = rows[2]
    data_rows = rows[3:]
    contest_cols = build_contest_columns(row0, row2)

    counties_map = load_counties_map(counties_csv)
    county_from_results, candidate_by_office_party = load_county_results_maps(county_results_csv)
    counties_map = {**counties_map, **county_from_results}

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    written_rows = 0

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["county", "precinct", "office", "district", "candidate", "party", "votes"],
        )
        writer.writeheader()

        for row in data_rows:
            if len(row) < METADATA_COLS:
                continue

            county_code = clean(row[16]).zfill(2)
            county = counties_map.get(county_code, county_code)
            precinct = clean(row[1])
            if precinct == "":
                continue

            for col in contest_cols:
                if col.index >= len(row):
                    continue
                votes = parse_int(row[col.index])
                if votes is None:
                    continue
                candidate = col.candidate
                if col.lookup_candidate and candidate == "":
                    candidate = candidate_by_office_party.get((col.office, col.party), "")
                writer.writerow(
                    {
                        "county": county,
                        "precinct": precinct,
                        "office": col.office,
                        "district": district_value(col.district_kind, row),
                        "candidate": candidate,
                        "party": col.party,
                        "votes": votes,
                    }
                )
                written_rows += 1

    return len(data_rows), written_rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Minnesota 2002 wide precinct results CSV into OpenElections-style precinct rows."
    )
    parser.add_argument("--results", required=True, type=Path, help="Input '...Results.csv' file")
    parser.add_argument("--output", required=True, type=Path, help="Output OpenElections-style precinct CSV")
    parser.add_argument(
        "--counties",
        type=Path,
        default=None,
        help="Optional counties lookup CSV (e.g., '...Counties.csv')",
    )
    parser.add_argument(
        "--county-results",
        type=Path,
        default=None,
        help="Optional OpenElections county CSV to backfill county names and statewide candidate names",
    )
    args = parser.parse_args()

    input_rows, output_rows = convert(
        results_csv=args.results,
        output_csv=args.output,
        counties_csv=args.counties,
        county_results_csv=args.county_results,
    )
    print(
        f"Converted {args.results} ({input_rows} precinct rows) -> {args.output} "
        f"with {output_rows} contest rows."
    )


if __name__ == "__main__":
    main()
