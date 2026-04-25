# North Star Vote Atlas

Minnesota election atlas for district-level analysis, classroom use, and public-facing election explainers.

This repository contains:
- A static web app (`index.html`) designed for GitHub Pages.
- Source election files and district geometry under `Data/`.
- Python scripts in `Scripts/` that generate map-ready contest slices and manifests.

## Who This Is For

### Students

Use this as a reproducible election data lab:
- Learn how raw precinct returns become district-level summaries.
- Compare descriptive mapping (`winners`) vs analytical mapping (`shift`, `flips`).
- Validate claims by tracing from rendered map color back to source rows.

### General Public

Use this to answer local questions quickly:
- Who won where?
- Was that win narrow or decisive?
- Did my district shift compared with previous cycles?

### Political Junkies and Data Journalists

Use this for fast comparative analysis:
- Identify coalition movement by geography.
- Separate true flips from simple margin compression.
- Compare county patterns against state house/state senate boundaries.
- Build defensible election narratives with transparent data lineage.

## What The App Covers

### Geography Layers

- Counties
- Congressional districts
- State house districts
- State senate districts

### Visualization Modes

- `margins`: color intensity by absolute margin.
- `winners`: winner-only party coloration.
- `shift`: movement versus prior available cycle.
- `flips`: highlights places where party winner changed.

### Interaction

- Contest/year dropdown loads from generated manifests.
- Click-to-zoom for all map layers.
- Feature details panel with vote totals, winner, and margin.
- Colorblind mode toggle.
- Precinct overlay can inherit county colors when a precinct key is unmatched (helps avoid gray “holes” when precinct IDs differ between sources).

### Map Styling Notes

- Town/city label halos are reinforced for readability over data fills (stronger opacity when a contest is selected).
- District boundaries use a two-stroke “casing + inner line” treatment (congressional, state house, state senate) with zoom-scaled opacity/width for cleaner readability.

## Scope and Coverage

Coverage is data-driven by generated manifests:
- `Data/contests/manifest.json`
- `Data/district_contests/manifest.json`

In practice, current files support:
- Statewide/county/precinct contest slices primarily from 2000-2024 (varies by office/year).
- District contest slices for congressional, state house, and state senate with strongest recent-cycle coverage.

For authoritative coverage, check manifest entries rather than assumptions.

## Core Questions This Atlas Can Answer

- Did a district flip or just move closer?
- Where are durable strongholds vs persuasion zones?
- How does district behavior differ from county behavior?
- Do congressional, house, and senate views tell the same story?
- Which places break statewide trend lines?

## Repository Layout

- `index.html`: app shell, map logic, style, contest loading, UI state.
- `Data/`: election inputs, boundaries, crosswalks, generated JSON outputs.
- `Data/contests/`: statewide/county/precinct contest slices + manifest.
- `Data/district_contests/`: district contest slices + manifest.
- `Data/crosswalks/`: precinct-to-district allocation tables.
- `Scripts/`: data conversion and build pipeline scripts.

## Data Pipeline (High Level)

1. Normalize or convert source election files into consistent precinct/county CSV format.
2. Build precinct/county contest slices (`Data/contests/*.json`).
3. Build precinct-to-district crosswalks (`Data/crosswalks/*.csv`).
4. Build district contest slices (`Data/district_contests/*.json`).
5. Publish static site + generated data to GitHub Pages.

## Methodology

### Party Bucketing

Major party mapping used by builders:
- Democratic bucket: `DFL`, `DEM`, `D`
- Republican bucket: `R`, `REP`
- Other bucket: all remaining parties

Rows with party markers like `DIST`, `TOTAL`, `TOT`, `EST` are skipped in district aggregation workflows.

### Winner and Margin Calculation

For each unit (precinct/county/district):
- `margin = rep_votes - dem_votes`
- `margin_pct = (margin / total_votes) * 100`
- winner:
  - `REPUBLICAN` when margin > 0
  - `DEMOCRAT` when margin < 0
  - `TIE` when margin = 0

### Color Scale Buckets

Color thresholds are applied to absolute margin percentage:
- `>= 40`
- `>= 30`
- `>= 20`
- `>= 10`
- `>= 5.5`
- `>= 1.0`
- `>= 0.5`
- else neutral

Republican shades use reds and Democratic shades use blues.

### District Aggregation and Fallback Logic

District builder: `Scripts/build_mn_district_contests_from_precinct_and_baf.py`

Primary allocation path:
- Resolve `(county, precinct)` to canonical precinct key.
- Use crosswalk weights from `Data/crosswalks/*.csv`.

Plan selection logic:
- Congressional uses `precinct_to_cd118.csv`.
- State house/senate use:
  - 2022 plan for years `<= 2022`
  - 2024 plan for years `>= 2024`

Fallback behavior:
- For district-native races (`us_house`, `state_house`, `state_senate`), direct district code fallback is attempted first.
- If precinct mapping fails for non-district-native contests, county-level weighted fallback can be used.
- Unmatched votes are tracked in output metadata.

### Candidate Name Selection

Dem/Rep candidate labels in generated outputs are chosen as top-vote names within each aggregated unit (excluding write-ins).

## Data Dictionary

### Canonical Input Schema (`*__mn__general__precinct.csv`)

Common columns used by builders:
- `county`
- `precinct`
- `office`
- `district`
- `candidate`
- `party`
- `votes`

Example file:
- `Data/20201103__mn__general__precinct.csv`

### 2024 Official Field Reference

Reference dictionary:
- `Data/2024-general-federal-state-results-by-precinct-official - Fields.csv`

Important geography and contest fields include:
- `VTDID`, `PCTNAME`, `COUNTYNAME`
- `CONGDIST`, `MNSENDIST`, `MNLEGDIST`
- `USPRSR`, `USPRSDFL`, `USPRSTOTAL`
- `MNSENR`, `MNSENDFL`, `MNSENTOTAL`
- `MNLEGR`, `MNLEGDFL`, `MNLEGTOTAL`

### Crosswalk Schema (`Data/crosswalks/*.csv`)

Columns:
- `precinct_key`
- `district_num`
- `district_code`
- `area_weight`
- `block_count`
- `total_blocks`
- `countyfp`
- `vtdst20`

Interpretation:
- `area_weight` is the share allocated from a precinct key to a district.
- Weights should sum to approximately 1.0 by precinct key.

### Contest Slice Output Schema (`Data/contests/*.json`)

Top-level fields:
- `contest_type`
- `year`
- `state`
- `rows`
- `meta`

Per-row fields:
- `county` (precinct-key label for precinct-derived slices)
- `dem_votes`
- `rep_votes`
- `other_votes`
- `total_votes`
- `dem_candidate`
- `rep_candidate`
- `margin`
- `margin_pct`
- `winner`
- `color`

### District Slice Output Schema (`Data/district_contests/*.json`)

Top-level fields:
- `scope`
- `contest_type`
- `year`
- `state`
- `general.results`
- `meta`

Per-district fields:
- `district`
- `dem_votes`
- `rep_votes`
- `other_votes`
- `total_votes`
- `dem_candidate`
- `rep_candidate`
- `margin`
- `margin_pct`
- `winner`
- `color`

District metadata includes QA-critical fields:
- `total_input_votes`
- `crosswalk_matched_votes`
- `fallback_matched_votes`
- `county_fallback_votes`
- `unmatched_votes`
- `match_coverage_pct`
- `crosswalk_match_pct`

### Manifest Schema

`Data/contests/manifest.json` entries include:
- `contest_type`
- `year`
- `file`
- `rows`
- `dem_total`
- `rep_total`
- `other_total`
- `major_party_contested`
- `match_coverage_pct`

`Data/district_contests/manifest.json` entries include:
- `scope`
- `contest_type`
- `year`
- `file`
- `rows`
- `districts`
- `dem_total`
- `rep_total`
- `other_total`
- `major_party_contested`
- `match_coverage_pct`
- `crosswalk_match_pct`
- `county_fallback_pct`

## Validation and QA

Run these checks after rebuilding:

1. Manifest files exist:

```powershell
Test-Path Data/contests/manifest.json; Test-Path Data/district_contests/manifest.json
```

2. Expected 2024 presidential district row counts:

```powershell
rg '"scope":"state_house","contest_type":"president","year":2024,"file":"state_house_president_2024.json","rows":134' Data/district_contests/manifest.json
rg '"scope":"state_senate","contest_type":"president","year":2024,"file":"state_senate_president_2024.json","rows":67' Data/district_contests/manifest.json
```

3. Inspect specific districts directly in output JSON before assuming UI bug:

```powershell
Get-Content Data/district_contests/state_house_president_2024.json -TotalCount 80
```

4. If colors/winners look stale, rebuild in this order:
- `build_mn_contests_from_precinct_csv.py`
- `build_mn_district_carry_crosswalks.py`
- `build_mn_district_contests_from_precinct_and_baf.py`

Then hard refresh (`Ctrl+F5`).

## Known Limitations

- Coverage varies by year and office; manifests are the source of truth.
- Some cycles depend more heavily on fallback allocation when precinct matching is incomplete.
- District-native race availability is uneven in some years.
- No repository-wide license file is currently included.

## Transparency Notes For Public Presentation

When showing this to a class, newsroom, or public audience:
- Report both winner and margin, not winner only.
- Mention when a map value is crosswalk-allocated rather than direct district reporting.
- Cite the exact source file and build date from JSON `meta`.
- Keep screenshots tied to a specific contest, year, and mode.

## Suggested Storytelling Workflows

### Classroom Exercise Workflow

1. Open one district in `winners`.
2. Switch same district to `margins`.
3. Switch to `shift` and explain movement.
4. Validate with output JSON and identify top candidate labels.

### Public Explainer Workflow

1. Start with county map for orientation.
2. Move to state house/senate to show local detail.
3. Use `flips` and `shift` for intuitive change framing.
4. Close with caveats from `Known Limitations`.

### Political Analysis Workflow

1. Use `margins` to classify safe vs competitive territory.
2. Use `shift` to identify movement clusters.
3. Cross-check suspicious districts in raw JSON.
4. Track whether movement persists across presidential and midterm cycles.

## Build Commands

Run commands from repository root.

### 1) Prepare precinct layers (optional but recommended)

```powershell
python Scripts/prepare_mn_precinct_layers.py
```

### 2) Build county contest slices

```powershell
python Scripts/build_mn_contests_from_county_csv.py --year-min 2000 --year-max 2024
```

### 3) Build precinct contest slices

```powershell
python Scripts/build_mn_contests_from_precinct_csv.py --year-min 2000 --year-max 2024
```

### 4) Build crosswalks

```powershell
python Scripts/build_mn_district_carry_crosswalks.py
```

### 5) Build district contest slices

```powershell
python Scripts/build_mn_district_contests_from_precinct_and_baf.py --year-min 2012 --year-max 2024
```

## Recent Project Updates

- `1003e6f`: fixed legislative candidate labels and Minnesota-themed UI refresh.
- `7bd9aa7`: fixed state house district color-match padding.
- `3e5b4e6`: improved legislative layer UX and click-to-zoom behavior.
- `a9bdaa9`: fixed legislative cross-chamber aggregation and district fallback labels.
- `58d5dc6`: rebuilt 2024 presidential district slices from aligned precinct data.
- `1bacd68`: rebuilt 2012/2016 presidential district slices from official workbooks.

## How To Run (Local and Pages)

### Local

1. Start a static server from repo root:

```powershell
python -m http.server 8000
```

2. Open:

```text
http://localhost:8000/
```

3. After data changes, hard refresh:
- `Ctrl+F5`

### GitHub Pages

1. Keep `index.html` and `Data/` at repo root.
2. Push commits to the branch configured for Pages (currently `main`).
3. Wait for deploy completion.
4. Open the site and hard refresh once.
