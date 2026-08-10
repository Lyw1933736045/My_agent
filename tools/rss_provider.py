"""从 RSS/Atom 订阅源稳定地发现媒体候选。"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

from .media_models import MediaCandidate, ProviderDiagnostics


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
            "application/rss+xml, application/atom+xml, application/xml, "
            "text/xml, */*"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    def __init__(
        self,
        feeds: list[dict],
        timeout: float = 15.0,
        max_age_days: int = 3,
        max_content_bytes: int = 6_000_000,
        default_max_items: int = 0,
        request_interval_min: float = 0.5,
        request_interval_max: float = 1.0,
        max_retries: int = 1,
        retry_wait_min: float = 2.0,
        retry_wait_max: float = 3.0,
        session: requests.Session | None = None,
    ) -> None:
        self.feeds = [item for item in feeds if item.get("enabled", True)]
        self.timeout = max(1.0, float(timeout))
        self.max_age_days = max(0, int(max_age_days))
        self.max_content_bytes = max(1, int(max_content_bytes))
        self.default_max_items = max(0, int(default_max_items))
        self.request_interval_min = max(0.0, float(request_interval_min))
        self.request_interval_max = max(
            self.request_interval_min, float(request_interval_max)
        )
        self.max_retries = min(1, max(0, int(max_retries)))
        self.retry_wait_min = max(0.0, float(retry_wait_min))
        self.retry_wait_max = max(self.retry_wait_min, float(retry_wait_max))
        self.session = session or requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        self.diagnostics = ProviderDiagnostics()

    def fetch(self, url: str) -> bytes:
        try:
            with self.session.get(url, timeout=self.timeout, stream=True) as response:
                response.raise_for_status()
                chunks = []
                size = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_content_bytes:
                        raise ValueError("响应超过允许的最大字节数")
                    chunks.append(chunk)
                return b"".join(chunks)
        except requests.Timeout as exc:
            raise ValueError(f"请求超时（{self.timeout:g}s）") from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise ValueError(f"HTTP {status}") from exc
        except requests.RequestException as exc:
            raise ValueError(f"连接失败：{exc}") from exc

    def _fetch_with_retry(self, url: str, name: str, progress=None) -> bytes | None:
        for attempt in range(self.max_retries + 1):
            try:
                return self.fetch(url)
            except ValueError as exc:
                if attempt < self.max_retries:
                    wait = random.uniform(self.retry_wait_min, self.retry_wait_max)
                    if progress:
                        progress(f"    RSS 请求失败：{name}（{exc}），{wait:.1f}s 后重试")
                    time.sleep(wait)
                else:
                    self.diagnostics.failed_sources[name] = str(exc)
                    if progress:
                        progress(f"    RSS 最终失败：{name}（{exc}）")
        return None

    def parse(self, raw: bytes, feed: dict) -> list[MediaCandidate]:
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            raise ValueError("返回了无效 XML") from exc
        result = []
        feed_max_age = feed.get("max_age_days")
        effective_max_age = self.max_age_days if feed_max_age is None else max(
            0, int(feed_max_age)
        )
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=effective_max_age)
            if effective_max_age > 0
            else None
        )
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
            if cutoff is not None and published_at:
                if datetime.fromisoformat(published_at) < cutoff:
                    continue
            guid = _child_text(element, {"guid", "id"}) or url
            result.append(MediaCandidate(
                title=_plain_text(title),
                url=url,
                source_name=str(feed.get("name") or feed.get("id") or "未知来源"),
                published_at=published_at,
                snippet=_plain_text(
                    _child_text(element, {"description", "summary", "content"})
                ),
                discovered_by=("rss",),
                source_group=str(feed.get("source_group", "news_media")),
                guid=guid,
                max_age_days=effective_max_age,
            ))
        feed_max_items = feed.get("max_items")
        max_items = self.default_max_items if feed_max_items is None else max(
            0, int(feed_max_items)
        )
        return result[:max_items] if max_items else result

    def search(self, queries: list[str], limit: int = 20, progress=None) -> list[MediaCandidate]:
        self.diagnostics = ProviderDiagnostics()
        candidates = []
        total = len(self.feeds)
        for index, feed in enumerate(self.feeds, 1):
            name = str(feed.get("name") or feed.get("id") or "未知源")
            if progress:
                progress(f"  [{index}/{total}] RSS：{name}")
            raw = self._fetch_with_retry(str(feed.get("url", "")), name, progress)
            if raw is not None:
                try:
                    items = self.parse(raw, feed)
                    candidates.extend(items)
                    self.diagnostics.successful_sources[name] = f"{len(items)} 条"
                    if progress:
                        progress(f"    RSS 成功：{name}（{len(items)} 条）")
                except ValueError as exc:
                    self.diagnostics.failed_sources[name] = str(exc)
                    if progress:
                        progress(f"    RSS 解析失败：{name}（{exc}）")
            if index < total and self.request_interval_max:
                time.sleep(random.uniform(
                    self.request_interval_min, self.request_interval_max
                ))
        return candidates
