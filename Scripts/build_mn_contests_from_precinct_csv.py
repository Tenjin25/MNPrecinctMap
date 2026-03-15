#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ContestSpec:
    offices: tuple[str, ...]
    dem_parties: tuple[str, ...] = ("DFL", "DEM", "D")
    rep_parties: tuple[str, ...] = ("R", "REP")


CONTEST_SPECS: dict[str, ContestSpec] = {
    "president": ContestSpec(offices=("PRESIDENT",)),
    "us_senate": ContestSpec(offices=("U.S. SENATE", "UNITED STATES SENATOR")),
    "governor": ContestSpec(offices=("GOVERNOR", "GOVERNOR & LT GOVERNOR")),
    "attorney_general": ContestSpec(offices=("ATTORNEY GENERAL",)),
    "secretary_of_state": ContestSpec(offices=("SECRETARY OF STATE",)),
    "auditor": ContestSpec(offices=("STATE AUDITOR",)),
}

NON_GEO_TOKENS = (
    "ABSENTEE",
    "MAIL",
    "POSTAL",
    "UOCAVA",
    "OVERSEAS",
    "PROVISIONAL",
    "UNASSIGNED",
    "NO PRECINCT",
    "NO DATA",
    "STATEWIDE",
    "TOTAL",
)


def clean(value: str | None) -> str:
    return (value or "").strip()


def parse_int(value: str | None) -> int:
    token = clean(value).replace(",", "")
    if token == "":
        return 0
    try:
        return int(float(token))
    except ValueError:
        return 0


def normalize_office(value: str | None) -> str:
    return clean(value).upper()


def classify_party(party: str, spec: ContestSpec) -> str:
    p = clean(party).upper()
    if p in spec.dem_parties:
        return "dem"
    if p in spec.rep_parties:
        return "rep"
    return "other"


def compute_color(margin_pct_abs: float, winner: str) -> str:
    if margin_pct_abs >= 40:
        return "#67000d" if winner == "R" else "#08306b"
    if margin_pct_abs >= 30:
        return "#a50f15" if winner == "R" else "#08519c"
    if margin_pct_abs >= 20:
        return "#cb181d" if winner == "R" else "#3182bd"
    if margin_pct_abs >= 10:
        return "#ef3b2c" if winner == "R" else "#6baed6"
    if margin_pct_abs >= 5.5:
        return "#fb6a4a" if winner == "R" else "#9ecae1"
    if margin_pct_abs >= 1.0:
        return "#fcae91" if winner == "R" else "#c6dbef"
    if margin_pct_abs >= 0.5:
        return "#fee8c8" if winner == "R" else "#e1f5fe"
    return "#f7f7f7"


def top_candidate(counter: Counter[str]) -> str:
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def precinct_key(county: str, precinct: str) -> str:
    c = clean(county)
    p = clean(precinct)
    if c == "" or p == "":
        return ""
    return f"{c} - {p}"


def is_non_geographic_label(value: str) -> bool:
    token = clean(value).upper()
    if token == "":
        return True
    return any(flag in token for flag in NON_GEO_TOKENS)


def normalize_county_token(value: str | None) -> str:
    token = clean(value).upper().replace(" COUNTY", "")
    token = token.replace("&", " AND ")
    token = re.sub(r"[^A-Z0-9]+", "", token)
    return token


def normalize_precinct_token(value: str | None) -> str:
    token = clean(value).upper()
    if token == "":
        return ""
    token = token.replace("&", " AND ")
    token = token.replace("TOWNSHIP", "TWP").replace("TWP.", "TWP")
    token = token.replace("UNORGANIZED", "UNORG")
    token = re.sub(r"\bWARD\b", "W", token)
    token = re.sub(r"\bPRECINCT\b", "P", token)
    token = re.sub(r"\bPCT\b", "P", token)
    token = re.sub(r"\bP\s*[-.]?\s*0*([0-9]+)\b", lambda m: f"P{int(m.group(1))}", token)
    token = re.sub(r"\bW\s*[-.]?\s*0*([0-9]+)\b", lambda m: f"W{int(m.group(1))}", token)
    token = re.sub(r"[^A-Z0-9]+", "", token)
    return token


def make_alias_key(county: str, precinct: str) -> str:
    c = normalize_county_token(county)
    p = normalize_precinct_token(precinct)
    if c == "" or p == "":
        return ""
    return f"{c}-{p}"


def build_precinct_alias_map(precincts_geojson: Path) -> dict[str, str]:
    if not precincts_geojson.exists():
        return {}

    obj = json.loads(precincts_geojson.read_text(encoding="utf-8"))
    alias_to_key: dict[str, str] = {}
    collided: set[str] = set()

    def add_alias(alias: str, canonical_key: str) -> None:
        if alias == "" or canonical_key == "":
            return
        if alias in collided:
            return
        hit = alias_to_key.get(alias)
        if hit is None:
            alias_to_key[alias] = canonical_key
            return
        if hit != canonical_key:
            collided.add(alias)
            alias_to_key.pop(alias, None)

    for feature in obj.get("features", []):
        props = feature.get("properties", {}) or {}
        county = clean(props.get("county_nam") or props.get("COUNTYNAME"))
        vtd = clean(props.get("VTDST20") or props.get("prec_id") or props.get("PCTKEY20"))
        if county == "" or vtd == "":
            continue

        canonical_key = precinct_key(county, vtd)
        add_alias(make_alias_key(county, vtd), canonical_key)
        if vtd.isdigit():
            n = str(int(vtd))
            add_alias(make_alias_key(county, n), canonical_key)
            add_alias(make_alias_key(county, n.zfill(6)), canonical_key)

        for field in ("NAME20", "NAMELSAD20", "precinct_name", "PRECINCT_NAME"):
            value = clean(props.get(field))
            if value == "":
                continue
            add_alias(make_alias_key(county, value), canonical_key)
            if " - " in value:
                c, _, p = value.partition(" - ")
                add_alias(make_alias_key(c, p), canonical_key)

    return alias_to_key


def resolve_precinct_key(county: str, precinct: str, alias_map: dict[str, str]) -> tuple[str, bool]:
    raw = precinct_key(county, precinct)
    if raw == "":
        return "", False

    variants = [precinct]
    token = clean(precinct)
    float_like = re.match(r"^([0-9]+)\.0+$", token)
    if float_like:
        token = float_like.group(1)
        variants.append(token)
    if token.isdigit():
        n = str(int(token))
        variants.append(n)
        variants.append(n.zfill(6))

    for v in variants:
        alias = make_alias_key(county, v)
        if alias == "":
            continue
        hit = alias_map.get(alias, "")
        if hit:
            return hit, True
    return raw, False


def build_yearly_slices(
    precinct_csv_path: Path,
    out_dir: Path,
    alias_map: dict[str, str],
) -> list[dict[str, object]]:
    year = int(precinct_csv_path.name[:4])

    office_to_contest: dict[str, list[str]] = defaultdict(list)
    for contest_type, spec in CONTEST_SPECS.items():
        for office in spec.offices:
            office_to_contest[office].append(contest_type)

    contest_precinct: dict[str, dict[str, dict[str, int]]] = {
        contest_type: defaultdict(lambda: {"dem": 0, "rep": 0, "other": 0})
        for contest_type in CONTEST_SPECS
    }
    candidate_votes: dict[str, dict[str, dict[str, Counter[str]]]] = {
        contest_type: defaultdict(lambda: {"dem": Counter(), "rep": Counter()})
        for contest_type in CONTEST_SPECS
    }

    source_votes = 0
    matched_votes = 0

    with precinct_csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            county = clean(row.get("county"))
            precinct = clean(row.get("precinct"))
            if is_non_geographic_label(county) or is_non_geographic_label(precinct):
                continue
            key, matched = resolve_precinct_key(county, precinct, alias_map)
            if key == "":
                continue

            office = normalize_office(row.get("office"))
            matching_contests = office_to_contest.get(office, [])
            if not matching_contests:
                continue

            votes = parse_int(row.get("votes"))
            if votes < 0:
                continue

            source_votes += votes
            if matched:
                matched_votes += votes

            party = clean(row.get("party")).upper()
            candidate = clean(row.get("candidate"))
            candidate_u = candidate.upper()

            for contest_type in matching_contests:
                spec = CONTEST_SPECS[contest_type]
                bucket = classify_party(party, spec)
                node = contest_precinct[contest_type][key]
                node[bucket] += votes

                if bucket in {"dem", "rep"} and candidate and candidate_u != "WRITE-IN":
                    candidate_votes[contest_type][key][bucket][candidate] += votes

    manifest_entries: list[dict[str, object]] = []
    for contest_type, precinct_nodes in contest_precinct.items():
        rows: list[dict[str, object]] = []
        dem_total = 0
        rep_total = 0
        other_total = 0

        for key in sorted(precinct_nodes.keys()):
            node = precinct_nodes[key]
            dem_votes = int(node["dem"])
            rep_votes = int(node["rep"])
            other_votes = int(node["other"])
            total_votes = dem_votes + rep_votes + other_votes
            if total_votes <= 0:
                continue

            precinct_candidates = candidate_votes[contest_type][key]
            dem_candidate = top_candidate(precinct_candidates["dem"])
            rep_candidate = top_candidate(precinct_candidates["rep"])

            signed_margin = rep_votes - dem_votes
            margin_pct = (signed_margin / total_votes) * 100.0
            winner_code = "R" if signed_margin > 0 else ("D" if signed_margin < 0 else "T")
            winner = "REPUBLICAN" if winner_code == "R" else ("DEMOCRAT" if winner_code == "D" else "TIE")

            dem_total += dem_votes
            rep_total += rep_votes
            other_total += other_votes

            rows.append(
                {
                    "county": key,
                    "dem_votes": dem_votes,
                    "rep_votes": rep_votes,
                    "other_votes": other_votes,
                    "total_votes": total_votes,
                    "dem_candidate": dem_candidate,
                    "rep_candidate": rep_candidate,
                    "margin": signed_margin,
                    "margin_pct": round(margin_pct, 6),
                    "winner": winner,
                    "color": compute_color(abs(margin_pct), winner_code),
                }
            )

        if not rows:
            continue

        out_file = out_dir / f"{contest_type}_{year}.json"
        payload = {
            "contest_type": contest_type,
            "year": year,
            "state": "MN",
            "rows": rows,
            "meta": {
                "source_file": precinct_csv_path.name,
                "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "match_coverage_pct": round((matched_votes / source_votes) * 100.0, 4) if source_votes > 0 else 0.0,
            },
        }
        out_file.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

        manifest_entries.append(
            {
                "contest_type": contest_type,
                "year": year,
                "file": out_file.name,
                "rows": len(rows),
                "dem_total": dem_total,
                "rep_total": rep_total,
                "other_total": other_total,
                "major_party_contested": bool(dem_total > 0 and rep_total > 0),
                "match_coverage_pct": round((matched_votes / source_votes) * 100.0, 4) if source_votes > 0 else 0.0,
            }
        )

    return manifest_entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build MN precinct-level contest slices (manifest + JSON) for county/precinct view."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("Data"),
        help="Directory containing *__mn__general__precinct.csv files.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("Data/contests"),
        help="Output directory for contest slices and manifest.json.",
    )
    parser.add_argument(
        "--year-min",
        type=int,
        default=2000,
        help="Minimum year to include.",
    )
    parser.add_argument(
        "--year-max",
        type=int,
        default=2024,
        help="Maximum year to include.",
    )
    parser.add_argument(
        "--precincts-geojson",
        type=Path,
        default=Path("Data/precincts.geojson"),
        help="Precinct GeoJSON used to resolve county/precinct names to canonical precinct keys.",
    )
    args = parser.parse_args()

    precinct_files = []
    for path in sorted(args.data_dir.glob("*__mn__general__precinct.csv")):
        name = path.name
        if len(name) < 4 or not name[:4].isdigit():
            continue
        year = int(name[:4])
        if year < args.year_min or year > args.year_max:
            continue
        precinct_files.append(path)

    if not precinct_files:
        raise SystemExit("No precinct CSV files matched the requested year range.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    alias_map = build_precinct_alias_map(args.precincts_geojson)
    manifest_entries: list[dict[str, object]] = []
    for csv_path in precinct_files:
        manifest_entries.extend(build_yearly_slices(csv_path, args.out_dir, alias_map))

    contest_order = {
        "president": 0,
        "us_senate": 1,
        "governor": 2,
        "attorney_general": 3,
        "secretary_of_state": 4,
        "auditor": 5,
    }
    manifest_entries.sort(
        key=lambda x: (
            contest_order.get(str(x.get("contest_type")), 999),
            int(x.get("year", 0)),
            str(x.get("contest_type", "")),
        )
    )

    manifest_payload = {"files": manifest_entries}
    manifest_path = args.out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload, separators=(",", ":")), encoding="utf-8")

    print(
        f"Wrote {len(manifest_entries)} precinct contest slices to {args.out_dir} "
        f"and manifest to {manifest_path}"
    )


if __name__ == "__main__":
    main()
