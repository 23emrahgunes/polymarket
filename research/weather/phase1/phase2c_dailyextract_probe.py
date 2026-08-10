#!/usr/bin/env python3
from __future__ import annotations

import argparse
from html.parser import HTMLParser
from typing import Any

import requests


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "table":
            self._depth += 1
            if self._depth == 1:
                self._table = []
        elif self._depth == 1 and t == "tr":
            self._row = []
        elif self._depth == 1 and t in {"td", "th"}:
            self._cell = []
        elif self._depth == 1 and self._cell is not None and t == "br":
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._depth == 1 and self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if self._depth == 1 and t in {"td", "th"} and self._cell is not None:
            txt = " ".join("".join(self._cell).split())
            if self._row is not None:
                self._row.append(txt)
            self._cell = None
        elif self._depth == 1 and t == "tr" and self._row is not None:
            if self._table is not None and any(x for x in self._row):
                self._table.append(self._row)
            self._row = None
        elif t == "table" and self._depth:
            if self._depth == 1 and self._table is not None:
                self.tables.append(self._table)
                self._table = None
            self._depth -= 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--month", type=int, default=7)
    args = ap.parse_args()

    url = f"https://www.weather.gov.hk/en/cis/dailyExtract.htm?m={args.month:02d}&y={args.year}"
    r = requests.get(url, timeout=45, headers={"User-Agent": "polymarket-weather-phase2c-dailyextract/0.1"})
    print("===== HKO DAILY EXTRACT PROBE =====")
    print(f"url={url}")
    print(f"status={r.status_code} content_type={r.headers.get('Content-Type')} bytes={len(r.content)}")
    r.raise_for_status()
    html = r.text

    for needle in ["Absolute Daily Max", "Daily Max", "Maximum", "Air Temperature", "dailyExtract"]:
        print(f"contains[{needle!r}]={needle.lower() in html.lower()}")

    p = TableParser()
    p.feed(html)
    print(f"tables_found={len(p.tables)}")

    # Print compact samples only; enough to lock the actual table/schema without flooding SSH.
    for i, table in enumerate(p.tables[:12]):
        widths = sorted({len(r) for r in table})
        print(f"\n--- TABLE {i} rows={len(table)} widths={widths[:10]} ---")
        for row in table[:12]:
            print(" | ".join(row[:18]))

    # Also print any rows anywhere containing likely maximum-temperature labels.
    print("\n===== ROWS MATCHING MAX/TEMP =====")
    found = 0
    for ti, table in enumerate(p.tables):
        for ri, row in enumerate(table):
            s = " | ".join(row)
            low = s.lower()
            if "max" in low or "temperature" in low or "temp" in low:
                print(f"T{ti} R{ri}: {s[:1000]}")
                found += 1
                if found >= 40:
                    break
        if found >= 40:
            break
    print(f"matching_rows_printed={found}")

    if not p.tables:
        print("RESULT: page returned but no HTML tables were present; data may be injected by JavaScript/API.")
        return 2
    print("RESULT: HTML tables found. Use the samples above to lock the recent-actual parser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
