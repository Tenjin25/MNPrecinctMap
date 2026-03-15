#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ContestSpec:
    index: int
    office: str
    district_kind: str
    party: str
    candidate: str
    lookup_candidate: bool


@dataclass
class SourceSpec:
    year: int
    mode: str
    precinct_idx: int
    county_code_idx: int | None
    county_name_idx: int | None
    cg_idx: int | None
    leg_idx: int | None
    jd_idx: int | None
    header_rows: int


SOURCE_SPECS: dict[int, SourceSpec] = {
    2002: SourceSpec(
        year=2002,
        mode="2002_multi",
        precinct_idx=1,
        county_code_idx=16,
        county_name_idx=None,
        cg_idx=3,
        leg_idx=4,
        jd_idx=13,
        header_rows=3,
    ),
    2004: SourceSpec(
        year=2004,
        mode="2004_single",
        precinct_idx=1,
        county_code_idx=16,
        county_name_idx=None,
        cg_idx=3,
        leg_idx=4,
        jd_idx=13,
        header_rows=1,
    ),
    2006: SourceSpec(
        year=2006,
        mode="2006_single",
        precinct_idx=0,
        county_code_idx=10,
        county_name_idx=None,
        cg_idx=2,
        leg_idx=3,
        jd_idx=7,
        header_rows=1,
    ),
    2008: SourceSpec(
        year=2008,
        mode="2008_single",
        precinct_idx=0,
        county_code_idx=13,
        county_name_idx=None,
        cg_idx=2,
        leg_idx=3,
        jd_idx=6,
        header_rows=1,
    ),
    2010: SourceSpec(
        year=2010,
        mode="2010_single",
        precinct_idx=0,
        county_code_idx=8,
        county_name_idx=9,
        cg_idx=2,
        leg_idx=3,
        jd_idx=5,
        header_rows=1,
    ),
}


DEFAULT_JOBS = [
    {
        "year": 2002,
        "results": Path("Data/2002_general_results - Results.csv"),
        "output": Path("Data/20021105__mn__general__precinct.csv"),
        "counties": Path("Data/2002_general_results - Counties.csv"),
        "county_results": Path("Data/20021105__mn__general__county.csv"),
    },
    {
        "year": 2004,
        "results": Path("Data/2004_general_results.csv"),
        "output": Path("Data/20041102__mn__general__precinct.csv"),
        "counties": None,
        "county_results": Path("Data/20041102__mn__general__county.csv"),
    },
    {
        "year": 2006,
        "results": Path("Data/2006_general_results - Results.csv"),
        "output": Path("Data/20061107__mn__general__precinct.csv"),
        "counties": None,
        "county_results": Path("Data/20061107__mn__general__county.csv"),
    },
    {
        "year": 2008,
        "results": Path("Data/2008_general_results - Results.csv"),
        "output": Path("Data/20081104__mn__general__precinct.csv"),
        "counties": None,
        "county_results": Path("Data/20081104__mn__general__county.csv"),
    },
    {
        "year": 2010,
        "results": Path("Data/2010_general_results_final - Results.csv"),
        "output": Path("Data/20101102__mn__general__precinct.csv"),
        "counties": None,
        "county_results": Path("Data/20101102__mn__general__county.csv"),
    },
]


def clean(value: str) -> str:
    return (value or "").strip()


def get(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]


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
    m = re.match(r"^0*([0-9]+)([A-Z]?)$", token)
    if not m:
        return token
    return f"{int(m.group(1))}{m.group(2)}"


def normalize_senate_district(leg_value: str) -> str:
    token = clean(leg_value).upper()
    if token == "":
        return ""
    m = re.match(r"^0*([0-9]+)[A-Z]?$", token)
    if not m:
        return token
    return str(int(m.group(1)))


def district_value(kind: str, row: list[str], spec: SourceSpec) -> str:
    if kind == "na":
        return "NA"
    if kind == "cg":
        return strip_leading_zeroes(get(row, spec.cg_idx)) or "NA"
    if kind == "leg_house":
        return normalize_house_district(get(row, spec.leg_idx)) or "NA"
    if kind == "leg_senate":
        return normalize_senate_district(get(row, spec.leg_idx)) or "NA"
    if kind == "jd":
        jd = clean(get(row, spec.jd_idx))
        return jd.zfill(2) if jd else "NA"
    return "NA"


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def smart_title(name: str) -> str:
    words = clean(name).lower().split()
    if not words:
        return ""
    out: list[str] = []
    for i, w in enumerate(words):
        if i > 0 and w in {"and", "of", "the"}:
            out.append(w)
        elif w.startswith("mc") and len(w) > 2:
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def split_compound_name(token: str) -> str:
    t = clean(token).replace("_", " ")
    t = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", t)
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


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


def load_candidate_overrides(path: Path | None) -> dict[tuple[int, str, str, str], str]:
    overrides: dict[tuple[int, str, str, str], str] = {}
    if path is None or not path.exists():
        return overrides
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"year", "office", "district", "party", "candidate"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                f"Candidate overrides file {path} must include headers: year,office,district,party,candidate"
            )
        for row in reader:
            year_text = clean(row.get("year", ""))
            if year_text == "":
                continue
            year = int(year_text)
            office = clean(row.get("office", ""))
            district = clean(row.get("district", ""))
            party = clean(row.get("party", ""))
            candidate = clean(row.get("candidate", ""))
            if not office or not party or not candidate:
                continue
            overrides[(year, office, district, party)] = candidate
    return overrides


def resolve_override_candidate(
    overrides: dict[tuple[int, str, str, str], str],
    year: int,
    office: str,
    district: str,
    party: str,
) -> str:
    for key in [
        (year, office, district, party),
        (year, office, "NA", party),
        (year, office, "", party),
        (0, office, district, party),
        (0, office, "NA", party),
        (0, office, "", party),
    ]:
        value = overrides.get(key, "")
        if value:
            return value
    return ""


def make_partisan_contest(
    index: int,
    office: str,
    district_kind: str,
    party_token: str,
    party_map: dict[str, str],
    lookup: bool,
) -> ContestSpec | None:
    token = party_token.upper()
    if token in {"TOT", "TOTAL"}:
        return None
    party = party_map.get(token, token)
    candidate = "WRITE-IN" if party == "WI" else ""
    return ContestSpec(
        index=index,
        office=office,
        district_kind=district_kind,
        party=party,
        candidate=candidate,
        lookup_candidate=lookup and party != "WI",
    )


def build_2002_contests(row0: list[str], row2: list[str]) -> list[ContestSpec]:
    columns: list[ContestSpec] = []
    active_group = ""

    for i in range(27, len(row2)):
        group = clean(row0[i]) if i < len(row0) else ""
        if group:
            active_group = group
        alias = clean(row2[i])
        if not alias or alias == "Wellstone":
            continue

        handled = False
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
            if alias.startswith(prefix):
                token = alias[len(prefix) :].upper()
                party_map = {
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
                lookup = office in {
                    "U.S. Senate",
                    "Governor",
                    "Secretary of State",
                    "Attorney General",
                    "State Auditor",
                }
                spec = make_partisan_contest(i, office, district_kind, token, party_map, lookup)
                if spec is not None:
                    columns.append(spec)
                handled = True
                break
        if handled:
            continue

        if active_group.startswith("ASSOCIATE JUSTICE-SUPREME CT"):
            seat = clean(active_group.replace("ASSOCIATE JUSTICE-SUPREME CT", ""))
            is_wi = alias.upper().startswith("WRITE-IN")
            columns.append(
                ContestSpec(
                    index=i,
                    office=f"Associate Justice - Supreme Court {seat}" if seat else "Associate Justice - Supreme Court",
                    district_kind="na",
                    party="WI" if is_wi else "NP",
                    candidate="WRITE-IN" if is_wi else alias,
                    lookup_candidate=False,
                )
            )
            continue

        if active_group.startswith("APPEALS "):
            seat = clean(active_group.replace("APPEALS ", ""))
            is_wi = alias.upper().startswith("WRITE-IN")
            columns.append(
                ContestSpec(
                    index=i,
                    office=f"Judge - Court of Appeals {seat}" if seat else "Judge - Court of Appeals",
                    district_kind="na",
                    party="WI" if is_wi else "NP",
                    candidate="WRITE-IN" if is_wi else alias,
                    lookup_candidate=False,
                )
            )
            continue

        m = re.match(r"^([0-9]+)(?:ST|ND|RD|TH) DISTRICT COURT ([0-9]+)$", active_group)
        if m:
            district_num = int(m.group(1))
            seat = m.group(2)
            is_wi = alias.upper().startswith("WRITE-IN")
            columns.append(
                ContestSpec(
                    index=i,
                    office=f"Judge - {ordinal(district_num)} District Court {seat}",
                    district_kind="jd",
                    party="WI" if is_wi else "NP",
                    candidate="WRITE-IN" if is_wi else alias,
                    lookup_candidate=False,
                )
            )

    return columns


def build_2004_contests(headers: list[str]) -> list[ContestSpec]:
    columns: list[ContestSpec] = []
    for i, h in enumerate(headers):
        if i < 28:
            continue

        hu = h.upper()

        m = re.match(r"^USPRES([A-Z]+)$", hu)
        if m:
            party_map = {
                "GP": "GP",
                "R": "R",
                "DFL": "DFL",
                "SE": "SE",
                "SW": "SWP",
                "CF": "CF",
                "BL": "BL",
                "C": "C",
                "L": "LIB",
                "WI": "WI",
            }
            spec = make_partisan_contest(i, "President", "na", m.group(1), party_map, True)
            if spec:
                columns.append(spec)
            continue

        m = re.match(r"^USCONG([A-Z]+)$", hu)
        if m:
            party_map = {"GR": "GP", "I": "IND", "R": "R", "DFL": "DFL", "WI": "WI"}
            spec = make_partisan_contest(i, "U.S. House", "cg", m.group(1), party_map, False)
            if spec:
                columns.append(spec)
            continue

        m = re.match(r"^MNLEG([A-Z]+)$", hu)
        if m:
            party_map = {"GR": "GP", "I": "IND", "R": "R", "DFL": "DFL", "WI": "WI"}
            spec = make_partisan_contest(i, "State House", "leg_house", m.group(1), party_map, False)
            if spec:
                columns.append(spec)
            continue

        m = re.match(r"^AJSC([0-9]+)(.+)$", h)
        if m:
            seat, token = m.group(1), m.group(2)
            is_wi = token.upper() == "WI"
            columns.append(
                ContestSpec(
                    index=i,
                    office=f"Associate Justice - Supreme Court {seat}",
                    district_kind="na",
                    party="WI" if is_wi else "NP",
                    candidate="WRITE-IN" if is_wi else split_compound_name(token),
                    lookup_candidate=False,
                )
            )
            continue

        m = re.match(r"^JCOA([0-9]+)(.+)$", h)
        if m:
            seat, token = m.group(1), m.group(2)
            is_wi = token.upper() == "WI"
            columns.append(
                ContestSpec(
                    index=i,
                    office=f"Judge - Court of Appeals {seat}",
                    district_kind="na",
                    party="WI" if is_wi else "NP",
                    candidate="WRITE-IN" if is_wi else split_compound_name(token),
                    lookup_candidate=False,
                )
            )
            continue

    return columns


def build_2006_contests(headers: list[str]) -> list[ContestSpec]:
    columns: list[ContestSpec] = []
    for i, h in enumerate(headers):
        hu = h.upper()
        for prefix, office, district_kind, lookup in [
            ("USSEN", "U.S. Senate", "na", True),
            ("GOV", "Governor", "na", True),
            ("ATTGEN", "Attorney General", "na", True),
            ("SOS", "Secretary of State", "na", True),
            ("STAUD", "State Auditor", "na", True),
            ("CONG", "U.S. House", "cg", False),
            ("STATESEN", "State Senate", "leg_senate", False),
            ("STATEHOUSE", "State House", "leg_house", False),
        ]:
            if hu.startswith(prefix):
                token = hu[len(prefix) :]
                party_map = {"R": "R", "DFL": "DFL", "IP": "IND", "WI": "WI"}
                spec = make_partisan_contest(i, office, district_kind, token, party_map, lookup)
                if spec:
                    columns.append(spec)
                break
    return columns


def build_2008_contests(headers: list[str]) -> list[ContestSpec]:
    columns: list[ContestSpec] = []
    for i, h in enumerate(headers):
        hu = h.upper()
        if hu == "AMENDYES":
            columns.append(
                ContestSpec(
                    index=i,
                    office="Constitutional Amendment",
                    district_kind="na",
                    party="",
                    candidate="YES",
                    lookup_candidate=False,
                )
            )
            continue
        if hu == "AMENDNO":
            columns.append(
                ContestSpec(
                    index=i,
                    office="Constitutional Amendment",
                    district_kind="na",
                    party="",
                    candidate="NO",
                    lookup_candidate=False,
                )
            )
            continue

        for prefix, office, district_kind, lookup in [
            ("USPRES", "President", "na", True),
            ("USSEN", "U.S. Senate", "na", True),
            ("CONG", "U.S. House", "cg", False),
            ("MNSEN", "State Senate", "leg_senate", False),
            ("MNLEG", "State House", "leg_house", False),
        ]:
            if hu.startswith(prefix):
                token = hu[len(prefix) :]
                party_map = {
                    "R": "R",
                    "DFL": "DFL",
                    "GP": "GP",
                    "SWP": "SWP",
                    "IND": "IND",
                    "LIB": "LIB",
                    "CP": "CP",
                    "WI": "WI",
                    "IP": "IND",
                }
                spec = make_partisan_contest(i, office, district_kind, token, party_map, lookup)
                if spec:
                    columns.append(spec)
                break
    return columns


def build_2010_contests(headers: list[str]) -> list[ContestSpec]:
    columns: list[ContestSpec] = []
    for i, h in enumerate(headers):
        hu = h.upper()
        for prefix, office, district_kind, lookup in [
            ("CONG", "U.S. House", "cg", False),
            ("MNSEN", "State Senate", "leg_senate", False),
            ("MNLEG", "State House", "leg_house", False),
            ("GOV", "Governor", "na", True),
            ("SOS", "Secretary of State", "na", True),
            ("STAUD", "State Auditor", "na", True),
            ("ATGEN", "Attorney General", "na", True),
        ]:
            if hu.startswith(prefix):
                token = hu[len(prefix) :]
                party_map = {
                    "IP": "IND",
                    "R": "R",
                    "DFL": "DFL",
                    "GP": "GP",
                    "TRP": "TRP",
                    "GR": "GR",
                    "EDP": "EDP",
                    "WI": "WI",
                }
                spec = make_partisan_contest(i, office, district_kind, token, party_map, lookup)
                if spec:
                    columns.append(spec)
                break
    return columns


def normalize_headers(header_row: list[str]) -> list[str]:
    out: list[str] = []
    for h in header_row:
        token = re.sub(r"[\s\r\n]+", "", clean(h))
        out.append(token)
    return out


def build_contests(rows: list[list[str]], spec: SourceSpec) -> list[ContestSpec]:
    if spec.mode == "2002_multi":
        return build_2002_contests(rows[0], rows[2])
    headers = normalize_headers(rows[0])
    if spec.mode == "2004_single":
        return build_2004_contests(headers)
    if spec.mode == "2006_single":
        return build_2006_contests(headers)
    if spec.mode == "2008_single":
        return build_2008_contests(headers)
    if spec.mode == "2010_single":
        return build_2010_contests(headers)
    raise ValueError(f"Unsupported mode: {spec.mode}")


def convert_file(
    year: int,
    results_csv: Path,
    output_csv: Path,
    counties_csv: Path | None = None,
    county_results_csv: Path | None = None,
    candidate_overrides: dict[tuple[int, str, str, str], str] | None = None,
    unknown_candidate_policy: str = "blank",
    missing_candidates: Counter[tuple[int, str, str, str]] | None = None,
) -> tuple[int, int]:
    spec = SOURCE_SPECS[year]

    with results_csv.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if len(rows) <= spec.header_rows:
        raise ValueError(f"Input has no data rows: {results_csv}")

    contests = build_contests(rows, spec)
    data_rows = rows[spec.header_rows :]

    counties_map = load_counties_map(counties_csv)
    county_from_results, candidate_by_office_party = load_county_results_maps(county_results_csv)
    counties_map = {**counties_map, **county_from_results}

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    written_rows = 0
    if candidate_overrides is None:
        candidate_overrides = {}

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["county", "precinct", "office", "district", "candidate", "party", "votes"],
        )
        writer.writeheader()

        for row in data_rows:
            precinct = clean(get(row, spec.precinct_idx))
            if precinct == "":
                continue

            county_code = clean(get(row, spec.county_code_idx)).zfill(2)
            county = counties_map.get(county_code, "")
            if county == "":
                county_name_raw = clean(get(row, spec.county_name_idx))
                county = smart_title(county_name_raw) if county_name_raw else county_code

            for contest in contests:
                votes = parse_int(get(row, contest.index))
                if votes is None:
                    continue
                district = district_value(contest.district_kind, row, spec)
                candidate = contest.candidate
                if contest.lookup_candidate and candidate == "":
                    candidate = candidate_by_office_party.get((contest.office, contest.party), "")
                if candidate == "":
                    candidate = resolve_override_candidate(
                        candidate_overrides,
                        year=year,
                        office=contest.office,
                        district=district,
                        party=contest.party,
                    )
                if candidate == "":
                    if unknown_candidate_policy == "party_label":
                        candidate = f"{contest.party} Candidate" if contest.party else "Unknown Candidate"
                    elif unknown_candidate_policy == "unknown":
                        candidate = "Unknown Candidate"
                    elif missing_candidates is not None:
                        missing_candidates[(year, contest.office, district, contest.party)] += 1
                writer.writerow(
                    {
                        "county": county,
                        "precinct": precinct,
                        "office": contest.office,
                        "district": district,
                        "candidate": candidate,
                        "party": contest.party,
                        "votes": votes,
                    }
                )
                written_rows += 1

    return len(data_rows), written_rows


def write_missing_report(counter: Counter[tuple[int, str, str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["year", "office", "district", "party", "missing_rows"],
        )
        writer.writeheader()
        for (year, office, district, party), missing_rows in sorted(counter.items()):
            writer.writerow(
                {
                    "year": year,
                    "office": office,
                    "district": district,
                    "party": party,
                    "missing_rows": missing_rows,
                }
            )


def run_default_jobs(
    candidate_overrides: dict[tuple[int, str, str, str], str] | None = None,
    unknown_candidate_policy: str = "blank",
    missing_candidates: Counter[tuple[int, str, str, str]] | None = None,
) -> None:
    for job in DEFAULT_JOBS:
        year = job["year"]
        rows_in, rows_out = convert_file(
            year=year,
            results_csv=job["results"],
            output_csv=job["output"],
            counties_csv=job["counties"],
            county_results_csv=job["county_results"],
            candidate_overrides=candidate_overrides,
            unknown_candidate_policy=unknown_candidate_policy,
            missing_candidates=missing_candidates,
        )
        print(
            f"{year}: {job['results']} -> {job['output']} "
            f"({rows_in} source rows, {rows_out} output rows)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert MN legacy wide precinct CSVs (2002/2004/2006/2008/2010) into OpenElections-style precinct rows."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run built-in conversion jobs for 2002, 2004, 2006, 2008, and 2010.",
    )
    parser.add_argument("--year", type=int, choices=sorted(SOURCE_SPECS.keys()), help="Election year format selector")
    parser.add_argument("--results", type=Path, help="Input results CSV")
    parser.add_argument("--output", type=Path, help="Output precinct CSV")
    parser.add_argument("--counties", type=Path, default=None, help="Optional counties lookup CSV")
    parser.add_argument(
        "--county-results",
        type=Path,
        default=None,
        help="Optional county OpenElections CSV for county-name and candidate lookup",
    )
    parser.add_argument(
        "--candidate-overrides",
        type=Path,
        default=None,
        help="Optional CSV with columns year,office,district,party,candidate for missing-name backfills.",
    )
    parser.add_argument(
        "--unknown-candidate-policy",
        choices=["blank", "party_label", "unknown"],
        default="blank",
        help="How to fill candidates still missing after lookup/overrides.",
    )
    parser.add_argument(
        "--missing-report",
        type=Path,
        default=None,
        help="Optional output CSV path summarizing rows where candidate remains missing.",
    )
    args = parser.parse_args()

    candidate_overrides = load_candidate_overrides(args.candidate_overrides)
    missing_candidates: Counter[tuple[int, str, str, str]] = Counter()

    if args.all:
        run_default_jobs(
            candidate_overrides=candidate_overrides,
            unknown_candidate_policy=args.unknown_candidate_policy,
            missing_candidates=missing_candidates,
        )
        if args.unknown_candidate_policy == "blank":
            report_path = args.missing_report or Path("Data/missing_candidates_legacy_precincts.csv")
            write_missing_report(missing_candidates, report_path)
            print(f"Missing-candidate report written: {report_path} ({sum(missing_candidates.values())} rows)")
        return

    if args.year is None or args.results is None or args.output is None:
        parser.error("Use --all, or provide --year, --results, and --output.")

    rows_in, rows_out = convert_file(
        year=args.year,
        results_csv=args.results,
        output_csv=args.output,
        counties_csv=args.counties,
        county_results_csv=args.county_results,
        candidate_overrides=candidate_overrides,
        unknown_candidate_policy=args.unknown_candidate_policy,
        missing_candidates=missing_candidates,
    )
    print(f"{args.year}: {args.results} -> {args.output} ({rows_in} source rows, {rows_out} output rows)")
    if args.unknown_candidate_policy == "blank":
        report_path = args.missing_report or Path("Data/missing_candidates_legacy_precincts.csv")
        write_missing_report(missing_candidates, report_path)
        print(f"Missing-candidate report written: {report_path} ({sum(missing_candidates.values())} rows)")


if __name__ == "__main__":
    main()
