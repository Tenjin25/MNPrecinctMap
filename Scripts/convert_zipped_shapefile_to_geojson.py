#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import shapefile  # pyshp


def normalize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def convert_zip(zip_path: Path, output_path: Path, shp_name: str | None = None) -> tuple[int, list[str]]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        shp_candidates = [n for n in names if n.lower().endswith(".shp")]
        if not shp_candidates:
            raise FileNotFoundError(f"No .shp found in {zip_path}")

        if shp_name:
            wanted = (shp_name if shp_name.lower().endswith(".shp") else f"{shp_name}.shp").lower()
            shp_member = next((n for n in shp_candidates if Path(n).name.lower() == wanted), None)
            if not shp_member:
                raise FileNotFoundError(f"{wanted} not found in {zip_path}")
        elif len(shp_candidates) == 1:
            shp_member = shp_candidates[0]
        else:
            raise RuntimeError(
                f"Multiple .shp files in {zip_path}; pass --shp-name. Found: {', '.join(Path(n).name for n in shp_candidates)}"
            )

        stem = Path(shp_member).with_suffix("").as_posix()
        shx_member = next((n for n in names if n.lower() == f"{stem}.shx".lower()), None)
        dbf_member = next((n for n in names if n.lower() == f"{stem}.dbf".lower()), None)
        if not shx_member or not dbf_member:
            raise FileNotFoundError(f"Missing .shx or .dbf sidecar for {shp_member} in {zip_path}")

        shp_io = io.BytesIO(zf.read(shp_member))
        shx_io = io.BytesIO(zf.read(shx_member))
        dbf_io = io.BytesIO(zf.read(dbf_member))
        reader = shapefile.Reader(shp=shp_io, shx=shx_io, dbf=dbf_io, encoding="utf-8")

    field_names = [f[0] for f in reader.fields[1:]]
    features = []
    for sr in reader.iterShapeRecords():
        props = {field_names[i]: normalize_value(sr.record[i]) for i in range(len(field_names))}
        features.append({"type": "Feature", "properties": props, "geometry": sr.shape.__geo_interface__})

    fc = {"type": "FeatureCollection", "features": features}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")

    return len(features), field_names


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a zipped shapefile to GeoJSON.")
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True, help="Path to .zip containing .shp/.dbf/.shx.")
    parser.add_argument("--out", dest="output_path", type=Path, required=True, help="Output GeoJSON path.")
    parser.add_argument("--shp-name", default=None, help="Optional shapefile basename (with or without .shp).")
    args = parser.parse_args()

    count, fields = convert_zip(args.zip_path, args.output_path, shp_name=args.shp_name)
    print(f"{args.zip_path} -> {args.output_path} ({count} features, {len(fields)} fields)")


if __name__ == "__main__":
    main()
