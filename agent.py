"""NewsNow、RSS、Tavily 媒体研究流程编排器。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from typing import Callable

from .llms import LLMClient
from .nodes import (
    AdaptiveRetrievalNode,
    BriefNode,
    CandidateFilterNode,
    MediaNode,
    QueryPlanNode,
    RetrievalCheckNode,
)
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
    """研究流程的总编排器：负责把多个 Node 和 Tool 串成完整流程。

    可以把这个类理解成“项目经理”：它本身不负责完成每一个细节，
    而是决定先规划 Query，再检索媒体，最后读取正文、分析并生成报告。
    """

    def __init__(
        self,
        config: Settings,
        *,
        llm_client=None,
        progress: ProgressCallback | None = None,
        enabled_sources: set[str] | None = None,
    ) -> None:
        self.config = config
        self.progress = progress
        self.enabled_sources = enabled_sources
        self.llm_client = llm_client or LLMClient(
            api_key=config.QUERY_ENGINE_API_KEY,
            model_name=config.QUERY_ENGINE_MODEL_NAME,
            base_url=config.QUERY_ENGINE_BASE_URL,
            timeout=config.LLM_REQUEST_TIMEOUT,
        )
        # Node 负责相对独立的推理/分析步骤；Provider 和 WebReader 负责外部数据访问。
        self.query_plan_node = QueryPlanNode(self.llm_client)
        self.retrieval_check_node = RetrievalCheckNode()
        self.adaptive_retrieval_node = AdaptiveRetrievalNode(self.llm_client)
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
        if self._source_enabled("newsnow") and (newsnow.get("enabled", True) or self.enabled_sources is not None):
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
        if self._source_enabled("rss") and (rss.get("enabled", False) or self.enabled_sources is not None):
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
        if self._source_enabled("tavily") and api_key and (tavily.get("enabled", False) or self.enabled_sources is not None):
            days = tavily.get("days")
            trusted_domains = list(tavily.get("trusted_media_domains", []))
            for domain in list(tavily.get("domestic_finance_domains", [])) + list(
                tavily.get("overseas_finance_domains", [])
            ):
                if domain not in trusted_domains:
                    trusted_domains.append(domain)
            providers["tavily"] = TavilyMediaProvider(
                api_key,
                max_results_per_query=int(tavily.get("max_results_per_query", 5)),
                targeted_search_enabled=bool(tavily.get("targeted_search_enabled", True)),
                targeted_max_results=int(tavily.get("targeted_max_results", 10)),
                trusted_media_domains=trusted_domains,
                search_depth=str(tavily.get("search_depth", "basic")),
                days=int(days) if days is not None else None,
            )
        elif tavily.get("enabled", False):
            self._progress("Tavily 跳过：未配置 TAVILY_API_KEY")

        weibo = media_config.get("weibo") or {}
        if self._source_enabled("weibo") and (weibo.get("enabled", False) or self.enabled_sources is not None):
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

    def _source_enabled(self, name: str) -> bool:
        return self.enabled_sources is None or name in self.enabled_sources

    def discover(self, query: str, limit: int | None = None) -> RunState:
        """一站式执行：先生成计划，再执行媒体发现。

        API 的人工审核流程不会直接调用这个方法，而是把 create_plan 和
        discover_from_plan 拆开，让用户有机会修改 Query。
        """
        state = self.create_plan(query)
        return self.discover_from_plan(state, limit)

    def create_plan(self, query: str) -> RunState:
        """第一阶段：生成检索计划，但不调用任何媒体 Provider。

        用户问题会先被转换成 topic、media_queries 和 provider_queries。
        只有这一步完成后，后续检索才有明确的 Query 可以使用。
        """
        query = query.strip()
        if not query:
            raise ValueError("研究主题不能为空")
        state = RunState(query=query)
        self._progress("① 正在规划媒体检索词……")
        # QueryPlanNode 通常会调用 LLM，把自然语言问题拆成可检索的 Query。
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
        """第二阶段：使用已经审核的检索计划执行媒体发现。

        这里接收的 state 已经包含 media_queries，因此才会真正调用
        NewsNow、RSS、Tavily、微博等 Provider。
        """
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
        # 先根据配置和用户选择决定本次运行启用哪些 Provider。
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
        # MediaDiscovery 负责协调多个 Provider，并汇总候选结果。
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
                name: ([*query] if isinstance(query, list) else [query])
                for name, query in state.provider_queries.items()
                if (
                    name in providers
                    and (
                        (isinstance(query, str) and query.strip())
                        or (isinstance(query, list) and any(str(item).strip() for item in query))
                    )
                )
            },
            topic=state.topic,
            # 传入检查节点和补充检索节点，允许 MediaDiscovery 在首轮不足时补搜。
            retrieval_check_node=self.retrieval_check_node,
            adaptive_retrieval_node=self.adaptive_retrieval_node,
            adaptive_config=media_config.get("adaptive_retrieval") or {},
            cancel_check=cancel_check,
            progress=self.progress,
        )
        weibo_provider = providers.get("weibo")
        if weibo_provider is not None:
            state.weibo_raw = list(getattr(weibo_provider, "raw_results", []))
        state.retrieval_reflection = dict(
            state.discovery.retrieval_reflection if state.discovery else {}
        )
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
        """第三阶段：正文读取 → 相关性筛选 → 观点提炼 → 报告生成。"""
        if state.discovery is None:
            raise ValueError("尚未完成媒体发现")
        media_config = load_media_sources()
        selection = media_config["selection"]
        candidates = state.discovery.candidates if state.discovery else []
        news = [item for item in candidates if item.source_group != "social_media"]
        social = [item for item in candidates if item.source_group == "social_media"]
        # 检索结果通常很多，这里只选择有限数量的候选读取正文，控制成本和耗时。
        selected = news[: int(selection.get("read_limit", 8))]
        social_limit = int(selection.get("social_read_limit", 5))
        selected += social if social_limit <= 0 else social[:social_limit]
        state.read_attempted_count = len(selected)
        # 逐篇读取候选正文；单篇失败只跳过该文章，不立即让整个任务失败。
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
                        top_k=5,
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
        # 只有通过正文相关性复核的文档，才允许进入观点提炼阶段。
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

        # MediaNode 从相关正文中提炼事实、观点、影响对象和风险。
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
        # BriefNode 将结构化观点组织成最终 Markdown 简报。
        state.brief = self.brief_node.run({
            "query": state.query,
            "topic": state.topic,
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

    @staticmethod
    def save_retrieval_reflection(trace: dict, report_path: Path) -> Path | None:
        if not trace:
            return None
        path = report_path.with_suffix(".retrieval.json")
        path.write_text(
            json.dumps(trace, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path
