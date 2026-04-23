#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_dashboard_php import (  # type: ignore
    DASHBOARD_HASH,
    DASHBOARD_PASSWORD,
    DASHBOARD_USER,
    _create_dashboard_db,
)


PHP_BIN = shutil.which("php")
BROWSER_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "dashboard_visual"


def _find_browser() -> Path:
    for candidate in BROWSER_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Headless browser not found. Chrome or Edge is required for dashboard visual smoke.")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _request(url: str, auth: tuple[str, str] | None = None) -> urllib.response.addinfourl:
    request = urllib.request.Request(url)
    if auth is not None:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    return urllib.request.urlopen(request, timeout=15)


def _wait_for_server(base_url: str) -> None:
    last_error: Exception | None = None
    for _ in range(60):
        try:
            with _request(base_url + "/api.php", auth=(DASHBOARD_USER, DASHBOARD_PASSWORD)):
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"Dashboard server did not start in time: {last_error}")


def _run_browser_capture(
    browser_path: Path,
    *,
    url: str,
    screenshot_path: Path,
    width: int,
    height: int,
) -> str:
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    if screenshot_path.exists():
        screenshot_path.unlink()

    screenshot_args = [
        str(browser_path),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=7000",
        f"--window-size={width},{height}",
        f"--screenshot={screenshot_path}",
        url,
    ]
    screenshot_run = subprocess.run(
        screenshot_args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if screenshot_run.returncode != 0:
        raise RuntimeError(
            "Browser screenshot capture failed\n"
            f"STDOUT:\n{screenshot_run.stdout}\nSTDERR:\n{screenshot_run.stderr}"
        )

    dom_args = [
        str(browser_path),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=7000",
        f"--window-size={width},{height}",
        "--dump-dom",
        url,
    ]
    dom_run = subprocess.run(
        dom_args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if dom_run.returncode != 0:
        raise RuntimeError(
            "Browser DOM capture failed\n"
            f"STDOUT:\n{dom_run.stdout}\nSTDERR:\n{dom_run.stderr}"
        )
    return dom_run.stdout


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        signature = handle.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError(f"Expected PNG file, got invalid signature: {path}")
        _length = handle.read(4)
        chunk = handle.read(4)
        if chunk != b"IHDR":
            raise RuntimeError(f"PNG missing IHDR header: {path}")
        width, height = struct.unpack(">II", handle.read(8))
        return width, height


def _assert_dom_contains(dom: str, required_snippets: list[str]) -> None:
    for snippet in required_snippets:
        if snippet not in dom:
            raise RuntimeError(f"Rendered DOM missing required snippet: {snippet}")


def _start_dashboard_server(*, db_path: Path, lane_mode: str) -> tuple[subprocess.Popen[bytes], str]:
    if PHP_BIN is None:
        raise RuntimeError("php is not available in PATH")

    port = _find_free_port()
    env = os.environ.copy()
    env.update(
        {
            "GHOST_TRADER_REPO_ROOT": str(REPO_ROOT),
            "GHOST_TRADER_DB_PATH": str(db_path),
            "DASHBOARD_USER": DASHBOARD_USER,
            "DASHBOARD_PASSWORD_HASH": DASHBOARD_HASH,
            "DASHBOARD_REFRESH_SECONDS": "2",
            "DASHBOARD_LOG_LINES": "5",
            "DASHBOARD_LANE_MODE": lane_mode,
            "DASHBOARD_API_CACHE_SECONDS": "0",
            "DASHBOARD_DISABLE_AUTH": "1",
        }
    )
    process = subprocess.Popen(
        [PHP_BIN, "-S", f"127.0.0.1:{port}", "-t", str(REPO_ROOT / "dashboard" / "public")],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process, f"http://127.0.0.1:{port}"


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    browser_path = _find_browser()
    artifact_dir = ARTIFACT_ROOT / time.strftime("%Y%m%d_%H%M%S")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "all_checks_passed": False,
        "browser": str(browser_path),
        "artifact_dir": str(artifact_dir),
        "pages": {},
    }

    with tempfile.TemporaryDirectory(prefix="ws-dashboard-visual-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        db_path = tmp_dir / "dashboard_visual.db"
        _create_dashboard_db(db_path)

        page_specs = [
            {
                "name": "landing",
                "lane_mode": "split",
                "required": ["WhaleSignal Operations", "Polymarket Copy Trade", "Binance Trading Operations", "Kontrol Merkezi"],
            },
            {
                "name": "polymarket",
                "lane_mode": "polymarket_research",
                "required": [
                    "Polymarket Copy Trade",
                    "Takipteki Balinalar",
                    "Portfolio Equity",
                    "Daily PnL",
                    "Bizim Copy Trade Ozeti",
                ],
            },
            {
                "name": "binance",
                "lane_mode": "binance_technical",
                "required": [
                    "Binance Trading Operations",
                    "Acik Pozisyonlar",
                    "Acik Emirler",
                    "Fresh PnL Ozeti",
                    "7g PnL Trend",
                    "Spot / Futures / Risk",
                ],
            },
        ]

        for spec in page_specs:
            process, base_url = _start_dashboard_server(db_path=db_path, lane_mode=spec["lane_mode"])
            try:
                _wait_for_server(base_url)
                auth_url = base_url + "/index.php"
                page_report: dict[str, Any] = {
                    "lane_mode": spec["lane_mode"],
                    "url": auth_url,
                    "desktop": {},
                    "mobile": {},
                }
                for variant, width, height in [
                    ("desktop", 1440, 2200),
                    ("mobile", 430, 2200),
                ]:
                    screenshot_path = artifact_dir / f"{spec['name']}_{variant}.png"
                    dom = _run_browser_capture(
                        browser_path,
                        url=auth_url,
                        screenshot_path=screenshot_path,
                        width=width,
                        height=height,
                    )
                    _assert_dom_contains(dom, spec["required"])
                    if not screenshot_path.is_file() or screenshot_path.stat().st_size <= 2048:
                        raise RuntimeError(f"Screenshot artifact missing or too small: {screenshot_path}")
                    img_width, img_height = _png_dimensions(screenshot_path)
                    page_report[variant] = {
                        "screenshot": str(screenshot_path),
                        "bytes": screenshot_path.stat().st_size,
                        "width": img_width,
                        "height": img_height,
                    }
                report["pages"][spec["name"]] = page_report
            finally:
                _stop_process(process)

    report["all_checks_passed"] = True
    print("DASHBOARD_VISUAL_SMOKE_SUMMARY")
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
