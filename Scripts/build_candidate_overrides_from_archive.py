#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

ARCHIVE_SEARCH_URL = "https://mnelectionarchive.datamade.us/search/"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
}

OFFICE_TO_ARCHIVE_CODE = {
    "President": "10",
    "U.S. Senate": "11",
    "U.S. House": "12",
    "Governor": "20",
    "Secretary of State": "22",
    "Attorney General": "23",
    "State Auditor": "24",
    "State Senate": "70",
    "State House": "71",
}

ARCHIVE_PARTY_TO_CODE = {
    "republican": "R",
    "democratic-farmer-labor": "DFL",
    "democrat": "DFL",
    "independence": "IND",
    "independent": "I",
    "independent christian pro-life": "ICP",
    "green": "GP",
    "libertarian": "LIB",
    "constitution": "CP",
    "socialist workers": "SWP",
    "socialist equality": "SE",
    "christian freedom": "CF",
    "better life": "BL",
    "resource": "TRP",
    "grassroots": "GR",
    "ecology democracy": "EDP",
    "no new taxes": "NNT",
    "write-in": "WI",
    "write-in (sticker)": "WI",
    "nonpartisan": "NP",
}


@dataclass(frozen=True)
class MissingKey:
    year: int
    office: str
    district: str
    party: str


def clean(value: str) -> str:
    return (value or "").strip()


def normalize_district(office: str, district: str) -> str:
    d = clean(district).upper()
    if d in {"", "NA"}:
        return "NA"

    if office == "State House":
        m = re.match(r"^(\d+)([A-Z]?)$", d)
        if m:
            return f"{int(m.group(1))}{m.group(2)}"

    if office in {"State Senate", "U.S. House"}:
        m = re.match(r"^(\d+)$", d)
        if m:
            return str(int(m.group(1)))

    return d


def parse_district_from_office_cell(office_text: str, office: str) -> str:
    m = re.search(r"District\s+([0-9]{1,2}[A-Z]?)", office_text)
    if not m:
        return "NA"
    return normalize_district(office, m.group(1))


def normalize_candidate(candidate: str) -> str:
    c = clean(candidate)
    c = re.sub(r"\s+Incumbent$", "", c, flags=re.IGNORECASE)
    if c.lower().startswith("scattering"):
        return "WRITE-IN"
    return c


def party_text_to_code(party_text: str) -> str:
    return ARCHIVE_PARTY_TO_CODE.get(clean(party_text).lower(), "")


def post_archive_search(form_data: dict[str, str]) -> BeautifulSoup:
    encoded = urllib.parse.urlencode(form_data).encode()
    req = urllib.request.Request(ARCHIVE_SEARCH_URL, data=encoded, headers=REQUEST_HEADERS)
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
    return BeautifulSoup(html, "html.parser")


def load_missing_keys(missing_report_csv: Path) -> tuple[list[MissingKey], set[tuple[int, str]]]:
    keys: list[MissingKey] = []
    year_office: set[tuple[int, str]] = set()
    with missing_report_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            office = clean(row["office"])
            if office not in OFFICE_TO_ARCHIVE_CODE:
                continue
            key = MissingKey(
                year=int(row["year"]),
                office=office,
                district=normalize_district(office, row["district"]),
                party=clean(row["party"]),
            )
            keys.append(key)
            year_office.add((key.year, key.office))
    return keys, year_office


def build_archive_candidate_map(year_office: set[tuple[int, str]]) -> dict[MissingKey, set[str]]:
    candidate_map: dict[MissingKey, set[str]] = defaultdict(set)

    for year, office in sorted(year_office):
        office_code = OFFICE_TO_ARCHIVE_CODE[office]
        page = 1
        seen_contests: set[tuple[str, str]] = set()

        while True:
            form = {
                "page": str(page),
                "sort": "",
                "etype": "elections",
                "from": str(year),
                "to": str(year),
                "office": office_code,
                "district": "",
                "gender": "",
                "party": "",
                "birth_county": "",
                "residence_county": "",
                "stage": "10",
                "incumbent": "",
                "marginoperator": "",
                "margin": "",
                "q": "",
            }

            soup = post_archive_search(form)
            trs = soup.select("table tbody > tr")
            if not trs:
                break

            contest_rows = 0
            found_new_contest = False
            current_district = "NA"

            for tr in trs:
                tds = tr.find_all("td", recursive=False)
                classes = tr.get("class") or []

                # Contest header row.
                if len(tds) == 5 and "d-flex" not in classes:
                    date_text = clean(tds[0].get_text(" ", strip=True))
                    office_text = clean(tds[1].get_text(" ", strip=True))
                    if not date_text.endswith(str(year)):
                        continue
                    contest_rows += 1
                    contest_key = (date_text, office_text)
                    if contest_key not in seen_contests:
                        seen_contests.add(contest_key)
                        found_new_contest = True
                    current_district = parse_district_from_office_cell(office_text, office)
                    continue

                # Candidate detail row.
                if len(tds) == 5 and "d-flex" in classes:
                    candidate = normalize_candidate(tds[0].get_text(" ", strip=True))
                    party_code = party_text_to_code(tds[2].get_text(" ", strip=True))
                    if not candidate or not party_code:
                        continue
                    key = MissingKey(
                        year=year,
                        office=office,
                        district=current_district,
                        party=party_code,
                    )
                    candidate_map[key].add(candidate)

            if contest_rows == 0:
                break
            if not found_new_contest and page > 1:
                break

            page += 1
            if page > 100:
                break

    return candidate_map


def write_overrides(
    output_csv: Path,
    missing_keys: list[MissingKey],
    candidate_map: dict[MissingKey, set[str]],
) -> tuple[int, int, int]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    unresolved = 0
    ambiguous = 0
    seen_rows: set[tuple[int, str, str, str]] = set()

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["year", "office", "district", "party", "candidate"])
        writer.writeheader()

        for key in sorted(missing_keys, key=lambda k: (k.year, k.office, k.district, k.party)):
            if (key.year, key.office, key.district, key.party) in seen_rows:
                continue
            seen_rows.add((key.year, key.office, key.district, key.party))

            candidates = candidate_map.get(key, set())
            if len(candidates) == 1:
                writer.writerow(
                    {
                        "year": key.year,
                        "office": key.office,
                        "district": key.district,
                        "party": key.party,
                        "candidate": next(iter(candidates)),
                    }
                )
                written += 1
            elif len(candidates) > 1:
                ambiguous += 1
            else:
                unresolved += 1

    return written, unresolved, ambiguous


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build candidate overrides from the Minnesota Historical Election Archive for missing candidate rows."
    )
    parser.add_argument(
        "--missing-report",
        type=Path,
        default=Path("Data/missing_candidates_legacy_precincts.csv"),
        help="Input missing-candidate report CSV from convert_mn_legacy_results_to_openelections_precinct.py",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("Data/candidate_overrides_from_archive.csv"),
        help="Output overrides CSV with year,office,district,party,candidate",
    )
    args = parser.parse_args()

    missing_keys, year_office = load_missing_keys(args.missing_report)
    if not missing_keys:
        raise ValueError(f"No eligible missing rows found in {args.missing_report}")

    candidate_map = build_archive_candidate_map(year_office)
    written, unresolved, ambiguous = write_overrides(args.output, missing_keys, candidate_map)

    print(f"Wrote {written} override rows to {args.output}")
    print(f"Unresolved keys: {unresolved}")
    print(f"Ambiguous keys: {ambiguous}")


if __name__ == "__main__":
    main()
