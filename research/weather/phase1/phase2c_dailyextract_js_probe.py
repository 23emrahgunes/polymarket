#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from html import unescape
from urllib.parse import urljoin

import requests

TERMS = [
    "dailyextract",
    "absolute daily max",
    "daily max",
    "temperature",
    "ajax",
    "fetch(",
    "getjson",
    "weatherapi",
    "opendata",
    ".json",
    ".php",
    "/cis/",
]

URL_RE = re.compile(r"https?://[^\"'<>\\s]+|/[A-Za-z0-9_./?=&%+-]+(?:\.json|\.php|\.csv|\.txt)(?:\?[^\"'<>\\s]*)?", re.I)
SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.I)
INLINE_RE = re.compile(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.I | re.S)


def compact(s: str, n: int = 1200) -> str:
    s = " ".join(unescape(s).split())
    return s[:n]


def interesting_lines(text: str, limit: int = 80):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        if any(t in low for t in TERMS):
            out.append((i, compact(line)))
            if len(out) >= limit:
                break
    return out


def urls_in(text: str, limit: int = 80):
    seen = []
    for m in URL_RE.finditer(text):
        u = m.group(0).rstrip(")]},;")
        if u not in seen:
            seen.append(u)
        if len(seen) >= limit:
            break
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--month", type=int, default=7)
    args = ap.parse_args()

    page = f"https://www.weather.gov.hk/en/cis/dailyExtract.htm?m={args.month:02d}&y={args.year}"
    s = requests.Session()
    s.headers.update({"User-Agent": "polymarket-weather-phase2c-js-probe/0.1"})

    r = s.get(page, timeout=45)
    r.raise_for_status()
    html = r.text

    print("===== DAILY EXTRACT JS/API PROBE =====")
    print(f"page={page}")
    print(f"status={r.status_code} html_bytes={len(r.content)}")

    scripts = [urljoin(page, x) for x in SCRIPT_RE.findall(html)]
    print(f"external_scripts={len(scripts)}")
    for i, u in enumerate(scripts, 1):
        print(f"  [{i:02d}] {u}")

    print("\n===== HTML INTERESTING LINES =====")
    lines = interesting_lines(html, 50)
    if not lines:
        print("(none)")
    for n, line in lines:
        print(f"L{n}: {line}")

    print("\n===== HTML URL CANDIDATES =====")
    hu = urls_in(html, 60)
    if not hu:
        print("(none)")
    for u in hu:
        print(u)

    inline = INLINE_RE.findall(html)
    print(f"\ninline_scripts={len(inline)}")
    for i, txt in enumerate(inline, 1):
        il = interesting_lines(txt, 30)
        iu = urls_in(txt, 30)
        if il or iu:
            print(f"\n--- INLINE SCRIPT {i} ---")
            for n, line in il:
                print(f"L{n}: {line}")
            for u in iu:
                print(f"URL: {u}")

    print("\n===== EXTERNAL SCRIPT SCAN =====")
    hits = 0
    for i, u in enumerate(scripts, 1):
        try:
            q = s.get(u, timeout=45)
            print(f"\n--- SCRIPT {i}/{len(scripts)} status={q.status_code} bytes={len(q.content)} {u} ---")
            if q.status_code != 200:
                continue
            text = q.text
            il = interesting_lines(text, 80)
            iu = urls_in(text, 80)
            if not il and not iu:
                print("no relevant strings")
                continue
            hits += 1
            for n, line in il:
                print(f"L{n}: {line}")
            if iu:
                print("URL candidates:")
                for x in iu:
                    print(f"  {x}")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")

    print(f"\nrelevant_external_scripts={hits}")
    print("RESULT: inspect any dailyExtract/ajax/fetch/API URL printed above; that is the next source to probe for recent HKO actuals.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
