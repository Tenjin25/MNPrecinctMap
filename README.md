# North Star Vote Atlas

Minnesota election atlas built for fast reporting, district trend checks, and late-night map nerding.

If you are a data journalist or political junkie, this repo is meant to answer practical questions quickly:
- Where did a district actually move since the last cycle?
- Did this seat flip, or just tighten?
- Is the margin pattern at county level the same story you see in state house or senate boundaries?

This is a static GitHub Pages app (`index.html` + `Data/`) with no frontend build step.

## Who This Is For

- Data reporters writing local and statewide election stories.
- Campaign and policy watchers tracking geographic shifts.
- Civic nerds who want reproducible precinct-to-district aggregation logic.

## What You Can Do Fast

- Switch across county, congressional, state house, and state senate maps.
- Toggle between `margins`, `winners`, `shift`, and `flips`.
- Drill into a feature to inspect vote totals, winner, and change context.
- Compare patterns across years from the same interface.

## Live Site (GitHub Pages)

This app is hosted from your Pages site. The app already handles subpath hosting (for example `https://<user>.github.io/<repo>/`) through the `withBase()` path resolver in [`index.html`](./index.html).

If you move this to a different repo name or custom domain, keep the relative `./Data/...` layout unchanged.

## Election Coverage Strengths

- Four map views:
  - Counties
  - Congressional districts
  - State house districts
  - State senate districts
- Contest selector with year support.
- Visualization modes:
  - Margins
  - Winners
  - Shift
  - Flips
- Hover + click detail panels with vote breakdowns and trend context.
- Click-to-zoom on county, district, and precinct layers.
- Colorblind mode toggle.
- Crosswalk-based district carryover support for reallocated historical contests.

## Reporting Angles You Can Support

- Districts that changed party vs districts that only changed intensity.
- Urban/rural or metro/non-metro movement between cycles.
- House and senate split-ticket geography inside the same senate footprint.
- Outlier districts where presidential and legislative coalitions diverge.

## Repository Layout

- [`index.html`](./index.html): App UI, map logic, data loading, and interaction behavior.
- [`Data/`](./Data): GeoJSON boundaries, contests, district contest slices, crosswalks, and source election files.
- [`Data/contests/`](./Data/contests): County/precinct contest slices + manifest.
- [`Data/district_contests/`](./Data/district_contests): District contest slices + manifest.
- [`Data/crosswalks/`](./Data/crosswalks): Precinct-to-district carryover crosswalk CSVs.
- [`Scripts/`](./Scripts): Data build and transformation scripts.

## Local Development

No frontend build step is required.

1. Start a local static server from repo root:

```powershell
python -m http.server 8000
```

2. Open:

```text
http://localhost:8000/
```

3. Hard refresh after data changes (`Ctrl+F5`), especially when JSON files were cached.

## Data Integrity Notes (For Publishing)

- District-level historical carryover depends on crosswalk allocation from precinct geography.
- Presidential district slices have source-specific rebuilds by cycle (see notes below).
- If a district color looks wrong, regenerate contests and manifests first, then hard refresh.
- Treat every rebuild as a versioned dataset update: commit data + manifest together.

## GitHub Pages Deployment

This repo is static, so deployment is standard Pages publishing:

1. Push changes to the branch configured for Pages (currently `main`).
2. Wait for Pages to rebuild.
3. Hard refresh browser cache.

Notes:
- Keep `index.html` at repo root.
- Keep the `Data/` directory at repo root.
- Do not convert relative paths to absolute root paths unless you also change `withBase()` logic.

## Data Build Workflows

All script paths below are under `Scripts/` (capital `S`).

### 1. Build County Contest Slices

Input: `Data/*__mn__general__county.csv`  
Output: `Data/contests/*.json` + `Data/contests/manifest.json`

```powershell
python Scripts/build_mn_contests_from_county_csv.py --year-min 2000 --year-max 2024
```

### 2. Build Precinct Contest Slices

Input: `Data/*__mn__general__precinct.csv` and `Data/precincts.geojson`  
Output: `Data/contests/*.json` + `Data/contests/manifest.json`

```powershell
python Scripts/build_mn_contests_from_precinct_csv.py --year-min 2000 --year-max 2024
```

### 3. Build District Carryover Crosswalks

Input:
- `Data/BlockAssign_ST27_MN.zip`
- `Data/precincts.geojson`
- `Data/nhgis_blk2020_blk2010_27.zip` (optional validation check)

Output:
- `Data/crosswalks/precinct_to_cd118.csv`
- `Data/crosswalks/precinct_to_2022_state_house.csv`
- `Data/crosswalks/precinct_to_2024_state_house.csv`
- `Data/crosswalks/precinct_to_2022_state_senate.csv`
- `Data/crosswalks/precinct_to_2024_state_senate.csv`

```powershell
python Scripts/build_mn_district_carry_crosswalks.py
```

### 4. Build District Contest Slices

Input:
- `Data/*__mn__general__precinct.csv`
- `Data/crosswalks/*.csv`
- `Data/precincts.geojson`

Output:
- `Data/district_contests/*.json`
- `Data/district_contests/manifest.json`

```powershell
python Scripts/build_mn_district_contests_from_precinct_and_baf.py --year-min 2012 --year-max 2024
```

## Presidential District Data Notes

Current presidential district sourcing:

- 2024 district presidential slices were rebuilt from:
  - `Data/2024-general-federal-state-results-by-precinct-official - Precinct-Results - aligned.csv`
- 2012 and 2016 district presidential slices were rebuilt from:
  - `Data/2012_general_precinct_official.xlsx`
  - `Data/2016_general_precinct_official.xlsx`
- 2020 district presidential slices currently remain based on existing precinct + crosswalk workflow because no equivalent `2020_general_precinct_official.xlsx` file is present in this repo.

## Methodology At A Glance

- Base unit is precinct-level election returns.
- District aggregation is produced by precinct-to-district crosswalks.
- Historical district views use carryover allocation when boundaries and cycles do not align cleanly.
- Frontend colors are driven by computed winner + margin from prebuilt JSON slices.

## Interaction Summary

- View buttons switch geometry layers (counties, congressional, house, senate).
- Contest dropdown controls vote data source by office/year.
- Viz mode controls coloring logic:
  - `margins`: intensity by margin
  - `winners`: binary party winner colors
  - `shift`: movement versus previous available cycle
  - `flips`: only flipped districts highlighted
- Click on map features to pin context and zoom to feature extent.

## Troubleshooting

- Map loads but no data colors:
  - Confirm `Data/contests/manifest.json` and `Data/district_contests/manifest.json` exist and include target year.
- Wrong values after update:
  - Hard refresh (`Ctrl+F5`) and verify JSON files changed on Pages.
- 404s on Pages:
  - Ensure files are committed in correct case-sensitive paths (`Scripts/` vs `scripts/`, `Data/`).
- Crosswalk mismatches:
  - Rebuild crosswalks and district contests in sequence (steps 3 then 4 above).

## Suggested Story Workflow

1. Pick contest + year and run `margins` to identify strongholds and close terrain.
2. Switch to `shift` to find where coalition movement is largest.
3. Switch to `flips` to isolate narrative districts.
4. Validate suspicious districts against source precinct CSV rows before publishing.

## Maintenance Checklist

When refreshing data:

1. Rebuild the relevant JSON slices.
2. Verify manifests were regenerated or updated.
3. Spot-check known districts/counties in app.
4. Commit both data files and manifest changes together.
5. Push and hard-refresh the Pages site.
