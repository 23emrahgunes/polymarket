#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from urllib.parse import quote

import requests

BUCKET = "https://noaa-gefs-pds.s3.amazonaws.com"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def list_prefix(prefix: str, max_keys: int = 1000):
    r = requests.get(BUCKET, params={"list-type": "2", "prefix": prefix, "max-keys": max_keys}, timeout=40)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for c in root.findall("s3:Contents", NS):
        key = c.findtext("s3:Key", default="", namespaces=NS)
        size = int(c.findtext("s3:Size", default="0", namespaces=NS))
        lm = c.findtext("s3:LastModified", default="", namespaces=NS)
        out.append((key, size, lm))
    return out


def head_key(key: str):
    url = f"{BUCKET}/{quote(key, safe='/')}"
    r = requests.head(url, timeout=30, allow_redirects=True)
    return r.status_code, r.headers.get("Content-Length"), r.headers.get("Last-Modified")


def summarize(items):
    keys = [k for k, _, _ in items]
    members = sorted(set(re.findall(r"\b(?:gec00|gep\d{2})\b", "\n".join(keys))))
    fhrs = sorted(set(int(x) for x in re.findall(r"\.f(\d{2,3})(?:\.|$)", "\n".join(keys))))
    idx = [k for k in keys if k.endswith(".idx")]
    grib = [k for k in keys if not k.endswith(".idx") and not k.endswith(".json")]
    return members, fhrs, idx, grib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-date", default="2026-03-21", help="Hong Kong target local date")
    ap.add_argument("--cycle", default="18", choices=["00", "06", "12", "18"])
    args = ap.parse_args()

    target = date.fromisoformat(args.target_date)
    run_date = target - timedelta(days=1) if args.cycle == "18" else target
    ymd = run_date.strftime("%Y%m%d")
    cc = args.cycle

    prefixes = [
        f"gefs.{ymd}/{cc}/atmos/pgrb2ap5/",
        f"gefs.{ymd}/{cc}/atmos/pgrb2sp25/",
        f"gefs.{ymd}/{cc}/atmos/pgrb2a/",
        f"gefs.{ymd}/{cc}/pgrb2a/",
        f"gefs.{ymd}/{cc}/pgrb2b/",
    ]

    print("===== GEFS HISTORICAL S3 PROBE =====")
    print(f"target_hkt_date={target}  conservative_run={ymd} {cc}Z")
    print("Reason: 18Z previous day initializes at 02:00 HKT on target day, before the 08:00 HKT decision.\n")

    found = []
    for p in prefixes:
        try:
            items = list_prefix(p)
        except Exception as e:
            print(f"PREFIX {p}\n  ERROR: {e}\n")
            continue
        print(f"PREFIX {p}\n  objects_returned={len(items)}")
        if items:
            members, fhrs, idx, grib = summarize(items)
            print(f"  members_seen={len(members)} sample={members[:12]}")
            print(f"  forecast_hours_seen={fhrs[:25]}")
            print(f"  idx_seen={len(idx)} grib_like_seen={len(grib)}")
            print("  first_objects:")
            for k, size, lm in items[:12]:
                print(f"    {size:>10}  {lm}  {k}")
            found.append((p, items))
        print()

    if not found:
        print("RESULT: No objects found under known GEFS layouts for this historical run.")
        print("This would mean we need a different NOAA archive source or path before building the ensemble extractor.")
        return 2

    print("===== CANDIDATE FILE CHECK =====")
    # Pick likely remaining-day forecast hours from prior-day 18Z: f006..f021 = 08:00..23:00 HKT target day.
    wanted = [6, 9, 12, 15, 18, 21]
    all_keys = [k for _, items in found for k, _, _ in items]
    for h in wanted:
        hs = f"f{h:03d}"
        matches = [k for k in all_keys if hs in k and ("gec00" in k or "gep01" in k)]
        print(f"{hs}: {len(matches)} sample={matches[:3]}")

    idx_candidates = [k for k in all_keys if k.endswith(".idx")]
    if idx_candidates:
        k = idx_candidates[0]
        st, clen, lm = head_key(k)
        print(f"\nHEAD idx sample: status={st} bytes={clen} last_modified={lm}\n  {k}")
    else:
        print("\nNo .idx object visible in first listing page; extractor may need GRIB inventory via companion index naming or direct GRIB parsing.")

    print("\nRESULT: Historical GEFS objects are present. Use this output to lock the exact path/member/file convention before full download code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
