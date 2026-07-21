"""面向公开金融信息研究的 Tavily 搜索封装。"""

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from tavily import TavilyClient

from ..utils.retry_helper import with_graceful_retry


@dataclass
class SearchResult:
    title: str
    url: str
    published_date: Optional[str]
    source: str
    content: str
    score: Optional[float] = None


@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult] = field(default_factory=list)


def _failed_response(*args, **kwargs) -> SearchResponse:
    return SearchResponse(query=kwargs.get("query", ""))


class TavilySearchAgency:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("TAVILY_API_KEY 不能为空")
        self.client = TavilyClient(api_key=api_key)

    @with_graceful_retry(default_factory=_failed_response, max_retries=2)
    def search(
        self,
        query: str,
        max_results: int = 7,
        search_depth: str = "basic",
        days: Optional[int] = None,
    ) -> SearchResponse:
        params = {
            "query": query,
            "topic": "general",
            "search_depth": search_depth,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        if days is not None:
            params["days"] = days
        raw = self.client.search(**params)
        results = []
        for item in raw.get("results", []):
            url = item.get("url", "")
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    published_date=item.get("published_date"),
                    source=urlparse(url).netloc.removeprefix("www."),
                    content=item.get("content", ""),
                    score=item.get("score"),
                )
            )
        return SearchResponse(query=raw.get("query", query), results=results)
