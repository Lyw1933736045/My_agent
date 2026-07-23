"""国务院静态政策栏目页的最小候选发现器。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .storage import Database
from .official_sources import OfficialSourcesRegistry, UnsafeOfficialUrlError


STATE_COUNCIL_LIST_URL = (
    "https://www.gov.cn/zhengce/zhengceku/gwywj/home.htm"
)
_DATE_PATTERN = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
_DETAIL_PATH_PATTERN = re.compile(
    r"^/zhengce/(?:zhengceku/|content/)?20\d{4}/content_[A-Za-z0-9_-]+\.htm$"
)


@dataclass(frozen=True)
class DiscoveredDocument:
    source_id: str
    title: str
    url: str
    published_at: str | None


class _StateCouncilListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str, str | None]] = []
        self._in_li = False
        self._in_anchor = False
        self._href: str | None = None
        self._anchor_parts: list[str] = []
        self._li_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_map = dict(attrs)
        if tag.lower() == "li":
            self._in_li = True
            self._href = None
            self._anchor_parts = []
            self._li_parts = []
        elif self._in_li and tag.lower() == "a" and self._href is None:
            self._href = attrs_map.get("href")
            self._in_anchor = self._href is not None

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self._in_anchor = False
        elif tag.lower() == "li" and self._in_li:
            title = " ".join("".join(self._anchor_parts).split())
            combined = " ".join("".join(self._li_parts).split())
            date_match = _DATE_PATTERN.search(combined)
            if self._href and title:
                self.items.append(
                    (title, self._href, date_match.group(0) if date_match else None)
                )
            self._in_li = False

    def handle_data(self, data: str) -> None:
        if not self._in_li:
            return
        self._li_parts.append(data)
        if self._in_anchor:
            self._anchor_parts.append(data)


class StateCouncilDiscovery:
    def __init__(
        self,
        database: Database,
        registry: OfficialSourcesRegistry | None = None,
        list_url: str = STATE_COUNCIL_LIST_URL,
        timeout: float = 30.0,
        user_agent: str = "FinancialFactResearch/0.1",
    ) -> None:
        self.database = database
        self.registry = registry or OfficialSourcesRegistry()
        self.list_url = list_url
        self.timeout = timeout
        self.user_agent = user_agent

    def fetch_html(self) -> str:
        request = Request(
            self.list_url,
            headers={"User-Agent": self.user_agent, "Accept": "text/html"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content_type = response.headers.get_content_type()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise ValueError(f"国务院栏目页返回非 HTML 内容：{content_type}")
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except HTTPError as exc:
            raise ValueError(f"国务院栏目页返回 HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError(f"无法读取国务院栏目页：{exc.reason}") from exc

    def parse(self, html: str, limit: int = 20) -> list[DiscoveredDocument]:
        if limit < 1:
            raise ValueError("limit 必须是正整数")
        parser = _StateCouncilListParser()
        parser.feed(html)
        discovered = []
        seen_urls = set()
        for title, href, published_at in parser.items:
            url = urljoin(self.list_url, href)
            if (
                url in seen_urls
                or published_at is None
                or not _DETAIL_PATH_PATTERN.match(urlparse(url).path)
            ):
                continue
            try:
                verification = self.registry.match_url(url)
            except UnsafeOfficialUrlError:
                continue
            if (
                verification.source_id != "state_council"
                or verification.verification_status != "verified"
            ):
                continue
            seen_urls.add(url)
            discovered.append(
                DiscoveredDocument(
                    source_id="state_council",
                    title=title,
                    url=url,
                    published_at=published_at,
                )
            )
            if len(discovered) >= limit:
                break
        return discovered

    def discover(self, limit: int = 20) -> tuple[int, int]:
        documents = self.parse(self.fetch_html(), limit=limit)
        inserted = 0
        for document in documents:
            _, was_inserted = self.database.upsert_document(
                source_id=document.source_id,
                title=document.title,
                url=document.url,
                published_at=document.published_at,
            )
            inserted += int(was_inserted)
        return len(documents), inserted
