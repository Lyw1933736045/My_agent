"""官方网页正文读取器。

该模块只读取用户提供的 URL，不接受搜索摘要作为正文来源。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class _ReadableHTMLParser(HTMLParser):
    """提取 HTML 中的可见文本，并忽略脚本、样式等非正文内容。"""

    _ignored_tags = {"script", "style", "noscript", "svg", "canvas", "template"}
    _block_tags = {
        "article", "aside", "blockquote", "br", "div", "footer", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "li", "main", "nav", "p", "section",
        "table", "td", "th", "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._ignored_tags:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in self._block_tags:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in self._block_tags:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def text(self) -> str:
        lines = []
        for raw_line in "".join(self._parts).splitlines():
            line = " ".join(raw_line.split())
            if line:
                lines.append(line)
        return "\n".join(lines)


@dataclass
class WebReadResult:
    requested_url: str
    final_url: str
    fetched_at: str
    content_type: str
    content: str


class WebReader:
    """读取 HTTP(S) HTML 或纯文本页面。"""

    _supported_types = ("text/html", "application/xhtml+xml", "text/plain")

    def __init__(
        self,
        timeout: float = 30.0,
        max_content_bytes: int = 5_000_000,
        max_text_length: int = 100_000,
        user_agent: str = "FinancialFactResearch/0.1",
    ) -> None:
        self.timeout = timeout
        self.max_content_bytes = max_content_bytes
        self.max_text_length = max_text_length
        self.user_agent = user_agent

    @staticmethod
    def validate_url(url: str) -> str:
        normalized = (url or "").strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("official_url 必须是有效的 HTTP(S) URL")
        return normalized

    def read(self, url: str) -> WebReadResult:
        requested_url = self.validate_url(url)
        request = Request(
            requested_url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                content_type = (
                    response.headers.get_content_type() or "application/octet-stream"
                ).lower()
                if content_type not in self._supported_types:
                    raise ValueError(f"暂不支持读取内容类型：{content_type}")

                raw = response.read(self.max_content_bytes + 1)
                if len(raw) > self.max_content_bytes:
                    raise ValueError("官方网页响应超过允许的最大字节数")

                charset: Optional[str] = response.headers.get_content_charset()
                decoded = raw.decode(charset or "utf-8", errors="replace")
        except HTTPError as exc:
            raise ValueError(f"网页返回 HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError(f"无法读取网页：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ValueError("网页读取超时") from exc

        if content_type in {"text/html", "application/xhtml+xml"}:
            parser = _ReadableHTMLParser()
            parser.feed(decoded)
            content = parser.text()
        else:
            content = "\n".join(
                line for line in (" ".join(item.split()) for item in decoded.splitlines()) if line
            )

        content = content[: self.max_text_length].strip()
        if not content:
            raise ValueError("官方网页未提取到可用正文")

        return WebReadResult(
            requested_url=requested_url,
            final_url=final_url,
            fetched_at=_now_iso(),
            content_type=content_type,
            content=content,
        )
