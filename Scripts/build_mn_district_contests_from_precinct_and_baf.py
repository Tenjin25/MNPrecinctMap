#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


DEM_PARTIES = {"DFL", "DEM", "D"}
REP_PARTIES = {"R", "REP"}
SKIP_PARTIES = {"DIST", "TOTAL", "TOT", "EST"}
NON_GEO_TOKENS = (
    "ABSENTEE",
    "MAIL",
    "POSTAL",
    "UOCAVA",
    "OVERSEAS",
    "PROVISIONAL",
    "UNASSIGNED",
    "NO PRECINCT",
    "HOSPITAL",
    "CARE CENTER",
    "NURSING HOME",
    "FEDERAL BALLOTS",
    "NO DATA",
    "STATEWIDE",
    "TOTAL",
)

STATEWIDE_OFFICE_TO_CONTEST = {
    "PRESIDENT": "president",
    "U.S. SENATE": "us_senate",
    "UNITED STATES SENATOR": "us_senate",
    "GOVERNOR": "governor",
    "GOVERNOR & LT GOVERNOR": "governor",
    "ATTORNEY GENERAL": "attorney_general",
    "SECRETARY OF STATE": "secretary_of_state",
    "STATE AUDITOR": "auditor",
}

DISTRICT_OFFICE_TO_CONTEST = {
    "U.S. HOUSE": "us_house",
    "STATE HOUSE": "state_house",
    "STATE SENATE": "state_senate",
}

OFFICE_TO_CONTEST = {**STATEWIDE_OFFICE_TO_CONTEST, **DISTRICT_OFFICE_TO_CONTEST}

SCOPE_CONTESTS = {
    "congressional": {
        "president",
        "us_senate",
        "governor",
        "attorney_general",
        "secretary_of_state",
        "auditor",
        "us_house",
    },
    "state_house": {
        "president",
        "us_senate",
        "governor",
        "attorney_general",
        "secretary_of_state",
        "auditor",
        "state_house",
    },
    "state_senate": {
        "president",
        "us_senate",
        "governor",
        "attorney_general",
        "secretary_of_state",
        "auditor",
        "state_senate",
    },
}

NATIVE_CONTEST_BY_SCOPE = {
    "congressional": "us_house",
    "state_house": "state_house",
    "state_senate": "state_senate",
}

SCOPE_ORDER = {"congressional": 0, "state_house": 1, "state_senate": 2}
CONTEST_ORDER = {
    "president": 0,
    "us_senate": 1,
    "governor": 2,
    "attorney_general": 3,
    "secretary_of_state": 4,
    "auditor": 5,
    "us_house": 6,
    "state_house": 7,
    "state_senate": 8,
}


@dataclass
class CrosswalkNode:
    scope: str
    path: Path
    by_precinct: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    tuple_to_precinct_key: dict[tuple[str, str], str] = field(default_factory=dict)
    precinct_key_to_county: dict[str, str] = field(default_factory=dict)
    precinct_key_to_vtd: dict[str, str] = field(default_factory=dict)
    county_district_weights: dict[str, list[tuple[str, float]]] = field(default_factory=dict)


@dataclass
class DistrictAccumulator:
    totals: dict[str, dict[str, float]] = field(
        default_factory=lambda: defaultdict(lambda: {"dem": 0.0, "rep": 0.0, "other": 0.0})
    )
    candidates: dict[str, dict[str, Counter[str]]] = field(
        default_factory=lambda: defaultdict(lambda: {"dem": Counter(), "rep": Counter()})
    )
    total_input_votes: float = 0.0
    crosswalk_matched_votes: float = 0.0
    fallback_matched_votes: float = 0.0
    county_fallback_votes: float = 0.0
    unmatched_votes: float = 0.0


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
    token = re.sub(r"\bSAINT\b", "ST", token)
    token = re.sub(r"\bSTE\.?\b", "ST", token)
    token = token.replace("TOWNSHIP", "TWP")
    token = token.replace("TWP.", "TWP")
    token = token.replace("UNORGANIZED", "UNORG")
    token = re.sub(r"\bHEIGHTS\b", "HTS", token)
    token = re.sub(r"\bCITY\b", "", token)
    token = re.sub(r"\bWARD\b", "W", token)
    token = re.sub(r"\bPRECINCT\b", "P", token)
    token = re.sub(r"\bPCT\b", "P", token)
    token = re.sub(r"\bP\s*[-.]?\s*0*([0-9]+)\b", lambda m: f"P{int(m.group(1))}", token)
    token = re.sub(r"\bW\s*[-.]?\s*0*([0-9]+)\b", lambda m: f"W{int(m.group(1))}", token)
    token = re.sub(r"[^A-Z0-9]+", "", token)
    return token


def make_alias_key(county: str, precinct: str) -> str:
    county_token = normalize_county_token(county)
    precinct_token = normalize_precinct_token(precinct)
    if county_token == "" or precinct_token == "":
        return ""
    return f"{county_token}-{precinct_token}"


def normalize_precinct_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", clean(value)).strip().upper()


def normalize_numeric_district(value: str | None) -> str:
    token = clean(value).upper()
    if token in {"", "NA"}:
        return ""
    token = token.replace("DISTRICT", "").replace("DIST", "").strip()
    m = re.match(r"^0*([0-9]+)$", token)
    if m:
        return str(int(m.group(1)))
    m_float = re.match(r"^0*([0-9]+)\.0+$", token)
    if m_float:
        return str(int(m_float.group(1)))
    m_any = re.search(r"([0-9]+)", token)
    if m_any:
        return str(int(m_any.group(1)))
    return ""


def normalize_house_district(value: str | None) -> str:
    token = clean(value).upper()
    if token in {"", "NA"}:
        return ""
    token = token.replace("DISTRICT", "").replace("DIST", "").strip()
    token = re.sub(r"[^A-Z0-9]+", "", token)
    m = re.match(r"^0*([0-9]+)([A-Z]?)$", token)
    if m:
        return f"{int(m.group(1))}{m.group(2)}"
    m_any = re.search(r"0*([0-9]+)([A-Z])", token)
    if m_any:
        return f"{int(m_any.group(1))}{m_any.group(2)}"
    return ""


def normalize_scope_district(scope: str, value: str | None) -> str:
    if scope == "state_house":
        return normalize_house_district(value)
    return normalize_numeric_district(value)


def normalize_fallback_district(contest_type: str, value: str | None) -> str:
    if contest_type == "state_house":
        return normalize_house_district(value)
    if contest_type in {"us_house", "state_senate"}:
        return normalize_numeric_district(value)
    return ""


def classify_party(party: str) -> str:
    p = clean(party).upper()
    if p in DEM_PARTIES:
        return "dem"
    if p in REP_PARTIES:
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


def district_sort_key(value: str) -> tuple[int, str]:
    token = clean(value).upper()
    m = re.match(r"^([0-9]+)([A-Z]?)$", token)
    if m:
        return int(m.group(1)), m.group(2)
    return 9999, token


def top_candidate(counter: Counter[str]) -> str:
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def is_non_geographic_precinct(value: str) -> bool:
    token = clean(value).upper()
    if token == "":
        return True
    return any(flag in token for flag in NON_GEO_TOKENS)


def is_non_geographic_county(value: str) -> bool:
    token = clean(value).upper()
    if token == "":
        return True
    return any(flag in token for flag in NON_GEO_TOKENS)


def crosswalk_path_for_scope(scope: str, year: int, crosswalk_dir: Path) -> Path:
    if scope == "congressional":
        return crosswalk_dir / "precinct_to_cd118.csv"
    plan_year = 2024 if year > 2022 else 2022
    if scope == "state_house":
        return crosswalk_dir / f"precinct_to_{plan_year}_state_house.csv"
    if scope == "state_senate":
        return crosswalk_dir / f"precinct_to_{plan_year}_state_senate.csv"
    raise ValueError(f"Unsupported scope: {scope}")


def load_crosswalk(scope: str, path: Path) -> CrosswalkNode:
    if not path.exists():
        raise FileNotFoundError(f"Crosswalk not found: {path}")
    by_precinct: dict[str, list[tuple[str, float]]] = defaultdict(list)
    tuple_to_precinct_key: dict[tuple[str, str], str] = {}
    precinct_key_to_county: dict[str, str] = {}
    precinct_key_to_vtd: dict[str, str] = {}
    county_district_blocks: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    county_block_totals: dict[str, float] = defaultdict(float)

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            precinct_key_raw = clean(row.get("precinct_key"))
            precinct_key = normalize_precinct_key(precinct_key_raw)
            district = normalize_scope_district(
                scope,
                row.get("district_num") or row.get("district_code") or row.get("district"),
            )
            weight = float(clean(row.get("area_weight")) or 0)
            if precinct_key == "" or district == "" or weight <= 0:
                continue

            by_precinct[precinct_key].append((district, weight))

            countyfp = clean(row.get("countyfp"))
            vtdst20 = clean(row.get("vtdst20"))
            if countyfp and vtdst20:
                tuple_to_precinct_key[(countyfp, vtdst20)] = precinct_key

            county_name = precinct_key_raw.split(" - ", 1)[0].strip() if " - " in precinct_key_raw else ""
            if county_name:
                precinct_key_to_county[precinct_key] = county_name
                county_norm = normalize_county_token(county_name)
                block_count = float(clean(row.get("block_count")) or 0)
                if block_count <= 0:
                    total_blocks = float(clean(row.get("total_blocks")) or 0)
                    if total_blocks > 0:
                        block_count = float(weight) * total_blocks
                if county_norm and block_count > 0:
                    county_district_blocks[county_norm][district] += block_count
                    county_block_totals[county_norm] += block_count
            if vtdst20:
                precinct_key_to_vtd[precinct_key] = vtdst20

    county_district_weights: dict[str, list[tuple[str, float]]] = {}
    for county_norm, district_blocks in county_district_blocks.items():
        total = county_block_totals.get(county_norm, 0.0)
        if total <= 0:
            continue
        weights = [(d, b / total) for d, b in district_blocks.items() if b > 0]
        weights.sort(key=lambda x: district_sort_key(x[0]))
        if weights:
            county_district_weights[county_norm] = weights

    return CrosswalkNode(
        scope=scope,
        path=path,
        by_precinct=dict(by_precinct),
        tuple_to_precinct_key=tuple_to_precinct_key,
        precinct_key_to_county=precinct_key_to_county,
        precinct_key_to_vtd=precinct_key_to_vtd,
        county_district_weights=county_district_weights,
    )


def build_alias_map(
    canonical_precinct_keys: set[str],
    key_to_county: dict[str, str],
    key_to_vtd: dict[str, str],
    tuple_to_precinct_key: dict[tuple[str, str], str],
    precincts_geojson: Path,
) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    collided_aliases: set[str] = set()

    def add_alias(alias: str, precinct_key: str) -> None:
        if alias == "" or precinct_key == "":
            return
        if alias in collided_aliases:
            return
        existing = alias_map.get(alias)
        if existing is None:
            alias_map[alias] = precinct_key
            return
        if existing != precinct_key:
            collided_aliases.add(alias)
            alias_map.pop(alias, None)

    for precinct_key in sorted(canonical_precinct_keys):
        county_name = key_to_county.get(precinct_key, "")
        vtd = key_to_vtd.get(precinct_key, "")
        if county_name == "" or vtd == "":
            continue

        add_alias(make_alias_key(county_name, vtd), precinct_key)
        if vtd.isdigit():
            n = str(int(vtd))
            add_alias(make_alias_key(county_name, n), precinct_key)
            add_alias(make_alias_key(county_name, n.zfill(6)), precinct_key)

    if not precincts_geojson.exists():
        return alias_map

    obj = json.loads(precincts_geojson.read_text(encoding="utf-8"))
    for feature in obj.get("features", []):
        props = feature.get("properties", {}) or {}
        countyfp = clean(props.get("COUNTYFP20"))
        vtdst20 = clean(props.get("VTDST20"))
        if countyfp == "" or vtdst20 == "":
            continue
        precinct_key = tuple_to_precinct_key.get((countyfp, vtdst20), "")
        if precinct_key == "":
            continue

        county_name = (
            clean(props.get("county_nam"))
            or clean(props.get("COUNTYNAME"))
            or key_to_county.get(precinct_key, "")
        )
        if county_name == "":
            continue

        for field in ("NAME20", "NAMELSAD20", "precinct_name", "PRECINCT_NAME"):
            value = clean(props.get(field))
            if value == "":
                continue
            if " - " in value:
                c, _, p = value.partition(" - ")
                add_alias(make_alias_key(c, p), precinct_key)
            add_alias(make_alias_key(county_name, value), precinct_key)

    return alias_map


def precinct_row_variants(raw_precinct: str) -> list[str]:
    value = clean(raw_precinct)
    if value == "":
        return []
    out = [value]
    if " - " in value:
        out.append(value.split(" - ", 1)[1].strip())
    token = value.strip()

    float_like = re.match(r"^([0-9]+)\.0+$", token)
    if float_like:
        token = float_like.group(1)
        out.append(token)

    if token.isdigit():
        n = str(int(token))
        out.append(n)
        out.append(n.zfill(6))
    return list(dict.fromkeys(out))


def resolve_precinct_key(county: str, precinct: str, alias_map: dict[str, str]) -> str:
    for variant in precinct_row_variants(precinct):
        alias = make_alias_key(county, variant)
        if alias == "":
            continue
        hit = alias_map.get(alias, "")
        if hit:
            return hit
    return ""


def build_native_precinct_fallback_weights(
    precinct_csv: Path,
    *,
    include_non_geographic: bool,
) -> dict[str, dict[str, list[tuple[str, float]]]]:
    votes_by_scope_precinct_district: dict[str, dict[str, dict[str, float]]] = {
        scope: defaultdict(lambda: defaultdict(float)) for scope in NATIVE_CONTEST_BY_SCOPE
    }

    with precinct_csv.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            county = clean(row.get("county"))
            precinct = clean(row.get("precinct"))
            office = normalize_office(row.get("office"))
            contest_type = OFFICE_TO_CONTEST.get(office)
            if not county or not precinct or not contest_type:
                continue

            if is_non_geographic_county(county):
                continue

            party = clean(row.get("party")).upper()
            if party in SKIP_PARTIES:
                continue

            votes = parse_int(row.get("votes"))
            if votes <= 0:
                continue

            if (not include_non_geographic) and is_non_geographic_precinct(precinct):
                continue

            precinct_alias = make_alias_key(county, precinct)
            if precinct_alias == "":
                continue

            district_raw = clean(row.get("district"))
            for scope, native_contest in NATIVE_CONTEST_BY_SCOPE.items():
                if contest_type != native_contest:
                    continue
                district = normalize_fallback_district(contest_type, district_raw)
                if district == "":
                    continue
                votes_by_scope_precinct_district[scope][precinct_alias][district] += votes

    out: dict[str, dict[str, list[tuple[str, float]]]] = {}
    for scope, precinct_bucket in votes_by_scope_precinct_district.items():
        scope_allocs: dict[str, list[tuple[str, float]]] = {}
        for precinct_alias, district_votes in precinct_bucket.items():
            total = sum(district_votes.values())
            if total <= 0:
                continue
            allocs = [(district, votes / total) for district, votes in district_votes.items() if votes > 0]
            allocs.sort(key=lambda x: district_sort_key(x[0]))
            if allocs:
                scope_allocs[precinct_alias] = allocs
        out[scope] = scope_allocs
    return out


def load_year_files(data_dir: Path, year_min: int, year_max: int) -> list[Path]:
    out: list[Path] = []
    for path in sorted(data_dir.glob("*__mn__general__precinct.csv")):
        if len(path.name) < 4 or not path.name[:4].isdigit():
            continue
        year = int(path.name[:4])
        if year < year_min or year > year_max:
            continue
        out.append(path)
    return out


def make_row_payload(
    district: str,
    node: dict[str, float],
    dem_counter: Counter[str],
    rep_counter: Counter[str],
) -> tuple[str, dict[str, object]] | None:
    dem_votes = int(round(node.get("dem", 0.0)))
    rep_votes = int(round(node.get("rep", 0.0)))
    other_votes = int(round(node.get("other", 0.0)))
    total_votes = dem_votes + rep_votes + other_votes
    if total_votes <= 0:
        return None

    margin = rep_votes - dem_votes
    margin_pct = (margin / total_votes) * 100.0
    winner_code = "R" if margin > 0 else ("D" if margin < 0 else "T")
    winner = "REPUBLICAN" if winner_code == "R" else ("DEMOCRAT" if winner_code == "D" else "TIE")
    color = compute_color(abs(margin_pct), winner_code if winner_code in {"R", "D"} else "R")

    payload = {
        "district": district,
        "dem_votes": dem_votes,
        "rep_votes": rep_votes,
        "other_votes": other_votes,
        "total_votes": total_votes,
        "dem_candidate": top_candidate(dem_counter),
        "rep_candidate": top_candidate(rep_counter),
        "margin": margin,
        "margin_pct": round(margin_pct, 6),
        "winner": winner,
        "color": color,
    }
    return district, payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build MN district contest slices from precinct CSVs using BAF crosswalks "
            "with district-race fallback for unmatched precinct keys."
        )
    )
    parser.add_argument("--data-dir", type=Path, default=Path("Data"))
    parser.add_argument("--crosswalk-dir", type=Path, default=Path("Data/crosswalks"))
    parser.add_argument("--precincts-geojson", type=Path, default=Path("Data/precincts.geojson"))
    parser.add_argument("--out-dir", type=Path, default=Path("Data/district_contests"))
    parser.add_argument("--year-min", type=int, default=2012)
    parser.add_argument("--year-max", type=int, default=2024)
    parser.add_argument(
        "--include-non-geographic",
        action="store_true",
        help="Include absentee/mail/non-geographic precinct labels (default: false).",
    )
    args = parser.parse_args()

    year_files = load_year_files(args.data_dir, args.year_min, args.year_max)
    if not year_files:
        raise SystemExit("No precinct CSV files matched the requested year range.")

    cw_cache: dict[tuple[str, int], CrosswalkNode] = {}

    def get_crosswalk(scope: str, year: int) -> CrosswalkNode:
        if scope == "congressional":
            cache_key = (scope, 0)
        else:
            cache_key = (scope, 2024 if year > 2022 else 2022)
        if cache_key in cw_cache:
            return cw_cache[cache_key]
        path = crosswalk_path_for_scope(scope, year, args.crosswalk_dir)
        node = load_crosswalk(scope, path)
        cw_cache[cache_key] = node
        return node

    # Prime one crosswalk set for alias generation.
    base_cw = get_crosswalk("congressional", args.year_max)
    canonical_precinct_keys = set(base_cw.by_precinct.keys())
    alias_map = build_alias_map(
        canonical_precinct_keys=canonical_precinct_keys,
        key_to_county=base_cw.precinct_key_to_county,
        key_to_vtd=base_cw.precinct_key_to_vtd,
        tuple_to_precinct_key=base_cw.tuple_to_precinct_key,
        precincts_geojson=args.precincts_geojson,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries: list[dict[str, object]] = []

    for precinct_csv in year_files:
        year = int(precinct_csv.name[:4])
        accum: dict[tuple[str, str], DistrictAccumulator] = {}
        native_precinct_fallbacks = build_native_precinct_fallback_weights(
            precinct_csv,
            include_non_geographic=args.include_non_geographic,
        )

        def get_acc(scope: str, contest_type: str) -> DistrictAccumulator:
            key = (scope, contest_type)
            if key not in accum:
                accum[key] = DistrictAccumulator()
            return accum[key]

        with precinct_csv.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                county = clean(row.get("county"))
                precinct = clean(row.get("precinct"))
                office = normalize_office(row.get("office"))
                contest_type = OFFICE_TO_CONTEST.get(office)
                if not county or not precinct or not contest_type:
                    continue

                if is_non_geographic_county(county):
                    continue

                party = clean(row.get("party")).upper()
                if party in SKIP_PARTIES:
                    continue

                votes = parse_int(row.get("votes"))
                if votes <= 0:
                    continue

                if (not args.include_non_geographic) and is_non_geographic_precinct(precinct):
                    continue

                bucket = classify_party(party)
                candidate = clean(row.get("candidate"))
                district_raw = clean(row.get("district"))
                precinct_alias = make_alias_key(county, precinct)
                precinct_key = resolve_precinct_key(county, precinct, alias_map)

                for scope, allowed in SCOPE_CONTESTS.items():
                    if contest_type not in allowed:
                        continue

                    cw = get_crosswalk(scope, year)
                    node = get_acc(scope, contest_type)
                    node.total_input_votes += votes

                    allocations: list[tuple[str, float]] = []
                    used_crosswalk = False
                    used_fallback = False
                    used_county_fallback = False

                    is_scope_district_contest = (
                        (scope == "congressional" and contest_type == "us_house")
                        or (scope == "state_house" and contest_type == "state_house")
                        or (scope == "state_senate" and contest_type == "state_senate")
                    )

                    if not allocations and precinct_key:
                        allocations = cw.by_precinct.get(precinct_key, [])
                        if allocations:
                            used_crosswalk = True

                    if not allocations and is_scope_district_contest:
                        fallback_district = normalize_fallback_district(contest_type, district_raw)
                        if fallback_district:
                            allocations = [(fallback_district, 1.0)]
                            used_fallback = True

                    if not allocations and not is_scope_district_contest:
                        if precinct_alias:
                            native_alloc = native_precinct_fallbacks.get(scope, {}).get(precinct_alias, [])
                            if native_alloc:
                                allocations = native_alloc
                                used_fallback = True

                    if not allocations and not is_scope_district_contest:
                        county_alloc = cw.county_district_weights.get(normalize_county_token(county), [])
                        if county_alloc:
                            allocations = county_alloc
                            used_county_fallback = True

                    if not allocations:
                        node.unmatched_votes += votes
                        continue

                    if used_crosswalk:
                        node.crosswalk_matched_votes += votes
                    elif used_fallback:
                        node.fallback_matched_votes += votes
                    elif used_county_fallback:
                        node.county_fallback_votes += votes

                    for district_num, weight in allocations:
                        allocated_votes = votes * float(weight)
                        district_totals = node.totals[district_num]
                        district_totals[bucket] += allocated_votes

                        if bucket in {"dem", "rep"}:
                            cand_u = candidate.upper()
                            if candidate and cand_u != "WRITE-IN":
                                node.candidates[district_num][bucket][candidate] += allocated_votes

        for (scope, contest_type), node in sorted(
            accum.items(),
            key=lambda x: (SCOPE_ORDER.get(x[0][0], 999), CONTEST_ORDER.get(x[0][1], 999), x[0][1]),
        ):
            results: dict[str, dict[str, object]] = {}
            dem_total = 0
            rep_total = 0
            other_total = 0

            for district in sorted(node.totals.keys(), key=district_sort_key):
                row_payload = make_row_payload(
                    district=district,
                    node=node.totals[district],
                    dem_counter=node.candidates[district]["dem"],
                    rep_counter=node.candidates[district]["rep"],
                )
                if row_payload is None:
                    continue
                district_id, payload = row_payload
                results[district_id] = payload
                dem_total += int(payload["dem_votes"])
                rep_total += int(payload["rep_votes"])
                other_total += int(payload["other_votes"])

            if not results:
                continue

            matched_votes = node.crosswalk_matched_votes + node.fallback_matched_votes + node.county_fallback_votes
            coverage_pct = (matched_votes / node.total_input_votes) * 100.0 if node.total_input_votes > 0 else 0.0
            crosswalk_pct = (
                (node.crosswalk_matched_votes / node.total_input_votes) * 100.0 if node.total_input_votes > 0 else 0.0
            )

            key = f"{scope}_{contest_type}_{year}"
            out_file = args.out_dir / f"{key}.json"
            payload = {
                "scope": scope,
                "contest_type": contest_type,
                "year": year,
                "state": "MN",
                "general": {"results": results},
                "meta": {
                    "source_file": precinct_csv.name,
                    "crosswalk_file": get_crosswalk(scope, year).path.name,
                    "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "total_input_votes": int(round(node.total_input_votes)),
                    "crosswalk_matched_votes": int(round(node.crosswalk_matched_votes)),
                    "fallback_matched_votes": int(round(node.fallback_matched_votes)),
                    "county_fallback_votes": int(round(node.county_fallback_votes)),
                    "unmatched_votes": int(round(node.unmatched_votes)),
                    "match_coverage_pct": round(coverage_pct, 4),
                    "crosswalk_match_pct": round(crosswalk_pct, 4),
                },
            }
            out_file.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

            manifest_entries.append(
                {
                    "scope": scope,
                    "contest_type": contest_type,
                    "year": year,
                    "file": out_file.name,
                    "rows": len(results),
                    "districts": len(results),
                    "dem_total": dem_total,
                    "rep_total": rep_total,
                    "other_total": other_total,
                    "major_party_contested": bool(dem_total > 0 and rep_total > 0),
                    "match_coverage_pct": round(coverage_pct, 4),
                    "crosswalk_match_pct": round(crosswalk_pct, 4),
                    "county_fallback_pct": round(
                        (node.county_fallback_votes / node.total_input_votes) * 100.0 if node.total_input_votes > 0 else 0.0,
                        4,
                    ),
                }
            )

    manifest_entries.sort(
        key=lambda x: (
            SCOPE_ORDER.get(str(x.get("scope")), 999),
            CONTEST_ORDER.get(str(x.get("contest_type")), 999),
            int(x.get("year", 0)),
            str(x.get("contest_type", "")),
        )
    )

    manifest_path = args.out_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"files": manifest_entries}, separators=(",", ":")), encoding="utf-8")

    print(
        f"Wrote {len(manifest_entries)} district contest slices "
        f"({args.year_min}-{args.year_max}) to {args.out_dir}"
    )


if __name__ == "__main__":
    main()
