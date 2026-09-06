#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import shapefile


def county_names(path: Path) -> dict[str, str]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for feature in obj.get("features", []):
        props = feature.get("properties", {}) or {}
        fips = str(props.get("COUNTYFP20") or props.get("COUNTYFP") or "").zfill(3)
        name = str(props.get("NAME20") or props.get("NAME") or "").strip()
        if fips and name:
            out[fips] = name
    return out


def convert(inputs: list[Path], output: Path, vintage: str, names: dict[str, str]) -> None:
    features: list[dict[str, object]] = []
    suffix = "00" if vintage == "2000" else "10"
    county_field = f"COUNTYFP{suffix}"
    code_field = f"VTDST{suffix}"
    name_field = f"NAME{suffix}"
    long_name_field = f"NAMELSAD{suffix}"

    for path in inputs:
        reader = shapefile.Reader(str(path))
        fields = [field[0] for field in reader.fields[1:]]
        for shape_record in reader.iterShapeRecords():
            props = dict(zip(fields, shape_record.record))
            county_fips = str(props.get(county_field) or "").zfill(3)
            county = names.get(county_fips, county_fips)
            code = str(props.get(code_field) or "").strip()
            if not county or not code:
                continue
            props.update({
                "county_nam": county,
                "prec_id": code,
                "precinct_name": f"{county} - {code}",
                "precinct_norm": f"{county} - {code}".upper(),
                "precinct_full_name": str(props.get(name_field) or props.get(long_name_field) or "").strip(),
                "geometry_vintage": vintage,
            })
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": shape_record.shape.__geo_interface__,
            })

    output.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(features)} {vintage} VTD features to {output}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build statewide Minnesota historical VTD GeoJSON layers.")
    parser.add_argument("--counties", type=Path, default=Path("Data/tl_2020_27_county20.geojson"))
    parser.add_argument("--vtd00-glob", default="Data/tl_2008_27*_vtd00.zip")
    parser.add_argument("--vtd10", type=Path, default=Path("Data/tl_2012_27_vtd10.zip"))
    parser.add_argument("--out-vtd00", type=Path, default=Path("Data/precincts_vtd00.geojson"))
    parser.add_argument("--out-vtd10", type=Path, default=Path("Data/precincts_vtd10.geojson"))
    args = parser.parse_args()

    names = county_names(args.counties)
    vtd00_inputs = sorted(Path().glob(args.vtd00_glob))
    if not vtd00_inputs:
        raise SystemExit(f"No VTD00 inputs matched {args.vtd00_glob}")
    convert(vtd00_inputs, args.out_vtd00, "2000", names)
    convert([args.vtd10], args.out_vtd10, "2010", names)


if __name__ == "__main__":
    main()
