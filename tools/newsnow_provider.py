"""从 NewsNow 热榜稳定地发现媒体候选。"""

from __future__ import annotations

import json
import random
import time
from http.client import HTTPException, RemoteDisconnected
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .media_models import MediaCandidate, ProviderDiagnostics


class NewsNowProvider:
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }

    def __init__(
        self,
        api_url: str,
        sources: list[dict],
        timeout: float = 30.0,
        max_retries: int = 1,
        retry_wait_min: float = 2.0,
        retry_wait_max: float = 3.0,
        request_interval: float = 0.5,
    ) -> None:
        self.api_url = api_url.rstrip("?")
        self.sources = [item for item in sources if item.get("enabled", True)]
        self.timeout = max(1.0, float(timeout))
        self.max_retries = max(0, int(max_retries))
        self.retry_wait_min = max(0.0, float(retry_wait_min))
        self.retry_wait_max = max(self.retry_wait_min, float(retry_wait_max))
        self.request_interval = max(0.0, float(request_interval))
        self.diagnostics = ProviderDiagnostics()

    def fetch_json(self, source_id: str) -> dict[str, Any]:
        url = self.api_url + "?" + urlencode({"id": source_id}) + "&latest"
        request = Request(url, headers=dict(self.DEFAULT_HEADERS))
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode(
                    response.headers.get_content_charset() or "utf-8",
                    errors="replace",
                )
        except HTTPError as exc:
            raise ValueError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError(f"连接失败：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ValueError(f"读取超时（{self.timeout:g}s）") from exc
        except RemoteDisconnected as exc:
            raise ValueError("远端服务器提前关闭连接") from exc
        except (HTTPException, OSError) as exc:
            raise ValueError(f"连接异常：{exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("返回了无效 JSON") from exc
        status = payload.get("status") if isinstance(payload, dict) else None
        if status not in {"success", "cache"}:
            raise ValueError(f"返回状态异常：{status or 'unknown'}")
        return payload

    @staticmethod
    def _safe_url(url: str, expected_domain: str) -> bool:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        expected = expected_domain.lower().strip().rstrip(".")
        return (
            parsed.scheme == "https"
            and bool(hostname)
            and (not expected or hostname == expected or hostname.endswith("." + expected))
        )

    def _fetch_with_retry(self, source_id: str, name: str, progress=None) -> dict | None:
        for attempt in range(self.max_retries + 1):
            try:
                return self.fetch_json(source_id)
            except ValueError as exc:
                if attempt < self.max_retries:
                    wait = random.uniform(self.retry_wait_min, self.retry_wait_max)
                    if progress:
                        progress(
                            f"    NewsNow 失败：{name}（{exc}），{wait:.1f}s 后重试"
                        )
                    time.sleep(wait)
                else:
                    self.diagnostics.failed_sources[name] = str(exc)
                    if progress:
                        progress(f"    NewsNow 最终失败：{name}（{exc}）")
        return None

    def search(self, queries: list[str], limit: int = 20, progress=None) -> list[MediaCandidate]:
        if limit < 1:
            raise ValueError("limit 必须是正整数")
        self.diagnostics = ProviderDiagnostics(status_counts={"success": 0, "cache": 0})
        candidates = []
        total = len(self.sources)
        for index, source in enumerate(self.sources, 1):
            name = str(source.get("name") or source.get("id") or "未知来源")
            if progress:
                progress(f"  [{index}/{total}] NewsNow：{name}")
            payload = self._fetch_with_retry(str(source.get("id", "")), name, progress)
            if payload is not None:
                status = str(payload["status"])
                self.diagnostics.status_counts[status] += 1
                if progress:
                    progress(f"    NewsNow 成功：{name}（{status}）")
                items = payload.get("items")
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        title = item.get("title")
                        url = item.get("url") or item.get("mobileUrl") or ""
                        if (
                            not isinstance(title, str)
                            or not title.strip()
                            or not isinstance(url, str)
                            or not self._safe_url(
                                url, str(source.get("expected_domain", ""))
                            )
                        ):
                            continue
                        candidates.append(MediaCandidate(
                            title=" ".join(title.split()),
                            url=url.strip(),
                            source_name=name,
                            published_at=None,
                            discovered_by=("newsnow",),
                            source_group=str(source.get("source_group", "news_media")),
                        ))
            if index < total and self.request_interval:
                time.sleep(self.request_interval)
        return candidates
