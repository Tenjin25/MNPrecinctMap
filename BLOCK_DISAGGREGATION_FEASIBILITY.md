# Minnesota block-disaggregation feasibility

## Conclusion

A Pennsylvania-style allocation pipeline works with the Minnesota congressional, state House, and state Senate layers already in this repository. The completed production build produces 100% allocated coverage while keeping the uncertain county fallback separate: 99.50%-99.998% of statewide votes use an identified current VTD, VTD10, or VTD00/block relationship. County-wide district weights cover the remaining 0.002%-0.50%.

The present method should be described as **equal census-block-count allocation**, not area weighting or observed block-level voting. It estimates how a precinct's votes should be divided when a current district line splits that precinct. It does not identify where individual votes were cast inside the precinct.

The Pennsylvania reference uses the same general pattern: match historical returns to a stable VTD geography, use census blocks to build VTD-to-current-district shares, and preserve residuals and coverage metadata. See the [PAPrecinctMap methodology](https://github.com/Tenjin25/PAPrecinctMap#4-reallocating-statewide-votes-to-current-district-lines).

## Current Minnesota crosswalk

The existing files in `Data/crosswalks/` assign 198,705 2020 census blocks to the current district layers and produce weights that sum to 1.0 for every modern VTD.

| Scope | VTDs | Split VTDs | Maximum districts in one VTD | Bad weight sums |
| --- | ---: | ---: | ---: | ---: |
| Congressional | 4,110 | 32 | 2 | 0 |
| State House | 4,110 | 288 | 4 | 0 |
| State Senate | 4,110 | 171 | 3 | 0 |

The `area_weight` column is currently calculated as `block_count / total_blocks`; it is not an area measurement.

## Built legacy bridge (2000-2008)

The build now has four explicit historical components:

1. `legacy_precinct_bridge_2000_2008.csv` preserves the source `PRCT`, county code, normalized alias, selected target, resolution rule, and conflict flag.
2. `vtd00_to_*.csv` assigns all 198,705 eligible 2020 census blocks to VTD00 and the current congressional/legislative layers, then derives per-VTD block-count weights. This covers 4,089 of 4,094 VTD00 features; five historical VTDs contain no eligible 2020 block internal point. There were 112 same-county nearest-VTD assignments and zero district misses.
3. `vtd10_to_*.csv` provides the primary 2002-2008 anchor. It covers 4,136 of 4,139 VTD10 features, with 73 same-county nearest-VTD assignments, zero missing block assignments, zero district misses, and valid weight sums.
4. The district builder uses direct reported districts for district-native contests, the historical bridge for statewide contests, and county weights only for the residual. Every vote row is allocated with Hamilton/largest-remainder rounding.

The diagnostic preview in `Data/district_contests_legacy_preview/` contains 59 district-contest slices. Production promotes only the 45 statewide-derived slices: 15 contests across congressional, modern state House, and modern state Senate lines. The 14 historical district-native slices are excluded because their reported historical district codes do not describe the modern geometry. For statewide contests, vote-weighted allocation paths are:

| Year | Identified bridge | VTD00 | VTD10 | Current VTD | County fallback | Total coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 99.5005% | 98.9002% | 0.0000% | 0.6003% | 0.4995% | 100.0000% |
| 2002 | 99.7744% | 1.0182% | 98.4246% | 0.3317% | 0.2256% | 100.0000% |
| 2004 | 99.7102% | 0.8948% | 98.5216% | 0.2938% | 0.2898% | 100.0000% |
| 2006 | 99.8066% | 0.6195% | 98.9600% | 0.2271% | 0.1934% | 100.0000% |
| 2008 | 99.9981% | 0.1328% | 99.8412% | 0.0241% | 0.0019% | 100.0000% |

All 45 promoted slices have a zero vote-conservation delta. The 100% figure is coverage, not a claim that every historical precinct identity is known. County fallback smooths the residual across every current district in the county and should remain visible in metadata for close-district analysis.

## What the source precinct codes add

The five official workbooks include a county code and `PRCT` identifier, but the seven-column OpenElections output intentionally retains only the precinct label. The sidecar bridge preserves the source identifiers without changing the OpenElections schema.

During the build, the older county-results lookup was found to omit Meeker and shift county codes 47-61. The converter now gives the official SOS `Counties.csv` lookup precedence. VTD00 recovery first reduced unresolved bridge rows from 1,376 to 196; the VTD10 anchor reduced them again to 44.

Tests of the raw `PRCT` codes found:

| Year | Exact match to 2020 VTD code | Name-or-code union | Name/code vote disagreement |
| --- | ---: | ---: | ---: |
| 2000 | 79.6% of precincts | 88.2% of statewide votes | 20.1% |
| 2002 | 87.3% of precincts | 92.2% of statewide votes | 4.5% |
| 2004 | 87.6% of precincts | 91.8% of statewide votes | 4.8% |
| 2006 | 87.6% of precincts | 91.9% of statewide votes | 5.0% |
| 2008 | 87.8% of precincts | 92.0% of statewide votes | 4.7% |

The union is diagnostic only. Name and code matches are not merged blindly. The final rules use VTD00 as the 2000 anchor and VTD10 as the usual 2002-2008 anchor. VTD10 code/name agreement resolves 405 former current-VTD conflicts. Eighteen St. Cloud rows are resolved by the explicit `legacy_precinct_overrides_2000_2008.csv` sidecar using VTD00 at high confidence: the SOS source keeps the same precinct names and `PRCT` codes from 2000 through 2006, those codes match the VTD00 names, and the 2008 source simultaneously changes the codes and several reported district assignments to the VTD10 scheme. Four other same-cycle 2000 conflicts retain the exact VTD00 code. All 427 conflict rows are marked resolved, and none remain in manual-review status.

The historical `vtds_2000.geojson` layer is especially promising for 2000: 94.1% of the 2000 source precincts match a VTD00 code directly. The same match declines to roughly 80% by 2008 as precinct definitions evolve.

## Remaining production cautions

1. The 44 unresolved aliases still use county weights. They account for at most 0.50% of statewide votes in these cycles, but the residual should remain visible in metadata.
2. Prefer voting-age population, CVAP, registered-voter, or validated turnout weights when available. Equal block count is a clearly labeled fallback, not observed block-level voting.
3. Keep bridge, VTD00, VTD10, resolved-conflict, county fallback, unmatched, and conservation metrics in every generated slice.
4. Validate reconstructed district totals against any official district summaries before displaying them as historical district results.

District-native races (`U.S. House`, `State House`, and `State Senate`) remain useful as diagnostics with their reported historical district fields, but they are not promoted onto the modern geometry. The production build is limited to statewide contests placed onto current district lines.
