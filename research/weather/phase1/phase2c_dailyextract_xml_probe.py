#!/usr/bin/env python3
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from collections import Counter

import requests

BASE = "https://www.weather.gov.hk/cis/dailyExtract"


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def candidates(year: int, month: int) -> list[str]:
    return [
        f"{BASE}/dailyExtract_{year}{month:02d}.xml",
        f"{BASE}/dailyExtract_{year}{month}.xml",
        f"{BASE}/dailyExtract_{year}.xml",
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--month", type=int, default=7)
    args = ap.parse_args()

    s = requests.Session()
    s.headers.update({"User-Agent": "polymarket-weather-phase2c-xml-probe/0.1"})

    print("===== HKO DAILY EXTRACT XML PROBE =====")
    chosen = None
    root = None
    raw = None

    for url in candidates(args.year, args.month):
        try:
            r = s.get(url, timeout=45)
            ct = r.headers.get("Content-Type", "")
            print(f"TRY status={r.status_code} bytes={len(r.content)} type={ct} url={url}")
            if r.status_code != 200 or not r.content.strip():
                continue
            try:
                rr = ET.fromstring(r.content)
            except Exception as e:
                print(f"  XML_PARSE_ERROR: {type(e).__name__}: {e}")
                print("  head=", r.text[:300].replace("\n", " "))
                continue
            chosen, root, raw = url, rr, r.content
            break
        except Exception as e:
            print(f"TRY ERROR {url}: {type(e).__name__}: {e}")

    if root is None:
        print("RESULT: no parseable XML candidate found")
        return 2

    print(f"\nCHOSEN={chosen}")
    print(f"root_tag={local(root.tag)} direct_children={len(list(root))} total_bytes={len(raw or b'')}")

    tags = Counter(local(e.tag) for e in root.iter())
    print("\n===== MOST COMMON TAGS =====")
    for k, n in tags.most_common(40):
        print(f"{k}: {n}")

    print("\n===== FIRST LEAF PATHS =====")
    shown = 0

    def walk(e: ET.Element, path: list[str]):
        nonlocal shown
        if shown >= 100:
            return
        kids = list(e)
        p = path + [local(e.tag)]
        if not kids:
            txt = " ".join((e.text or "").split())
            if txt:
                print(f"{'/'.join(p)} = {txt[:300]}")
                shown += 1
            return
        for c in kids:
            walk(c, p)
            if shown >= 100:
                break

    walk(root, [])

    print("\n===== MAX/TEMP/DATE RELATED NODES =====")
    rel = 0
    for e in root.iter():
        tag = local(e.tag)
        txt = " ".join((e.text or "").split())
        blob = f"{tag} {txt}".lower()
        if any(k in blob for k in ["absolute", "max", "temperature", "temp", "date", "day"]):
            attrs = " ".join(f"{k}={v}" for k, v in e.attrib.items())
            print(f"tag={tag} attrs=[{attrs}] text={txt[:500]}")
            rel += 1
            if rel >= 120:
                break
    print(f"related_nodes_printed={rel}")

    print("\n===== RECORD-LIKE CHILD SAMPLES =====")
    # Show compact mappings for elements that have >=3 direct leaf children.
    rec = 0
    for e in root.iter():
        kids = list(e)
        if len(kids) < 3:
            continue
        if not all(len(list(c)) == 0 for c in kids):
            continue
        vals = [(local(c.tag), " ".join((c.text or "").split())) for c in kids]
        if sum(bool(v) for _, v in vals) < 2:
            continue
        print("RECORD", local(e.tag), "|", " | ".join(f"{k}={v[:120]}" for k, v in vals[:30]))
        rec += 1
        if rec >= 20:
            break
    print(f"record_samples={rec}")

    print("\nRESULT: XML source located. Use tag/record samples above to lock the HKO actual-max parser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
