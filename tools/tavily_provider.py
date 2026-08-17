"""用 Tavily 搜索发现媒体候选 URL（不把搜索摘要当作事实）。"""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlparse

from .media_models import MediaCandidate, ProviderDiagnostics
from .search import TavilySearchAgency
from ..utils.dedup import canonical_url


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
        search_rounds: int = 2,
        max_results_per_query: int = 5,
        targeted_search_enabled: bool = True,
        targeted_max_results: int = 10,
        trusted_media_domains: list[str] | None = None,
        search_depth: str = "basic",
        days: int | None = None,
    ) -> None:
        self.agency = TavilySearchAgency(api_key)
        self.search_rounds = max(1, int(search_rounds))
        self.max_results_per_query = max(1, int(max_results_per_query))
        self.targeted_search_enabled = bool(targeted_search_enabled)
        self.targeted_max_results = max(1, int(targeted_max_results))
        self.trusted_media_domains = list(trusted_media_domains or [])
        depth = (search_depth or "basic").strip().lower()
        self.search_depth = depth if depth in {"basic", "advanced"} else "basic"
        self.days = days
        self.diagnostics = ProviderDiagnostics()
        self.round_stats: list[dict[str, int | str]] = []

    def search(
        self,
        queries: list[str],
        limit: int = 20,
        progress=None,
    ) -> list[MediaCandidate]:
        if limit < 1:
            raise ValueError("limit 必须是正整数")
        self.diagnostics = ProviderDiagnostics()
        self.round_stats = []
        candidates: list[MediaCandidate] = []
        seen_urls: set[str] = set()
        candidate_indexes: dict[str, int] = {}
        total = len(queries)
        for index, query in enumerate(queries, 1):
            label = f"检索词：{query}"
            for round_index in range(1, self.search_rounds + 1):
                round_label = f"{label}（第 {round_index}/{self.search_rounds} 轮）"
                if progress:
                    progress(
                        f"  [{index}/{total}] Tavily 第 {round_index}/{self.search_rounds} 轮：{query}"
                    )
                returned_count = 0
                round_urls: set[str] = set()
                searches = []
                if self.targeted_search_enabled and self.trusted_media_domains:
                    searches.append(("tavily_targeted", self.targeted_max_results, self.trusted_media_domains))
                searches.append(("tavily_general", self.max_results_per_query, None))
                for discovered_by, max_results, include_domains in searches:
                    try:
                        response = self.agency.search(
                            query=query,
                            max_results=max_results,
                            search_depth=self.search_depth,
                            days=self.days,
                            include_domains=include_domains,
                        )
                    except Exception as exc:
                        self.diagnostics.failed_sources[f"{round_label} ({discovered_by})"] = str(exc)
                        if progress:
                            progress(f"    Tavily 失败：{query}（{discovered_by}：{exc}）")
                        continue
                    for rank, item in enumerate(response.results, 1):
                        title = " ".join((item.title or "").split())
                        url = (item.url or "").strip()
                        parsed = urlparse(url)
                        if not title or parsed.scheme not in {"http", "https"} or not parsed.hostname:
                            continue
                        returned_count += 1
                        canonical = canonical_url(url)
                        appearance = {
                            "query": query,
                            "channel": discovered_by,
                            "rank": rank,
                            "score": item.score,
                        }
                        round_urls.add(canonical)
                        existing_index = candidate_indexes.get(canonical)
                        if existing_index is not None:
                            existing = candidates[existing_index]
                            metadata = dict(existing.metadata)
                            appearances = list(metadata.get("appearances") or [])
                            if appearance not in appearances:
                                appearances.append(appearance)
                            metadata["appearances"] = appearances
                            candidates[existing_index] = replace(existing, metadata=metadata)
                            continue
                        candidate_indexes[canonical] = len(candidates)
                        candidates.append(MediaCandidate(
                            title=title,
                            url=url,
                            source_name=_source_name_for_url(url),
                            published_at=item.published_date,
                            # Tavily 的 content 是搜索摘要，不是网页正文。
                            snippet=" ".join((item.search_snippet or "").split())[:500],
                            discovered_by=(discovered_by,),
                            source_group=_source_group_for_url(url),
                            query=query,
                            metadata={
                                "search_snippet": " ".join(
                                    (item.search_snippet or "").split()
                                )[:500],
                                "appearances": [appearance],
                            },
                        ))
                new_urls = round_urls - seen_urls
                seen_urls.update(round_urls)
                self.round_stats.append({
                    "query": query,
                    "round": round_index,
                    "returned_count": returned_count,
                    "unique_count": len(round_urls),
                    "new_unique_count": len(new_urls),
                })
                self.diagnostics.successful_sources[round_label] = f"{returned_count} 条，新增 {len(new_urls)} 条"
                if progress:
                    progress(f"    Tavily 第 {round_index} 轮成功：{query}（{returned_count} 条，新增 {len(new_urls)} 条）")
        return candidates
