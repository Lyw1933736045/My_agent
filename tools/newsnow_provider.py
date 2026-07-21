"""从 NewsNow 热榜发现媒体候选。"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .media_models import MediaCandidate


class NewsNowProvider:
    # 与 TrendRadar 保持一致，公共 NewsNow 实例会拒绝过于简单的爬虫请求头。
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }

    def __init__(
        self,
        api_url: str,
        sources: list[dict],
        timeout: float = 10.0,
        user_agent: str | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("?")
        self.sources = [item for item in sources if item.get("enabled", True)]
        self.timeout = timeout
        self.user_agent = user_agent

    def fetch_json(self, source_id: str) -> dict[str, Any]:
        url = self.api_url + "?" + urlencode({"id": source_id}) + "&latest"
        headers = dict(self.DEFAULT_HEADERS)
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode(
                    response.headers.get_content_charset() or "utf-8",
                    errors="replace",
                )
        except HTTPError as exc:
            raise ValueError(f"NewsNow 返回 HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError(f"无法访问 NewsNow：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ValueError(f"NewsNow 读取超时（{source_id}）") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("NewsNow 返回了无效 JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") not in {"success", "cache"}:
            raise ValueError("NewsNow 返回状态异常")
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

    def search(self, queries: list[str], limit: int = 20) -> list[MediaCandidate]:
        if limit < 1:
            raise ValueError("limit 必须是正整数")
        candidates = []
        for source in self.sources:
            try:
                payload = self.fetch_json(str(source.get("id", "")))
            except ValueError:
                # 单个平台失败不影响其他媒体来源。
                continue
            items = payload.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = item.get("title")
                if not isinstance(title, str) or not title.strip():
                    continue
                url = item.get("url") or item.get("mobileUrl") or ""
                if not isinstance(url, str) or not self._safe_url(
                    url, str(source.get("expected_domain", ""))
                ):
                    continue
                candidates.append(
                    MediaCandidate(
                        title=" ".join(title.split()),
                        url=url.strip(),
                        source_name=str(source.get("name") or source.get("id") or "未知来源"),
                        published_at=None,
                        source_group=str(source.get("source_group", "news_media")),
                    )
                )
        return filter_media_candidates(candidates, queries, limit=limit)


def _query_terms(query: str) -> list[str]:
    return [term.lower() for term in query.split() if term.strip()]


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    return parsed._replace(fragment="").geturl()


def filter_media_candidates(
    candidates: list[MediaCandidate],
    queries: list[str],
    *,
    limit: int = 20,
    max_per_source: int = 3,
) -> list[MediaCandidate]:
    """按标题和摘要匹配查询词，并保证来源不过度集中。"""
    scored = []
    for index, candidate in enumerate(candidates):
        text = f"{candidate.title} {candidate.snippet}".lower()
        best_score = 0
        for query in queries:
            normalized = " ".join(query.lower().split())
            terms = _query_terms(query)
            score = (10 if normalized and normalized in text else 0) + sum(
                3 for term in terms if term in text
            )
            best_score = max(best_score, score)
        if best_score:
            scored.append((best_score, index, candidate))

    result = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    source_counts: dict[str, int] = {}
    for _, _, candidate in sorted(scored, key=lambda item: (-item[0], item[1])):
        url_key = _canonical_url(candidate.url)
        title_key = "".join(candidate.title.lower().split())
        if url_key in seen_urls or title_key in seen_titles:
            continue
        if source_counts.get(candidate.source_name, 0) >= max_per_source:
            continue
        result.append(candidate)
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        source_counts[candidate.source_name] = source_counts.get(candidate.source_name, 0) + 1
        if len(result) == limit:
            break
    return result
