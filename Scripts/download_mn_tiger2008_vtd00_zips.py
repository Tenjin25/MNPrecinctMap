#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import urllib.error
import urllib.request
from pathlib import Path

import json


def slug_candidates(name: str) -> list[str]:
    n = (name or "").strip()
    if not n:
        return []
    base = [n]
    if "." in n:
        base.append(n.replace(".", ""))
    if "'" in n:
        base.append(n.replace("'", ""))
    out: list[str] = []
    for b in base:
        s = re.sub(r"\s+", "_", b.strip())
        s = re.sub(r"[^A-Za-z0-9_.-]", "", s)
        if s and s not in out:
            out.append(s)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Download MN TIGER2008 county vtd00 zips.")
    parser.add_argument("--counties-geojson", type=Path, default=Path("Data/tl_2020_27_county20.geojson"))
    parser.add_argument("--out-dir", type=Path, default=Path("Data"))
    parser.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args()

    counties_obj = json.loads(args.counties_geojson.read_text(encoding="utf-8"))
    features = counties_obj.get("features", [])
    args.out_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    fail = 0
    skipped = 0

    for feat in features:
        props = feat.get("properties", {}) or {}
        county_fp = str(props.get("COUNTYFP20", "")).strip().zfill(3)
        county_name = str(props.get("NAME20", "")).strip()
        if not county_fp or not county_name:
            continue

        geocode = f"27{county_fp}"
        out_zip = args.out_dir / f"tl_2008_{geocode}_vtd00.zip"
        if out_zip.exists() and out_zip.stat().st_size > 0:
            print(f"SKIP  {out_zip.name}")
            skipped += 1
            continue

        downloaded = False
        for slug in slug_candidates(county_name):
            url = (
                "https://www2.census.gov/geo/tiger/TIGER2008/27_MINNESOTA/"
                f"{geocode}_{slug}_County/tl_2008_{geocode}_vtd00.zip"
            )
            try:
                with urllib.request.urlopen(url, timeout=args.timeout) as resp:
                    data = resp.read()
                if not data:
                    continue
                out_zip.write_bytes(data)
                print(f"OK    {out_zip.name} <- {url}")
                ok += 1
                downloaded = True
                break
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    print(f"WARN  {county_name} ({geocode}) {url} -> HTTP {e.code}")
            except Exception as e:
                print(f"WARN  {county_name} ({geocode}) {url} -> {e}")

        if not downloaded:
            print(f"FAIL  {county_name} ({geocode})")
            fail += 1

    print(f"Done. ok={ok} skipped={skipped} fail={fail}")


if __name__ == "__main__":
    main()
