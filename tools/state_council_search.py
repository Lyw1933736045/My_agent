"""中国政府网官方搜索的最小 URL 发现工具。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ..utils.official_sources import OfficialSourcesRegistry, UnsafeOfficialUrlError


OFFICIAL_SEARCH_API = "https://sousuo.www.gov.cn/search-gov/data"
OFFICIAL_SEARCH_REFERER = "https://sousuo.www.gov.cn/sousuo/search.shtml"
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_DOT_DATE_PATTERN = re.compile(r"\b(20\d{2})\.(\d{1,2})\.(\d{1,2})\b")
_DASH_DATE_PATTERN = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_POLICY_DETAIL_PATH = re.compile(
    r"^/zhengce/(?:zhengceku/|content/)?20\d{4}/content_[A-Za-z0-9_-]+\.htm$"
)
_TRACKING_QUERY_KEYS = {"from", "source", "spm", "utm_source", "utm_medium", "utm_campaign"}
# 公文优先，再部门文件；公报等路径通常不符合详情页规则，排在最后。
_CAT_PRIORITY = {"gongwen": 0, "bumenfile": 1}


@dataclass(frozen=True)
class OfficialSearchCandidate:
    title: str
    url: str
    published_at: str | None
    source_id: str
    source_name: str


def _strip_html(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(_HTML_TAG_PATTERN.sub("", unescape(value)).split())


def _normalize_date(value: Any) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        from datetime import datetime, timezone

        # 政府网 pubtime / ptime 通常是毫秒时间戳。
        seconds = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    for pattern in (_DASH_DATE_PATTERN, _DOT_DATE_PATTERN):
        match = pattern.search(text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def _canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    if scheme == "http":
        scheme = "https"
    filtered_query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_QUERY_KEYS
        ]
    )
    return urlunsplit(
        (scheme, parts.netloc.lower(), parts.path, filtered_query, "")
    )


def _iter_result_items(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    search_vo = payload.get("searchVO")
    if not isinstance(search_vo, dict):
        search_vo = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if isinstance(search_vo, dict) and isinstance(search_vo.get("searchVO"), dict):
            search_vo = search_vo["searchVO"]

    items: list[tuple[str, dict[str, Any]]] = []
    cat_map = search_vo.get("catMap") if isinstance(search_vo, dict) else None
    if isinstance(cat_map, dict):
        ordered_cats = sorted(
            cat_map.items(),
            key=lambda item: (_CAT_PRIORITY.get(item[0], 99), item[0]),
        )
        for cat_key, cat_value in ordered_cats:
            if not isinstance(cat_value, dict):
                continue
            list_vo = cat_value.get("listVO")
            if not isinstance(list_vo, list):
                continue
            for entry in list_vo:
                if isinstance(entry, dict):
                    items.append((cat_key, entry))

    list_vo = search_vo.get("listVO") if isinstance(search_vo, dict) else None
    if isinstance(list_vo, list):
        for entry in list_vo:
            if isinstance(entry, dict):
                items.append(("listVO", entry))

    results = payload.get("results")
    if isinstance(results, list):
        for entry in results:
            if isinstance(entry, dict):
                items.append(("results", entry))
    return items


class StateCouncilSearch:
    def __init__(
        self,
        registry: OfficialSourcesRegistry | None = None,
        timeout: float = 30.0,
        user_agent: str = "FinancialFactResearch/0.1",
    ) -> None:
        self.registry = registry or OfficialSourcesRegistry()
        self.timeout = timeout
        self.user_agent = user_agent

    def build_search_url(self, query: str, *, page_size: int = 50) -> str:
        size = max(1, min(int(page_size), 100))
        return OFFICIAL_SEARCH_API + "?" + urlencode(
            {
                "t": "zhengcelibrary_gw_bm_gb",
                "q": query,
                "searchfield": "title:content:summary",
                "sort": "score",
                "sortType": "1",
                "p": "1",
                "n": str(size),
            }
        )

    def fetch_json(self, query: str, *, page_size: int = 50) -> dict[str, Any]:
        search_url = self.build_search_url(query, page_size=page_size)
        request = Request(
            search_url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Referer": OFFICIAL_SEARCH_REFERER,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode(
                    response.headers.get_content_charset() or "utf-8",
                    errors="replace",
                )
        except HTTPError as exc:
            raise ValueError(f"国务院搜索返回 HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError(f"无法访问国务院搜索：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ValueError("国务院搜索读取超时") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("国务院搜索返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("国务院搜索返回了非对象 JSON")
        code = payload.get("code")
        if code not in (None, 200, "200"):
            raise ValueError(f"国务院搜索失败：{payload.get('msg') or code}")
        return payload

    def parse(self, payload: dict[str, Any]) -> list[OfficialSearchCandidate]:
        candidates = []
        seen_urls = set()
        for _, entry in _iter_result_items(payload):
            title = _strip_html(entry.get("title"))
            raw_url = entry.get("url") or entry.get("link")
            if not title or not isinstance(raw_url, str) or not raw_url.strip():
                continue

            url = _canonicalize_url(raw_url)
            if url in seen_urls or urlsplit(url).path.lower().endswith(".pdf"):
                continue
            if not _POLICY_DETAIL_PATH.match(urlsplit(url).path):
                continue
            try:
                verification = self.registry.match_url(url)
            except UnsafeOfficialUrlError:
                continue
            if (
                verification.source_id != "state_council"
                or verification.verification_status != "verified"
                or self.registry.is_excluded_title(title)
            ):
                continue

            published_at = (
                _normalize_date(entry.get("pubtimeStr"))
                or _normalize_date(entry.get("ptime"))
                or _normalize_date(entry.get("pubtime"))
                or _normalize_date(entry.get("publishtime"))
            )
            seen_urls.add(url)
            candidates.append(
                OfficialSearchCandidate(
                    title=title,
                    url=url,
                    published_at=published_at,
                    source_id="state_council",
                    source_name=verification.source_name or "国务院政策文件库",
                )
            )

        return sorted(
            candidates,
            key=lambda item: not self.registry.has_accepted_document_keyword(item.title),
        )

    def search(self, queries: list[str], limit: int = 10) -> list[OfficialSearchCandidate]:
        if limit < 1:
            raise ValueError("limit 必须是正整数")
        page_size = max(limit, 20)
        merged: dict[str, OfficialSearchCandidate] = {}
        for query in queries:
            payload = self.fetch_json(query, page_size=page_size)
            for candidate in self.parse(payload):
                merged.setdefault(candidate.url, candidate)
        return list(merged.values())[:limit]
