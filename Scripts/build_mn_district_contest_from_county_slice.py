#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from build_mn_district_contests_from_precinct_and_baf import (
    CONTEST_ORDER,
    SCOPE_ORDER,
    allocate_integer_votes,
    crosswalk_path_for_scope,
    district_sort_key,
    load_crosswalk,
    make_row_payload,
    normalize_county_token,
)


SCOPES = ("congressional", "state_house", "state_senate")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build modern district slices from an official county contest slice using "
            "county-to-district Census-block weights."
        )
    )
    parser.add_argument("county_slice", type=Path)
    parser.add_argument("--crosswalk-dir", type=Path, default=Path("Data/crosswalks"))
    parser.add_argument("--out-dir", type=Path, default=Path("Data/district_contests"))
    parser.add_argument("--merge-manifest", action="store_true")
    args = parser.parse_args()

    source = json.loads(args.county_slice.read_text(encoding="utf-8-sig"))
    contest_type = str(source.get("contest_type", "")).strip()
    year = int(source.get("year", 0))
    rows = source.get("rows", [])
    if not contest_type or year <= 0 or not isinstance(rows, list) or not rows:
        raise SystemExit("County slice is missing contest_type, year, or rows.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    generated_entries: list[dict[str, object]] = []

    for scope in SCOPES:
        crosswalk_path = crosswalk_path_for_scope(scope, year, args.crosswalk_dir)
        crosswalk = load_crosswalk(scope, crosswalk_path)
        totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {"dem": 0, "rep": 0, "other": 0}
        )
        dem_candidates: dict[str, Counter[str]] = defaultdict(Counter)
        rep_candidates: dict[str, Counter[str]] = defaultdict(Counter)
        input_votes = 0
        allocated_votes = 0
        missing_counties: list[str] = []

        for row in rows:
            county = str(row.get("county", "")).split(" - ", 1)[0].strip()
            allocations = crosswalk.county_district_weights.get(normalize_county_token(county), [])
            if not allocations:
                missing_counties.append(county)
                continue

            dem_candidate = str(row.get("dem_candidate", "")).strip()
            rep_candidate = str(row.get("rep_candidate", "")).strip()
            for bucket, field, candidate in (
                ("dem", "dem_votes", dem_candidate),
                ("rep", "rep_votes", rep_candidate),
                ("other", "other_votes", ""),
            ):
                votes = max(0, int(row.get(field, 0) or 0))
                input_votes += votes
                for district, district_votes in allocate_integer_votes(votes, allocations):
                    totals[district][bucket] += district_votes
                    allocated_votes += district_votes
                    if bucket == "dem" and candidate:
                        dem_candidates[district][candidate] += district_votes
                    elif bucket == "rep" and candidate:
                        rep_candidates[district][candidate] += district_votes

        if missing_counties:
            missing = ", ".join(sorted(set(missing_counties)))
            raise SystemExit(f"{scope}: counties missing block weights: {missing}")
        if allocated_votes != input_votes:
            raise SystemExit(
                f"{scope}: vote conservation failed ({allocated_votes} allocated vs {input_votes} input)."
            )

        results: dict[str, dict[str, object]] = {}
        for district in sorted(totals, key=district_sort_key):
            built = make_row_payload(
                district,
                totals[district],
                dem_candidates[district],
                rep_candidates[district],
            )
            if built:
                district_key, payload_row = built
                results[district_key] = payload_row

        dem_total = sum(int(row["dem_votes"]) for row in results.values())
        rep_total = sum(int(row["rep_votes"]) for row in results.values())
        other_total = sum(int(row["other_votes"]) for row in results.values())
        output_file = args.out_dir / f"{scope}_{contest_type}_{year}.json"
        output_payload = {
            "year": year,
            "scope": scope,
            "contest_type": contest_type,
            "meta": {
                "source": "county_block_disaggregation",
                "source_file": args.county_slice.name,
                "crosswalk_file": crosswalk_path.name,
                "allocation_basis": "county share of Census blocks in target districts",
                "estimated": True,
                "match_coverage_pct": 100.0,
                "county_fallback_pct": 100.0,
                "allocated_output_votes": allocated_votes,
                "vote_conservation_delta": allocated_votes - input_votes,
                "election_type": source.get("meta", {}).get("election_type", ""),
                "senate_seat_class": source.get("meta", {}).get("senate_seat_class"),
            },
            "general": {"results": results},
        }
        output_file.write_text(json.dumps(output_payload, indent=2) + "\n", encoding="utf-8")
        generated_entries.append(
            {
                "scope": scope,
                "contest_type": contest_type,
                "year": year,
                "file": output_file.name,
                "rows": len(results),
                "districts": len(results),
                "dem_total": dem_total,
                "rep_total": rep_total,
                "other_total": other_total,
                "major_party_contested": bool(dem_total and rep_total),
                "match_coverage_pct": 100.0,
                "crosswalk_match_pct": 0.0,
                "county_fallback_pct": 100.0,
                "estimated": True,
                "election_type": source.get("meta", {}).get("election_type", ""),
                "senate_seat_class": source.get("meta", {}).get("senate_seat_class"),
            }
        )

    manifest_path = args.out_dir / "manifest.json"
    manifest_entries: list[dict[str, object]] = []
    if args.merge_manifest and manifest_path.exists():
        manifest_entries = json.loads(manifest_path.read_text(encoding="utf-8-sig")).get("files", [])
    generated_keys = {
        (entry["scope"], entry["contest_type"], entry["year"])
        for entry in generated_entries
    }
    manifest_entries = [
        entry for entry in manifest_entries
        if (entry.get("scope"), entry.get("contest_type"), entry.get("year")) not in generated_keys
    ] + generated_entries
    manifest_entries.sort(
        key=lambda entry: (
            SCOPE_ORDER.get(str(entry.get("scope", "")), 999),
            CONTEST_ORDER.get(str(entry.get("contest_type", "")), 999),
            int(entry.get("year", 0)),
            str(entry.get("contest_type", "")),
        )
    )
    manifest_path.write_text(
        json.dumps({"files": manifest_entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(generated_entries)} {contest_type} district slices for {year}; "
        f"manifest indexes {len(manifest_entries)} slices."
    )


if __name__ == "__main__":
    main()
