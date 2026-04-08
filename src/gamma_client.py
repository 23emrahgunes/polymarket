from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


logger = logging.getLogger(__name__)


@dataclass
class GammaFetchResult:
    data: list[Dict[str, Any]]
    reason: Optional[str] = None
    status_code: Optional[int] = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.reason is None


class GammaApiClient:
    def __init__(
        self,
        gamma_api_base: str = "https://gamma-api.polymarket.com",
        data_api_base: str = "https://data-api.polymarket.com",
        connect_timeout_sec: float = 3.0,
        read_timeout_sec: float = 6.0,
        inspection_concurrency: int = 8,
        max_retries: int = 1,
        log_throttle_seconds: float = 60.0,
    ):
        self.gamma_api_base = gamma_api_base.rstrip("/")
        self.data_api_base = data_api_base.rstrip("/")
        self.connect_timeout_sec = connect_timeout_sec
        self.read_timeout_sec = read_timeout_sec
        self.max_retries = max_retries
        self.log_throttle_seconds = log_throttle_seconds
        self._wallet_semaphore = asyncio.Semaphore(max(inspection_concurrency, 1))
        self._last_log_by_key: Dict[str, float] = {}

    async def fetch_leaderboard(self, limit: int = 20) -> GammaFetchResult:
        return await self._request_json(
            path="/v1/leaderboard",
            params={"limit": limit},
            timeout_reason="leaderboard_unavailable",
            http_reason="leaderboard_unavailable",
            invalid_reason="leaderboard_unavailable",
            base_url=self.data_api_base,
        )

    async def fetch_global_activity(self, limit: int = 50) -> GammaFetchResult:
        return await self._request_json(
            path="/trades",
            params={"limit": limit},
            timeout_reason="activity_feed_unavailable",
            http_reason="activity_feed_unavailable",
            invalid_reason="activity_feed_unavailable",
            base_url=self.data_api_base,
        )

    async def fetch_wallet_activity(self, address: str, limit: int = 5) -> GammaFetchResult:
        async with self._wallet_semaphore:
            return await self._request_json(
                path="/activity",
                params={"user": address, "limit": limit},
                timeout_reason="wallet_activity_timeout",
                http_reason="wallet_activity_http_error",
                invalid_reason="wallet_activity_http_error",
                log_suffix=address[:10],
                base_url=self.data_api_base,
            )

    async def _request_json(
        self,
        path: str,
        params: Optional[Dict[str, Any]],
        timeout_reason: str,
        http_reason: str,
        invalid_reason: str,
        log_suffix: str = "",
        base_url: Optional[str] = None,
    ) -> GammaFetchResult:
        url = f"{(base_url or self.gamma_api_base).rstrip('/')}{path}"
        last_reason = invalid_reason
        last_status_code: Optional[int] = None
        last_timed_out = False

        for attempt in range(self.max_retries + 1):
            try:
                response = await asyncio.to_thread(
                    requests.get,
                    url,
                    params=params,
                    timeout=(self.connect_timeout_sec, self.read_timeout_sec),
                )
                last_status_code = response.status_code
                if response.status_code != 200:
                    last_reason = http_reason
                    self._log_failure(http_reason, {"path": path, "status_code": response.status_code, "detail": log_suffix})
                else:
                    payload = response.json()
                    if isinstance(payload, list):
                        return GammaFetchResult(data=payload, status_code=response.status_code)
                    last_reason = invalid_reason
                    self._log_failure(invalid_reason, {"path": path, "detail": "non_list_payload", "scope": log_suffix})
            except (requests.ConnectTimeout, requests.ReadTimeout, requests.Timeout):
                last_reason = timeout_reason
                last_timed_out = True
                self._log_failure(timeout_reason, {"path": path, "detail": log_suffix or "timeout"})
            except Exception as exc:
                last_reason = http_reason
                self._log_failure(http_reason, {"path": path, "detail": f"{log_suffix or 'request'}:{exc}"})

            if attempt < self.max_retries:
                await asyncio.sleep(0.25 * (attempt + 1))

        return GammaFetchResult(
            data=[],
            reason=last_reason,
            status_code=last_status_code,
            timed_out=last_timed_out,
        )

    def _log_failure(self, reason: str, inputs: Dict[str, Any]) -> None:
        key = f"{reason}:{inputs.get('path')}:{inputs.get('detail')}:{inputs.get('status_code')}"
        now = time.time()
        previous = self._last_log_by_key.get(key, 0.0)
        if now - previous < self.log_throttle_seconds:
            return
        self._last_log_by_key[key] = now
        logger.info("[REJECT] source=gamma_client category=UNKNOWN market=%s reasons=%s inputs=%s", inputs.get("path", "gamma"), reason, inputs)
