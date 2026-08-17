"""NewsNow、RSS、Tavily 媒体研究流程编排器。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from difflib import SequenceMatcher
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
    DiscoveryResult,
    MediaCandidate,
    MediaDocument,
    RelevanceDecision,
    NewsNowProvider,
    RSSProvider,
    TavilyMediaProvider,
    WeiboProvider,
    WebReader,
    is_weibo_candidate_relevant,
)
from .tools.text_chunking import select_relevance_chunks, split_text
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
                if feed.get("source_group", "news_media") not in {"official_media", "news_media"}:
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
                search_rounds=int(tavily.get("search_rounds", 2)),
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

        用户问题会先被转换成 topic、预筛词和 Tavily provider query。
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
        state.newsnow_rss_core = list(plan.get("newsnow_rss_core") or [])
        state.newsnow_rss_support = list(plan.get("newsnow_rss_support") or [])
        state.tavily_queries = list(plan.get("tavily_queries") or [])
        state.weibo_query = str(plan.get("weibo_query") or "")
        return state

    def discover_from_plan(
        self,
        state: RunState,
        limit: int | None = None,
        cancel_check=None,
    ) -> RunState:
        """第二阶段：使用已经审核的检索计划执行媒体发现。

        这里接收的 state 已经包含 NewsNow/RSS 相关性词和 Tavily Query，因此才会真正调用
        NewsNow、RSS、Tavily、微博等 Provider。
        """
        if not state.topic.strip():
            raise ValueError("检索计划缺少研究主题")
        state.newsnow_rss_core = list(dict.fromkeys(
            " ".join(term.split()) for term in state.newsnow_rss_core if term.strip()
        ))
        state.newsnow_rss_support = list(dict.fromkeys(
            " ".join(term.split()) for term in state.newsnow_rss_support if term.strip()
        ))
        state.tavily_queries = list(dict.fromkeys(
            " ".join(query.split()) for query in state.tavily_queries if query.strip()
        ))
        state.weibo_query = " ".join(state.weibo_query.split())
        selection_terms = state.newsnow_rss_core + state.newsnow_rss_support
        if not selection_terms:
            raise ValueError("检索计划至少需要一个 NewsNow/RSS 相关性词")
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
            selection_terms,
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
            tavily_queries=state.tavily_queries,
            weibo_queries=[state.weibo_query] if state.weibo_query else None,
            newsnow_rss_core=state.newsnow_rss_core,
            newsnow_rss_support=state.newsnow_rss_support,
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
            state.discovery = self._filter_weibo_before_persistence(
                state.discovery,
                weibo_provider,
                state,
                selection,
            )
            state.weibo_raw = list(getattr(weibo_provider, "raw_results", []))
        state.retrieval_reflection = dict(
            state.discovery.retrieval_reflection if state.discovery else {}
        )
        return state

    def _filter_weibo_before_persistence(
        self,
        discovery: DiscoveryResult,
        provider: object,
        state: RunState,
        selection: dict,
    ) -> DiscoveryResult:
        """Review Weibo posts in memory, fetch comments for accepted posts only."""
        candidates = list(discovery.provider_candidates.get("weibo") or [])
        unique: list[MediaCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.guid or candidate.url
            if key and key not in seen:
                seen.add(key)
                unique.append(candidate)
        accepted: list[MediaCandidate] = []
        batch_size = 15
        for start in range(0, len(unique), batch_size):
            batch = unique[start:start + batch_size]
            documents = [
                MediaDocument(
                    candidate=item,
                    final_url=item.url,
                    fetched_at=str(item.metadata.get("fetched_at") or datetime.now().astimezone().isoformat(timespec="seconds")),
                    content_type="text/plain",
                    content=item.snippet,
                    raw_content=item.snippet,
                )
                for item in batch
            ]
            try:
                decisions = self.candidate_filter_node.run({
                    "stage": "content",
                    "topic": state.topic,
                    "newsnow_rss_core": state.newsnow_rss_core,
                    "newsnow_rss_support": state.newsnow_rss_support,
                    "documents": documents,
                    "max_content_chars": int(selection.get("content_filter_max_chars", 5_000)),
                    "model_min_score": 30,
                })
            except Exception as exc:
                self._progress(f"微博相关性模型失败，使用本地宽松规则：{exc}")
                decisions = []
                for item in batch:
                    relevant = is_weibo_candidate_relevant(
                        item.snippet,
                        state.newsnow_rss_core,
                        state.newsnow_rss_support,
                    )
                    decisions.append(RelevanceDecision(
                        item,
                        "weibo_local_fallback",
                        relevant,
                        30 if relevant else 0,
                        "相关性模型失败，使用 core/support 本地宽松规则",
                    ))
            for candidate, decision in zip(batch, decisions):
                if not decision.relevant:
                    continue
                metadata = dict(candidate.metadata)
                metadata.update({
                    "prechecked_relevance": True,
                    "relevance_score": decision.score,
                    "relevance_reason": decision.reason,
                })
                accepted.append(replace(candidate, metadata=metadata))

        fetch_comments = getattr(provider, "fetch_comments_for_candidates", None)
        if fetch_comments and accepted:
            try:
                refreshed = fetch_comments(accepted, progress=self.progress)
                accepted_by_key = {
                    item.guid or item.url: item for item in accepted
                }
                accepted = [
                    replace(
                        item,
                        metadata={
                            **item.metadata,
                            **{
                                key: value
                                for key, value in accepted_by_key[item.guid or item.url].metadata.items()
                                if key.startswith("relevance_") or key == "prechecked_relevance"
                            },
                        },
                    )
                    for item in refreshed
                ]
            except Exception as exc:
                self._progress(f"微博评论跳过：{exc}")

        accepted_by_key = {item.guid or item.url: item for item in accepted}

        def replace_weibo(items: list[MediaCandidate]) -> list[MediaCandidate]:
            result = []
            for item in items:
                if "weibo" not in item.discovered_by:
                    result.append(item)
                    continue
                replacement = accepted_by_key.get(item.guid or item.url)
                if replacement is not None:
                    result.append(replacement)
            return result

        raw_results = list(getattr(provider, "raw_results", []))
        accepted_wids = {
            str(item.metadata.get("wid") or "") for item in accepted
        }
        provider.raw_results = [
            item for item in raw_results
            if str(item.get("wid") or "") in accepted_wids
        ]
        provider_candidates = dict(discovery.provider_candidates)
        provider_candidates["weibo"] = accepted
        stats = dict(discovery.stats)
        stats["weibo_relevance_reviewed_count"] = len(unique)
        stats["weibo_relevance_accepted_count"] = len(accepted)
        stats["weibo_relevance_filtered_count"] = len(unique) - len(accepted)
        return replace(
            discovery,
            candidates=replace_weibo(list(discovery.candidates)),
            raw_candidates=replace_weibo(list(discovery.raw_candidates)),
            provider_candidates=provider_candidates,
            stats=stats,
        )

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

    def prepare_analysis(self, state: RunState, cancel_check=None) -> RunState:
        """正文读取、相关性复核和 MediaNode 提炼；停在 BriefNode 之前。"""
        if state.discovery is None:
            raise ValueError("尚未完成媒体发现")
        media_config = load_media_sources()
        selection = media_config["selection"]
        state.selected_documents = []
        state.content_decisions = []
        state.insights = []
        # Stage 1 已完成保存前筛选；正文阶段读取该次 Stage 1 的全部候选，
        # 不再按来源或数量截断。
        selected = list(
            state.discovery.raw_candidates or state.discovery.candidates
        )
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
                    raw_content = content
                else:
                    result = self.reader.read(candidate.url)
                    raw_content = result.content
                    chunks = split_text(result.content)
                    selected_chunks = select_relevance_chunks(
                        chunks,
                        topic=state.topic,
                        core_terms=state.newsnow_rss_core,
                        support_terms=state.newsnow_rss_support,
                        top_k=int(selection.get("content_relevance_top_k", 5)),
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
                    raw_content=raw_content,
                ))
            except Exception as exc:
                self._progress(f"正文跳过：{candidate.title}（{exc}）")
        state.read_success_count = len(state.selected_documents)
        if cancel_check and cancel_check():
            raise RuntimeError("任务已中止")
        if not state.selected_documents:
            raise ValueError("没有成功读取的正文")

        # 文章型来源先基于完整正文去重，再用保留下来的正文片段做相关性复核。
        unique_documents = []
        fingerprints: dict[str, MediaDocument] = {}
        for document in state.selected_documents:
            if document.candidate.source_group == "social_media":
                unique_documents.append(document)
                continue
            full_content = document.raw_content or document.content
            key = hashlib.sha256(re.sub(r"\s+", "", full_content).encode()).hexdigest()
            duplicate = next(
                (item for item in unique_documents
                 if SequenceMatcher(None,
                    (item.raw_content or item.content)[:12000],
                    full_content[:12000]).ratio() >= 0.92),
                None,
            )
            if key in fingerprints or duplicate is not None:
                self._progress(f"正文重复：{document.candidate.title}")
                continue
            fingerprints[key] = document
            unique_documents.append(document)
        state.selected_documents = unique_documents
        state.read_success_count = len(unique_documents)

        self._progress(
            f"③ 正文读取成功 {state.read_success_count}/{state.read_attempted_count}，"
            "正在复核相关性……"
        )
        # 微博已在保存前完成相关性复核；其他来源继续执行 Stage 2 正文复核。
        article_documents = [
            document for document in state.selected_documents
            if not (
                "weibo" in document.candidate.discovered_by
                and document.candidate.metadata.get("prechecked_relevance")
            )
        ]
        article_decisions = self.candidate_filter_node.run({
            "stage": "content",
            "topic": state.topic,
            "newsnow_rss_core": state.newsnow_rss_core,
            "newsnow_rss_support": state.newsnow_rss_support,
            "queries": state.newsnow_rss_core + state.newsnow_rss_support,
            "documents": article_documents,
            "max_content_chars": int(selection.get("content_filter_max_chars", 3_000)),
            "model_min_score": int(selection.get("relevance_model_min_score", 60)),
        }) if article_documents else []
        article_decision_by_url = {
            decision.candidate.url: decision for decision in article_decisions
        }
        state.content_decisions = []
        for document in state.selected_documents:
            candidate = document.candidate
            if "weibo" in candidate.discovered_by and candidate.metadata.get("prechecked_relevance"):
                state.content_decisions.append(RelevanceDecision(
                    candidate,
                    "weibo_pre_persistence",
                    True,
                    int(candidate.metadata.get("relevance_score") or 30),
                    str(candidate.metadata.get("relevance_reason") or "微博已通过保存前相关性复核"),
                ))
            else:
                state.content_decisions.append(article_decision_by_url[candidate.url])
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
        return state

    def generate_brief(self, state: RunState, cancel_check=None) -> RunState:
        """根据已经持久化或刚生成的 MediaNode 结果生成最终简报。"""
        if not state.insights:
            raise ValueError("没有可用于生成简报的结构化媒体分析")
        media_insights = [
            asdict(item) for item in state.insights
            if item.source_group != "social_media"
        ]
        social_insights = [
            asdict(item) for item in state.insights
            if item.source_group == "social_media"
        ]
        # BriefNode 跨来源综合为 brief_data，再由固定模板生成 Markdown。
        brief_result = self.brief_node.generate({
            "query": state.query,
            "topic": state.topic,
            "media_insights": media_insights,
            "social_insights": social_insights,
        })
        state.brief_data = brief_result.data
        state.brief = brief_result.markdown
        if cancel_check and cancel_check():
            raise RuntimeError("任务已中止")
        return state

    def complete(self, state: RunState, cancel_check=None) -> RunState:
        """兼容完整流程：准备结构化分析后继续生成简报。"""
        state = self.prepare_analysis(state, cancel_check=cancel_check)
        return self.generate_brief(state, cancel_check=cancel_check)

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
