"""从 RSS/Atom 订阅源发现媒体候选。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .media_models import MediaCandidate
from .newsnow_provider import filter_media_candidates


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join("".join(self.parts).split())


def _plain_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(unescape(value or ""))
    return parser.text()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element, names: set[str]) -> str:
    for child in element:
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _entry_url(element) -> str:
    for child in element:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        if href and child.attrib.get("rel", "alternate") in {"", "alternate"}:
            return href
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def _normalize_date(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="seconds")


class RSSProvider:
    DEFAULT_HEADERS = {
        "User-Agent": "TrendRadar/2.0 RSS Reader (https://github.com/trendradar)",
        "Accept": (
            "application/feed+json, application/json, application/rss+xml, "
            "application/atom+xml, application/xml, text/xml, */*"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    def __init__(
        self,
        feeds: list[dict],
        timeout: float = 15.0,
        max_age_days: int = 3,
        max_content_bytes: int = 6_000_000,
        user_agent: str = "FinancialFactResearch/0.1",
    ) -> None:
        self.feeds = [item for item in feeds if item.get("enabled", True)]
        self.timeout = timeout
        self.max_age_days = max(0, int(max_age_days))
        self.max_content_bytes = max(1, int(max_content_bytes))
        self.user_agent = user_agent

    def fetch(self, url: str) -> bytes:
        headers = dict(self.DEFAULT_HEADERS)
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_content_bytes + 1)
                if len(raw) > self.max_content_bytes:
                    raise ValueError("RSS 响应超过允许的最大字节数")
                return raw
        except HTTPError as exc:
            raise ValueError(f"RSS 返回 HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError(f"无法访问 RSS：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ValueError(f"RSS 读取超时：{url}") from exc

    def parse(self, raw: bytes, feed: dict) -> list[MediaCandidate]:
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            raise ValueError("RSS 返回了无效 XML") from exc
        result = []
        for element in root.iter():
            if _local_name(element.tag) not in {"item", "entry"}:
                continue
            title = _child_text(element, {"title"})
            url = _entry_url(element)
            if not title or urlparse(url).scheme not in {"http", "https"}:
                continue
            published_at = _normalize_date(
                _child_text(element, {"pubdate", "published", "updated", "date"})
            )
            max_age = int(feed.get("max_age_days", self.max_age_days))
            if max_age > 0 and published_at:
                published = datetime.fromisoformat(published_at)
                if published < datetime.now(timezone.utc) - timedelta(days=max_age):
                    continue
            result.append(
                MediaCandidate(
                    title=_plain_text(title),
                    url=url,
                    source_name=str(feed.get("name") or feed.get("id") or "未知来源"),
                    published_at=published_at,
                    snippet=_plain_text(
                        _child_text(element, {"description", "summary", "content"})
                    ),
                    discovered_by="rss",
                    source_group=str(feed.get("source_group", "news_media")),
                )
            )
        return result

    def search(
        self,
        queries: list[str],
        limit: int = 20,
        progress=None,
    ) -> list[MediaCandidate]:
        candidates = []
        total = len(self.feeds)
        for index, feed in enumerate(self.feeds, 1):
            name = str(feed.get("name") or feed.get("id") or "未知源")
            if progress:
                progress(f"  [{index}/{total}] RSS：{name}")
            try:
                candidates.extend(self.parse(self.fetch(str(feed.get("url", ""))), feed))
            except ValueError as exc:
                if progress:
                    progress(f"  [{index}/{total}] RSS 失败：{name}（{exc}）")
                continue
        return filter_media_candidates(candidates, queries, limit=limit)
