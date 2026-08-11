"""NewsNow、RSS、Tavily 媒体研究流程编排器。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from .llms import LLMClient
from .nodes import BriefNode, CandidateFilterNode, MediaNode, QueryPlanNode
from .state import RunState
from .tools import (
    MediaDiscovery,
    MediaDocument,
    NewsNowProvider,
    RSSProvider,
    TavilyMediaProvider,
    WeiboProvider,
    WebReader,
)
from .tools.text_chunking import select_chunks, split_text
from .utils.config import PROJECT_ROOT, Settings
from .utils.media_sources import (
    MediaSourcesConfigError,
    load_media_sources,
    resolve_feed_url,
    resolve_env_path,
)


ProgressCallback = Callable[[str], None]


class FinancialMediaAgent:
    def __init__(
        self,
        config: Settings,
        *,
        llm_client=None,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.config = config
        self.progress = progress
        self.llm_client = llm_client or LLMClient(
            api_key=config.QUERY_ENGINE_API_KEY,
            model_name=config.QUERY_ENGINE_MODEL_NAME,
            base_url=config.QUERY_ENGINE_BASE_URL,
            timeout=config.LLM_REQUEST_TIMEOUT,
        )
        self.query_plan_node = QueryPlanNode(self.llm_client)
        self.candidate_filter_node = CandidateFilterNode(self.llm_client)
        self.media_node = MediaNode(self.llm_client)
        self.brief_node = BriefNode(self.llm_client)
        self.reader = WebReader(
            timeout=config.WEB_REQUEST_TIMEOUT,
            max_content_bytes=config.WEB_MAX_CONTENT_BYTES,
            max_text_length=config.WEB_MAX_TEXT_LENGTH,
            user_agent=config.WEB_USER_AGENT,
        )

    def _progress(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _build_providers(self, media_config: dict) -> dict[str, object]:
        providers: dict[str, object] = {}
        newsnow = media_config["newsnow"]
        if newsnow.get("enabled", True):
            providers["newsnow"] = NewsNowProvider(
                api_url=str(newsnow.get("api_url", "")),
                sources=newsnow.get("sources", []),
                timeout=float(newsnow.get("timeout_seconds", 30)),
                max_retries=int(newsnow.get("max_retries", 1)),
                retry_wait_min=float(newsnow.get("retry_wait_min_seconds", 2)),
                retry_wait_max=float(newsnow.get("retry_wait_max_seconds", 3)),
                request_interval=float(newsnow.get("request_interval_seconds", 0.5)),
            )

        rss = media_config["rss"]
        if rss.get("enabled", False):
            feeds = []
            for feed in rss.get("feeds", []):
                if not feed.get("enabled", True):
                    continue
                try:
                    resolved = dict(feed)
                    resolved["url"] = resolve_feed_url(str(feed.get("url", "")))
                    feeds.append(resolved)
                except MediaSourcesConfigError as exc:
                    self._progress(
                        f"RSS 跳过：{feed.get('name', feed.get('id'))}（{exc}）"
                    )
            providers["rss"] = RSSProvider(
                feeds=feeds,
                timeout=float(rss.get("timeout_seconds", 15)),
                max_age_days=int(rss.get("max_age_days", 30)),
                max_content_bytes=int(rss.get("max_content_bytes", 6_000_000)),
                default_max_items=int(rss.get("default_max_items", 0)),
                request_interval_min=float(
                    rss.get("request_interval_min_seconds", 0.5)
                ),
                request_interval_max=float(
                    rss.get("request_interval_max_seconds", 1.0)
                ),
                max_retries=int(rss.get("max_retries", 1)),
                retry_wait_min=float(rss.get("retry_wait_min_seconds", 2)),
                retry_wait_max=float(rss.get("retry_wait_max_seconds", 3)),
            )

        tavily = media_config.get("tavily") or {}
        api_key = (self.config.TAVILY_API_KEY or "").strip()
        if tavily.get("enabled", False) and api_key:
            days = tavily.get("days")
            providers["tavily"] = TavilyMediaProvider(
                api_key,
                max_results_per_query=int(tavily.get("max_results_per_query", 5)),
                search_depth=str(tavily.get("search_depth", "basic")),
                days=int(days) if days is not None else None,
            )
        elif tavily.get("enabled", False):
            self._progress("Tavily 跳过：未配置 TAVILY_API_KEY")

        weibo = media_config.get("weibo") or {}
        if weibo.get("enabled", False):
            comments = weibo.get("comments") or {}
            providers["weibo"] = WeiboProvider(
                cookie_file=resolve_env_path(
                    str(weibo.get("cookie_file", "")), "WEIBO_COOKIE_FILE"
                ),
                search_url=str(weibo.get("search_url", "")),
                comments_url=str(weibo.get("comments_url", "")),
                target_posts=int(weibo.get("target_posts", 20)),
                max_search_pages=int(weibo.get("max_search_pages", 3)),
                timeout=float(weibo.get("timeout_seconds", 20)),
                request_interval_min=float(
                    weibo.get("request_interval_min_seconds", 4)
                ),
                request_interval_max=float(
                    weibo.get("request_interval_max_seconds", 8)
                ),
                trust_env_proxy=bool(weibo.get("trust_env_proxy", False)),
                comments_enabled=bool(comments.get("enabled", False)),
                max_comment_posts=int(comments.get("max_posts", 2)),
                comment_interval_min=float(
                    comments.get("request_interval_min_seconds", 5)
                ),
                comment_interval_max=float(
                    comments.get("request_interval_max_seconds", 10)
                ),
            )
        return providers

    def discover(self, query: str, limit: int | None = None) -> RunState:
        state = self.create_plan(query)
        return self.discover_from_plan(state, limit)

    def create_plan(self, query: str) -> RunState:
        """生成检索计划，但不调用任何媒体 Provider。"""
        query = query.strip()
        if not query:
            raise ValueError("研究主题不能为空")
        state = RunState(query=query)
        self._progress("① 正在规划媒体检索词……")
        plan = self.query_plan_node.run({"query": query})
        state.topic = plan["topic"]
        state.media_queries = plan["media_queries"]
        state.provider_queries = dict(plan.get("provider_queries") or {})
        return state

    def discover_from_plan(
        self,
        state: RunState,
        limit: int | None = None,
        cancel_check=None,
    ) -> RunState:
        """使用已经审核的检索计划执行媒体发现。"""
        if not state.topic.strip():
            raise ValueError("检索计划缺少研究主题")
        state.media_queries = [
            " ".join(query.split())
            for query in state.media_queries
            if isinstance(query, str) and query.strip()
        ]
        state.media_queries = list(dict.fromkeys(state.media_queries))
        if not state.media_queries:
            raise ValueError("检索计划至少需要一个检索词")
        media_config = load_media_sources()
        providers = self._build_providers(media_config)
        if not providers:
            raise ValueError("没有可用的媒体 Provider")
        selection = media_config["selection"]
        if cancel_check and cancel_check():
            raise RuntimeError("任务已中止")
        for provider in providers.values():
            setter = getattr(provider, "set_cancel_check", None)
            if setter:
                setter(cancel_check)
        self._progress(f"② 正在依次运行：{' → '.join(providers)}")
        state.discovery = MediaDiscovery(providers).run(
            state.media_queries,
            limit=limit or int(selection.get("candidate_limit", 20)),
            max_per_source=int(selection.get("max_per_source", 3)),
            max_age_days=int(media_config["rss"].get("max_age_days", 30)),
            max_per_source_overrides={
                "weibo": max(
                    int(weibo_config.get("target_posts", 20)) *
                    int(weibo_config.get("max_search_pages", 3)),
                    20,
                )
            } if (weibo_config := media_config.get("weibo") or {}).get("enabled", False) else None,
            provider_queries={
                name: [query]
                for name, query in state.provider_queries.items()
                if isinstance(query, str) and query.strip() and name in providers
            },
            cancel_check=cancel_check,
            progress=self.progress,
        )
        weibo_provider = providers.get("weibo")
        if weibo_provider is not None:
            state.weibo_raw = list(getattr(weibo_provider, "raw_results", []))
        return state

    def run(self, query: str, limit: int | None = None) -> RunState:
        state = self.discover(query, limit)
        return self.complete(state)

    def run_from_plan(
        self,
        state: RunState,
        limit: int | None = None,
    ) -> RunState:
        """从人工审核后的计划继续执行完整研究。"""
        state = self.discover_from_plan(state, limit)
        return self.complete(state)

    def complete(self, state: RunState, cancel_check=None) -> RunState:
        """基于媒体发现结果读取正文、分析并生成简报。"""
        if state.discovery is None:
            raise ValueError("尚未完成媒体发现")
        media_config = load_media_sources()
        selection = media_config["selection"]
        candidates = state.discovery.candidates if state.discovery else []
        news = [item for item in candidates if item.source_group != "social_media"]
        social = [item for item in candidates if item.source_group == "social_media"]
        selected = news[: int(selection.get("read_limit", 8))]
        social_limit = int(selection.get("social_read_limit", 5))
        selected += social if social_limit <= 0 else social[:social_limit]
        state.read_attempted_count = len(selected)
        for index, candidate in enumerate(selected, 1):
            if cancel_check and cancel_check():
                raise RuntimeError("任务已中止")
            self._progress(
                f"  [{index}/{len(selected)}] 读取：{candidate.source_name}｜"
                f"{candidate.title[:50]}"
            )
            try:
                if candidate.metadata.get("content_ready"):
                    final_url = candidate.url
                    fetched_at = str(
                        candidate.metadata.get("fetched_at")
                        or datetime.now().astimezone().isoformat(timespec="seconds")
                    )
                    content_type = "text/plain"
                    content = candidate.snippet
                else:
                    result = self.reader.read(candidate.url)
                    chunks = split_text(result.content)
                    selected_chunks = select_chunks(
                        chunks,
                        topic=state.topic,
                        queries=state.media_queries,
                        top_k=3,
                    )
                    final_url = result.final_url
                    fetched_at = result.fetched_at
                    content_type = result.content_type
                    content = "\n\n".join(selected_chunks)
                state.selected_documents.append(MediaDocument(
                    candidate=candidate,
                    final_url=final_url,
                    fetched_at=fetched_at,
                    content_type=content_type,
                    content=content,
                ))
            except Exception as exc:
                self._progress(f"正文跳过：{candidate.title}（{exc}）")
        state.read_success_count = len(state.selected_documents)
        if cancel_check and cancel_check():
            raise RuntimeError("任务已中止")
        if not state.selected_documents:
            raise ValueError("没有成功读取的正文")

        self._progress(
            f"③ 正文读取成功 {state.read_success_count}/{state.read_attempted_count}，"
            "正在复核相关性……"
        )
        state.content_decisions = self.candidate_filter_node.run({
            "stage": "content",
            "topic": state.topic,
            "queries": state.media_queries,
            "documents": state.selected_documents,
            "max_content_chars": int(selection.get("content_filter_max_chars", 3_000)),
            "model_min_score": int(selection.get("relevance_model_min_score", 60)),
        })
        if cancel_check and cancel_check():
            raise RuntimeError("任务已中止")
        relevant_documents = [
            document
            for document, decision in zip(
                state.selected_documents, state.content_decisions
            )
            if decision.relevant
        ]
        for decision in state.content_decisions:
            if not decision.relevant:
                self._progress(
                    f"正文拒绝：{decision.candidate.title}（{decision.reason}）"
                )
        self._progress(
            f"④ 正文高相关 {len(relevant_documents)}/{state.read_success_count}"
        )
        if not relevant_documents:
            raise ValueError("没有通过正文相关性复核的文章")

        state.insights = self.media_node.run(relevant_documents)
        if cancel_check and cancel_check():
            raise RuntimeError("任务已中止")
        media_insights = [
            asdict(item) for item in state.insights
            if item.source_group != "social_media"
        ]
        social_insights = [
            asdict(item) for item in state.insights
            if item.source_group == "social_media"
        ]
        state.brief = self.brief_node.run({
            "query": state.query,
            "topic": state.topic,
            "official_documents": [],
            "media_insights": media_insights,
            "social_insights": social_insights,
        })
        if cancel_check and cancel_check():
            raise RuntimeError("任务已中止")
        return state

    @staticmethod
    def save_brief(brief: str, output_dir: Path | None = None) -> Path:
        directory = output_dir or PROJECT_ROOT / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"topic_brief_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path.write_text(brief.strip() + "\n", encoding="utf-8")
        return path
