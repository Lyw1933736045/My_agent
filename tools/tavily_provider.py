"""用 Tavily 搜索发现媒体候选 URL（不把搜索摘要当作事实）。"""

from __future__ import annotations

from urllib.parse import urlparse

from .media_models import MediaCandidate
from .search import TavilySearchAgency


_SOCIAL_DOMAIN_SUFFIXES = (
    "weibo.com",
    "zhihu.com",
    "bilibili.com",
    "xueqiu.com",
    "douyin.com",
    "xiaohongshu.com",
)


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _source_group_for_url(url: str) -> str:
    host = _hostname(url)
    for suffix in _SOCIAL_DOMAIN_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return "social_media"
    return "news_media"


def _source_name_for_url(url: str) -> str:
    host = _hostname(url)
    return host.removeprefix("www.") or "Tavily"


class TavilyMediaProvider:
    """把 Tavily 结果转成 MediaCandidate，仅用于 URL 发现与标题匹配。"""

    def __init__(
        self,
        api_key: str,
        *,
        max_results_per_query: int = 5,
        search_depth: str = "basic",
        days: int | None = None,
    ) -> None:
        self.agency = TavilySearchAgency(api_key)
        self.max_results_per_query = max(1, int(max_results_per_query))
        depth = (search_depth or "basic").strip().lower()
        self.search_depth = depth if depth in {"basic", "advanced"} else "basic"
        self.days = days

    def search(
        self,
        queries: list[str],
        limit: int = 20,
        progress=None,
    ) -> list[MediaCandidate]:
        if limit < 1:
            raise ValueError("limit 必须是正整数")
        candidates: list[MediaCandidate] = []
        total = len(queries)
        for index, query in enumerate(queries, 1):
            if progress:
                progress(f"  [{index}/{total}] Tavily：{query}")
            response = self.agency.search(
                query=query,
                max_results=self.max_results_per_query,
                search_depth=self.search_depth,
                days=self.days,
            )
            for item in response.results:
                title = " ".join((item.title or "").split())
                url = (item.url or "").strip()
                parsed = urlparse(url)
                if not title or parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    continue
                candidates.append(
                    MediaCandidate(
                        title=title,
                        url=url,
                        source_name=_source_name_for_url(url),
                        published_at=item.published_date,
                        # snippet 只用于本地关键词打分，简报仍会重新抓取正文。
                        snippet=" ".join((item.content or "").split())[:500],
                        discovered_by=("tavily",),
                        source_group=_source_group_for_url(url),
                        query=query,
                    )
                )
        return candidates
