#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def clean(value: Any) -> str:
    return ("" if value is None else str(value)).strip()


def normalize_token(value: str) -> str:
    token = clean(value).upper()
    filtered = "".join(ch for ch in token if ch.isalnum() or ch in " .-")
    return " ".join(filtered.split())


def parse_coord(value: Any) -> float | None:
    token = clean(value)
    if token == "":
        return None
    try:
        return float(token)
    except ValueError:
        return None


def bbox_center(geometry: dict[str, Any]) -> tuple[float, float] | None:
    if not geometry:
        return None

    coords = geometry.get("coordinates")
    gtype = clean(geometry.get("type"))

    def walk(node: Any):
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and isinstance(node[0], (int, float)) and isinstance(node[1], (int, float)):
                yield float(node[0]), float(node[1])
            else:
                for child in node:
                    yield from walk(child)

    if gtype == "Point" and isinstance(coords, (list, tuple)) and len(coords) >= 2:
        return float(coords[0]), float(coords[1])

    points = list(walk(coords))
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare MN precinct polygon and centroid GeoJSON files for the atlas."
    )
    parser.add_argument("--precincts-in", type=Path, default=Path("Data/precincts.geojson"))
    parser.add_argument("--counties-in", type=Path, default=Path("Data/tl_2020_27_county20.geojson"))
    parser.add_argument("--precincts-out", type=Path, default=Path("Data/precincts.geojson"))
    parser.add_argument("--centroids-out", type=Path, default=Path("Data/precinct_centroids.geojson"))
    args = parser.parse_args()

    precincts = json.loads(args.precincts_in.read_text(encoding="utf-8"))
    counties = json.loads(args.counties_in.read_text(encoding="utf-8"))

    county_name_by_fp: dict[str, str] = {}
    for feature in counties.get("features", []):
        props = feature.get("properties", {}) or {}
        fp = clean(props.get("COUNTYFP20"))
        name = clean(props.get("NAME20")) or clean(props.get("NAMELSAD20"))
        if fp and name:
            county_name_by_fp[fp] = name.replace(" County", "").strip()

    centroid_features: list[dict[str, Any]] = []
    out_features: list[dict[str, Any]] = []

    for feature in precincts.get("features", []):
        props = dict(feature.get("properties", {}) or {})
        county_fp = clean(props.get("COUNTYFP20"))
        county_name = (
            clean(props.get("county_nam"))
            or clean(props.get("COUNTYNAME"))
            or county_name_by_fp.get(county_fp, "")
        )
        prec_id = clean(props.get("prec_id")) or clean(props.get("PREC_ID")) or clean(props.get("VTDST20"))
        if prec_id == "":
            prec_id = clean(props.get("GEOID20"))

        precinct_name = f"{county_name} - {prec_id}".strip(" -")
        precinct_norm = normalize_token(precinct_name)

        props["county_nam"] = county_name
        props["prec_id"] = prec_id
        props["precinct_name"] = precinct_name
        props["precinct_norm"] = precinct_norm

        geom = feature.get("geometry")
        out_features.append({"type": "Feature", "properties": props, "geometry": geom})

        lon = parse_coord(props.get("INTPTLON20"))
        lat = parse_coord(props.get("INTPTLAT20"))
        if lon is None or lat is None:
            center = bbox_center(geom or {})
            if center is not None:
                lon, lat = center
        if lon is None or lat is None:
            continue

        centroid_features.append(
            {
                "type": "Feature",
                "properties": {
                    "county_nam": county_name,
                    "prec_id": prec_id,
                    "precinct_norm": precinct_norm,
                    "precinct_name": precinct_name,
                    "COUNTYFP20": county_fp,
                    "GEOID20": clean(props.get("GEOID20")),
                },
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
            }
        )

    out_precincts = {"type": "FeatureCollection", "features": out_features}
    out_centroids = {"type": "FeatureCollection", "features": centroid_features}

    args.precincts_out.parent.mkdir(parents=True, exist_ok=True)
    args.precincts_out.write_text(json.dumps(out_precincts, separators=(",", ":")), encoding="utf-8")
    args.centroids_out.parent.mkdir(parents=True, exist_ok=True)
    args.centroids_out.write_text(json.dumps(out_centroids, separators=(",", ":")), encoding="utf-8")

    print(
        f"Prepared precinct polygons: {args.precincts_out} ({len(out_features)} features)\n"
        f"Prepared precinct centroids: {args.centroids_out} ({len(centroid_features)} features)"
    )


if __name__ == "__main__":
    main()
