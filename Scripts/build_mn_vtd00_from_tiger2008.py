#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import shapefile  # pyshp


def clean(value: Any) -> str:
    return ("" if value is None else str(value)).strip()


def normalize_token(value: str) -> str:
    token = clean(value).upper()
    filtered = "".join(ch for ch in token if ch.isalnum() or ch in " .-")
    return " ".join(filtered.split())


def load_county_name_map(counties_geojson: Path) -> dict[str, str]:
    obj = json.loads(counties_geojson.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for feature in obj.get("features", []):
        props = feature.get("properties", {}) or {}
        fp20 = clean(props.get("COUNTYFP20"))
        name = clean(props.get("NAME20")) or clean(props.get("NAMELSAD20")).replace(" County", "")
        if fp20 and name:
            out[fp20] = name
    return out


def read_zip_features(zip_path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        shp_name = next((n for n in names if n.lower().endswith(".shp")), None)
        if not shp_name:
            return []
        stem = Path(shp_name).with_suffix("").as_posix()
        shx_name = next((n for n in names if n.lower() == f"{stem}.shx".lower()), None)
        dbf_name = next((n for n in names if n.lower() == f"{stem}.dbf".lower()), None)
        if not shx_name or not dbf_name:
            return []

        reader = shapefile.Reader(
            shp=io.BytesIO(zf.read(shp_name)),
            shx=io.BytesIO(zf.read(shx_name)),
            dbf=io.BytesIO(zf.read(dbf_name)),
            encoding="utf-8",
        )
        field_names = [f[0] for f in reader.fields[1:]]

        features = []
        for sr in reader.iterShapeRecords():
            props = {field_names[i]: sr.record[i] for i in range(len(field_names))}
            features.append({"type": "Feature", "properties": props, "geometry": sr.shape.__geo_interface__})
        return features


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build MN statewide 2000 VTD GeoJSON from TIGER 2008 county zip files."
    )
    parser.add_argument("--data-dir", type=Path, default=Path("Data"))
    parser.add_argument("--counties", type=Path, default=Path("Data/tl_2020_27_county20.geojson"))
    parser.add_argument("--out", type=Path, default=Path("Data/vtds_2000.geojson"))
    args = parser.parse_args()

    county_name_by_fp = load_county_name_map(args.counties)
    zip_paths = sorted(args.data_dir.glob("tl_2008_27*_vtd00.zip"))
    if not zip_paths:
        raise SystemExit("No tl_2008_27*_vtd00.zip files found in Data.")

    merged: list[dict[str, Any]] = []
    for zip_path in zip_paths:
        feats = read_zip_features(zip_path)
        for feat in feats:
            props = dict(feat.get("properties", {}) or {})
            county_fp = clean(props.get("COUNTYFP00"))
            county_name = county_name_by_fp.get(county_fp, "")
            prec_id = clean(props.get("VTDST00")) or clean(props.get("VTDIDFP00")) or clean(props.get("NAME00"))
            precinct_name = f"{county_name} - {prec_id}".strip(" -")
            props["county_nam"] = county_name
            props["prec_id"] = prec_id
            props["precinct_name"] = precinct_name
            props["precinct_norm"] = normalize_token(precinct_name)
            feat["properties"] = props
            merged.append(feat)

    out_obj = {"type": "FeatureCollection", "features": merged}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out_obj, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {args.out} with {len(merged)} features from {len(zip_paths)} county zip(s)")


if __name__ == "__main__":
    main()
