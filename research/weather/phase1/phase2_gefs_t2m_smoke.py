#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import numpy as np
import requests

BUCKET = "https://noaa-gefs-pds.s3.amazonaws.com"
# Hong Kong Observatory Headquarters (approx. 22°18'07\"N, 114°10'27\"E)
HKO_LAT = 22.301944
HKO_LON = 114.174167


def url_for(key: str) -> str:
    return f"{BUCKET}/{quote(key, safe='/')}"


def idx_key(ymd: str, cycle: str, member: str, fhr: int) -> str:
    return (
        f"gefs.{ymd}/{cycle}/atmos/pgrb2ap5/"
        f"{member}.t{cycle}z.pgrb2a.0p50.f{fhr:03d}.idx"
    )


def grib_key(ymd: str, cycle: str, member: str, fhr: int) -> str:
    return idx_key(ymd, cycle, member, fhr)[:-4]


def parse_tmp2m_range(text: str) -> tuple[int, int | None, str]:
    rows = []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue
        try:
            offset = int(parts[1])
        except Exception:
            continue
        rows.append((offset, line))

    for i, (start, line) in enumerate(rows):
        # Standard NCEP inventory wording for 2-m temperature.
        if ":TMP:2 m above ground:" not in line:
            continue
        end = rows[i + 1][0] - 1 if i + 1 < len(rows) else None
        return start, end, line
    raise RuntimeError("TMP:2 m above ground record not found in .idx")


def fetch_tmp_message(session: requests.Session, ymd: str, cycle: str, member: str, fhr: int) -> tuple[bytes, str, tuple[int, int | None]]:
    ikey = idx_key(ymd, cycle, member, fhr)
    gkey = grib_key(ymd, cycle, member, fhr)

    r = session.get(url_for(ikey), timeout=35)
    r.raise_for_status()
    start, end, inventory_line = parse_tmp2m_range(r.text)

    headers = {"Range": f"bytes={start}-{end}" if end is not None else f"bytes={start}-"}
    g = session.get(url_for(gkey), headers=headers, timeout=45)
    if g.status_code not in (200, 206):
        raise RuntimeError(f"GRIB range GET failed status={g.status_code}")
    # A 200 here would mean Range was ignored and the whole file came back; abort rather than
    # accidentally processing/downloading huge files in the full runner.
    if g.status_code == 200 and len(g.content) > 5_000_000:
        raise RuntimeError(f"Server ignored Range header; got whole object bytes={len(g.content)}")
    if not g.content.startswith(b"GRIB"):
        raise RuntimeError(f"Downloaded slice does not start with GRIB magic; bytes={len(g.content)}")
    return g.content, inventory_line, (start, end)


def decode_nearest_c(message: bytes, lat: float, lon: float) -> dict:
    try:
        from eccodes import codes_get, codes_get_array, codes_grib_new_from_file, codes_release
    except Exception as e:
        raise RuntimeError(
            "ecCodes Python bindings are not installed/working. Run: pip install eccodes && python -m eccodes selfcheck"
        ) from e

    with tempfile.NamedTemporaryFile(prefix="gefs_t2m_", suffix=".grib2", delete=False) as tmp:
        tmp.write(message)
        path = Path(tmp.name)

    gid = None
    try:
        with path.open("rb") as f:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                raise RuntimeError("ecCodes could not decode GRIB message")
            lats = np.asarray(codes_get_array(gid, "latitudes"), dtype=float)
            lons = np.asarray(codes_get_array(gid, "longitudes"), dtype=float)
            vals = np.asarray(codes_get_array(gid, "values"), dtype=float)
            units = str(codes_get(gid, "units"))
            short_name = str(codes_get(gid, "shortName"))
            level = codes_get(gid, "level")

            # Longitude-safe approximate nearest-neighbour metric.
            dlon = np.abs(((lons - lon + 180.0) % 360.0) - 180.0)
            dx = dlon * math.cos(math.radians(lat))
            dy = lats - lat
            j = int(np.argmin(dx * dx + dy * dy))
            v = float(vals[j])
            c = v - 273.15 if units.upper().startswith("K") else v
            return {
                "temp_c": c,
                "grid_lat": float(lats[j]),
                "grid_lon": float(lons[j]),
                "raw_value": v,
                "units": units,
                "short_name": short_name,
                "level": level,
            }
    finally:
        if gid is not None:
            codes_release(gid)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def member_names(n_perturbed: int) -> list[str]:
    n = max(0, min(int(n_perturbed), 30))
    return ["gec00"] + [f"gep{i:02d}" for i in range(1, n + 1)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-date", default="2026-03-21")
    ap.add_argument("--cycle", default="18", choices=["00", "06", "12", "18"])
    ap.add_argument("--perturbed", type=int, default=2, help="Number of perturbed members to test (plus control); max 30")
    ap.add_argument("--lat", type=float, default=HKO_LAT)
    ap.add_argument("--lon", type=float, default=HKO_LON)
    args = ap.parse_args()

    target = date.fromisoformat(args.target_date)
    run_date = target - timedelta(days=1) if args.cycle == "18" else target
    ymd = run_date.strftime("%Y%m%d")
    fhrs = [6, 9, 12, 15, 18, 21]
    members = member_names(args.perturbed)

    s = requests.Session()
    s.headers.update({"User-Agent": "polymarket-weather-phase2-gefs/0.1"})

    print("===== GEFS T2M RANGE+DECODE SMOKE =====")
    print(f"target_hkt_date={target} run={ymd} {args.cycle}Z members={members}")
    print(f"HKO target point lat={args.lat:.6f} lon={args.lon:.6f}")
    print("f006..f021 from previous-day 18Z correspond approximately 08:00..23:00 HKT.\n")

    member_max = {}
    first_grid = None
    for member in members:
        vals = []
        print(f"--- {member} ---")
        for fhr in fhrs:
            blob, inv, byte_range = fetch_tmp_message(s, ymd, args.cycle, member, fhr)
            dec = decode_nearest_c(blob, args.lat, args.lon)
            vals.append(dec["temp_c"])
            if first_grid is None:
                first_grid = (dec["grid_lat"], dec["grid_lon"])
            end_s = str(byte_range[1]) if byte_range[1] is not None else "EOF"
            print(
                f"f{fhr:03d} temp={dec['temp_c']:.2f}C grid=({dec['grid_lat']:.3f},{dec['grid_lon']:.3f}) "
                f"slice={byte_range[0]}-{end_s} bytes={len(blob)} shortName={dec['short_name']} level={dec['level']}"
            )
        member_max[member] = max(vals)
        print(f"member_remaining_day_max={member_max[member]:.2f}C\n")

    print("===== MEMBER MAX SUMMARY =====")
    for m, v in member_max.items():
        print(f"{m}: {v:.2f}C")
    arr = np.array(list(member_max.values()), dtype=float)
    print(f"ensemble_test_mean={arr.mean():.2f}C min={arr.min():.2f}C max={arr.max():.2f}C n={len(arr)}")
    if first_grid:
        print(f"nearest_GEFS_grid={first_grid[0]:.3f},{first_grid[1]:.3f}")
    print("RESULT: byte-range extraction + ecCodes point decoding succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
