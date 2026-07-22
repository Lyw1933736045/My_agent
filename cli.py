"""My_agent 官方文件事实提取与候选管理 CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from typing import Sequence


COMMANDS = {
    "brief",
    "discover",
    "extract",
    "list",
    "media-search",
    "process",
    "reject",
    "search-web",
    "topic-brief",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in COMMANDS:
        parser = argparse.ArgumentParser(description="管理国务院政策候选文件")
        subparsers = parser.add_subparsers(dest="command", required=True)

        discover_parser = subparsers.add_parser("discover", help="发现最新国务院政策")
        discover_parser.add_argument("--limit", type=int, default=20)

        search_parser = subparsers.add_parser(
            "search-web", help="按自然语言主题检索国务院政策"
        )
        search_parser.add_argument("--query", required=True)
        search_parser.add_argument("--limit", type=int, default=10)

        media_parser = subparsers.add_parser(
            "media-search", help="按自然语言主题检索 NewsNow、RSS 与 Tavily 媒体候选"
        )
        media_parser.add_argument("--query", required=True)
        media_parser.add_argument("--limit", type=int, default=None)

        topic_brief_parser = subparsers.add_parser(
            "topic-brief", help="一步生成官方事实与媒体解读简报"
        )
        topic_brief_parser.add_argument("--query", required=True)
        topic_brief_parser.add_argument("--official-limit", type=int, default=3)
        topic_brief_parser.add_argument("--media-limit", type=int, default=None)
        topic_brief_parser.add_argument("--no-save", action="store_true")

        subparsers.add_parser("list", help="查看候选文件")

        brief_parser = subparsers.add_parser("brief", help="基于多个候选生成事实简报")
        brief_parser.add_argument("--ids", required=True)

        extract_parser = subparsers.add_parser("extract", help="只读验证一条候选")
        extract_parser.add_argument("--id", type=int, required=True, dest="document_id")

        process_parser = subparsers.add_parser("process", help="处理一条候选")
        process_parser.add_argument("--id", type=int, required=True, dest="document_id")

        reject_parser = subparsers.add_parser("reject", help="拒绝一条候选")
        reject_parser.add_argument("--id", type=int, required=True, dest="document_id")
        return parser.parse_args(arguments)

    parser = argparse.ArgumentParser(description="读取一个官方网页并提取金融事件事实")
    parser.set_defaults(command="url")
    parser.add_argument("official_url", nargs="?", help="政府、监管机构或交易所官方文件 URL")
    parser.add_argument("--no-save", action="store_true", help="不保存 state JSON")
    return parser.parse_args(arguments)


def _create_agent():
    from .agent import FinancialResearchAgent
    from .utils.config import Settings

    return FinancialResearchAgent(Settings())


def _create_llm_client():
    from .llms import LLMClient
    from .utils.config import Settings

    settings = Settings()
    return LLMClient(
        api_key=settings.QUERY_ENGINE_API_KEY,
        model_name=settings.QUERY_ENGINE_MODEL_NAME,
        base_url=settings.QUERY_ENGINE_BASE_URL,
        timeout=settings.LLM_REQUEST_TIMEOUT,
    )


def _run_url(args: argparse.Namespace) -> int:
    official_url = (args.official_url or "").strip()
    if not official_url:
        official_url = input("请输入官方文件 URL：").strip()
    if not official_url:
        print("错误：official_url 不能为空。", file=sys.stderr)
        return 2

    try:
        agent = _create_agent()
        verification = agent.verify_official_url(official_url)
        if verification.source_name:
            print(
                f"识别到官方机构：{verification.source_name}"
                f"（{verification.verification_status}）",
                file=sys.stderr,
            )
        else:
            print(
                "未识别为预定义官方来源；将继续读取，但验证状态为 unverified。",
                file=sys.stderr,
            )
        print(verification.verification_message, file=sys.stderr)
        state, state_path = agent.research_official_url(
            official_url,
            save_state=not args.no_save,
        )
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(asdict(state), ensure_ascii=False, indent=2))
    if state_path:
        print(f"\nstate JSON：{state_path}", file=sys.stderr)
    return 0


def _run_discover(args: argparse.Namespace) -> int:
    from .discovery import StateCouncilDiscovery
    from .storage import Database

    try:
        found, inserted = StateCouncilDiscovery(Database()).discover(args.limit)
    except Exception as exc:
        print(f"发现失败：{exc}", file=sys.stderr)
        return 1
    print(f"发现 {found} 条，新增 {inserted} 条，重复 {found - inserted} 条。")
    return 0


def _run_list() -> int:
    from .storage import Database

    documents = Database().list_documents()
    if not documents:
        print("暂无候选文件。")
        return 0
    print("ID\t发布日期\t标题\t状态\tURL")
    for document in documents:
        print(
            f"{document.id}\t{document.published_at or '-'}\t{document.title}\t"
            f"{document.status}\t{document.url}"
        )
    return 0


def _run_search_web(args: argparse.Namespace) -> int:
    from .nodes import QueryPlanNode
    from .storage import Database
    from .tools import StateCouncilSearch
    from .utils.config import Settings

    try:
        settings = Settings()
        plan = QueryPlanNode(_create_llm_client()).run({"query": args.query})
        searcher = StateCouncilSearch(
            timeout=settings.WEB_REQUEST_TIMEOUT,
            user_agent=settings.WEB_USER_AGENT,
        )
        candidates = searcher.search(plan["search_queries"], limit=args.limit)
        database = Database()
    except Exception as exc:
        print(f"主题检索失败：{exc}", file=sys.stderr)
        return 1

    print(f"主题：{plan['topic']}")
    print("检索词：")
    for index, query in enumerate(plan["search_queries"], 1):
        print(f"{index}. {query}")
    if not candidates:
        print("未发现通过官方来源校验的政策详情页。")
        return 0

    print("ID\t标题\t来源\tURL")
    for candidate in candidates:
        document_id, _ = database.upsert_document(
            source_id=candidate.source_id,
            title=candidate.title,
            url=candidate.url,
            published_at=candidate.published_at,
        )
        print(
            f"{document_id}\t{candidate.title}\t"
            f"{candidate.source_name}\t{candidate.url}"
        )
    return 0


def _run_media_search(args: argparse.Namespace) -> int:
    try:
        _progress("—— 媒体检索：开始 ——")
        plan, candidates, _ = _find_media_candidates(
            args.query, _create_llm_client(), args.limit
        )
    except Exception as exc:
        print(f"媒体检索失败：{exc}", file=sys.stderr)
        return 1

    print(f"主题：{plan['topic']}")
    print("媒体关键词：")
    for index, query in enumerate(plan["media_queries"], 1):
        print(f"{index}. {query}")
    if not candidates:
        print("当前 NewsNow、RSS 与 Tavily 中没有匹配的媒体文章。")
        return 0
    print("来源\t发现方式\t发布时间\t标题\tURL")
    for candidate in candidates:
        print(
            f"{candidate.source_name}\t{candidate.discovered_by}\t"
            f"{candidate.published_at or '-'}\t{candidate.title}\t{candidate.url}"
        )
    return 0


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _find_media_candidates(query: str, llm_client, limit: int | None = None):
    """统一执行媒体规划、抓取和确定性筛选。"""
    from .nodes import QueryPlanNode
    from .tools import NewsNowProvider, RSSProvider, filter_media_candidates
    from .tools.tavily_provider import TavilyMediaProvider
    from .utils.config import Settings
    from .utils.media_sources import (
        MediaSourcesConfigError,
        load_media_sources,
        resolve_feed_url,
    )

    settings = Settings()
    config = load_media_sources()
    _progress("① 正在规划检索词……")
    plan = QueryPlanNode(llm_client).run({"query": query})
    _progress(
        f"   主题：{plan['topic']}；关键词 {len(plan['media_queries'])} 条"
    )
    selection = config["selection"]
    candidate_limit = limit or int(selection.get("candidate_limit", 20))
    candidates = []
    before = 0

    newsnow = config["newsnow"]
    if newsnow.get("enabled", True):
        sources = [item for item in newsnow.get("sources", []) if item.get("enabled", True)]
        _progress(f"② 正在抓取 NewsNow（{len(sources)} 个平台）……")
        candidates.extend(
            NewsNowProvider(
                api_url=str(newsnow.get("api_url", "")),
                sources=newsnow.get("sources", []),
                timeout=float(newsnow.get("timeout_seconds", 10)),
            ).search(
                plan["media_queries"],
                limit=candidate_limit * 2,
                progress=_progress,
            )
        )
        _progress(f"   NewsNow 累计候选：{len(candidates) - before}")
        before = len(candidates)
    else:
        _progress("② NewsNow 已关闭，跳过")

    rss = config["rss"]
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
                name = feed.get("name", feed.get("id"))
                _progress(f"媒体 RSS 跳过：{name}（{exc}）")
        _progress(f"③ 正在抓取 RSS（{len(feeds)} 个源）……")
        candidates.extend(
            RSSProvider(
                feeds=feeds,
                timeout=float(rss.get("timeout_seconds", 15)),
                max_age_days=int(rss.get("max_age_days", 3)),
                max_content_bytes=int(rss.get("max_content_bytes", 6_000_000)),
                user_agent=settings.WEB_USER_AGENT,
            ).search(
                plan["media_queries"],
                limit=candidate_limit,
                progress=_progress,
            )
        )
        _progress(f"   RSS 累计候选：{len(candidates) - before}")
        before = len(candidates)
    else:
        _progress("③ RSS 已关闭，跳过")

    tavily = config.get("tavily") or {}
    if tavily.get("enabled", False):
        api_key = (settings.TAVILY_API_KEY or "").strip()
        if not api_key:
            _progress("④ 媒体 Tavily 跳过：未配置 TAVILY_API_KEY")
        else:
            _progress(
                f"④ 正在抓取 Tavily（{len(plan['media_queries'])} 组关键词）……"
            )
            try:
                days = tavily.get("days")
                candidates.extend(
                    TavilyMediaProvider(
                        api_key,
                        max_results_per_query=int(
                            tavily.get("max_results_per_query", 5)
                        ),
                        search_depth=str(tavily.get("search_depth", "basic")),
                        days=int(days) if days is not None else None,
                    ).search(
                        plan["media_queries"],
                        limit=candidate_limit,
                        progress=_progress,
                    )
                )
                _progress(f"   Tavily 累计候选：{len(candidates) - before}")
            except Exception as exc:
                _progress(f"媒体 Tavily 跳过：{exc}")
    else:
        _progress("④ Tavily 已关闭，跳过")

    # 三类来源分别筛选，避免某一类挤掉其余候选。
    _progress("⑤ 正在按来源分组筛选候选……")
    selected = []
    for source_group in ("official_media", "news_media", "social_media"):
        grouped = [
            candidate for candidate in candidates
            if candidate.source_group == source_group
        ]
        selected.extend(
            filter_media_candidates(
                grouped,
                plan["media_queries"],
                limit=candidate_limit,
                max_per_source=int(selection.get("max_per_source", 3)),
            )
        )
    _progress(f"   筛选后候选：{len(selected)} 篇")
    return plan, selected, config


def _find_official_rss_candidates(plan: dict, config: dict, settings) -> list:
    """读取 fact 层 RSS；未配置 RSSHub 时仍保留官方原生 RSS。"""
    from .tools import RSSProvider
    from .utils.media_sources import MediaSourcesConfigError, resolve_feed_url

    rss = config["rss"]
    feeds = []
    warned_rsshub = False
    for feed in rss.get("official_feeds", []):
        if not feed.get("enabled", True) or feed.get("layer") != "fact":
            continue
        try:
            resolved = dict(feed)
            resolved["url"] = resolve_feed_url(feed.get("url", ""))
            feeds.append(resolved)
        except MediaSourcesConfigError as exc:
            if not warned_rsshub:
                print(f"官方 RSSHub 跳过：{exc}", file=sys.stderr)
                warned_rsshub = True
    if not feeds:
        return []
    return RSSProvider(
        feeds=feeds,
        timeout=float(rss.get("timeout_seconds", 15)),
        max_age_days=int(rss.get("max_age_days", 30)),
        max_content_bytes=int(rss.get("max_content_bytes", 6_000_000)),
        user_agent=settings.WEB_USER_AGENT,
    ).search(plan["official_queries"], limit=20)


def _run_topic_brief(args: argparse.Namespace) -> int:
    from .agent import PROJECT_ROOT
    from .nodes import BriefNode, MediaNode
    from .tools import MediaDocument, StateCouncilSearch, WebReader
    from .utils.config import Settings

    try:
        settings = Settings()
        agent = _create_agent()
        _progress("—— 主题简报：开始媒体检索 ——")
        plan, media_candidates, media_config = _find_media_candidates(
            args.query, agent.llm_client, args.media_limit
        )
    except Exception as exc:
        print(f"主题简报检索失败：{exc}", file=sys.stderr)
        return 1

    official_candidates = []
    if media_config.get("official", {}).get("enabled", True):
        try:
            _progress("⑥ 正在检索国务院政策文件……")
            official_candidates = StateCouncilSearch(
                timeout=settings.WEB_REQUEST_TIMEOUT,
                user_agent=settings.WEB_USER_AGENT,
            ).search(plan["official_queries"], limit=args.official_limit)
        except Exception as exc:
            # 官方搜索故障时仍允许媒体分支继续生成有限简报。
            _progress(f"国务院搜索跳过：{exc}")

        try:
            _progress("⑦ 正在检索官方 RSS……")
            official_rss_candidates = _find_official_rss_candidates(
                plan, media_config, settings
            )
            merged = {candidate.url: candidate for candidate in official_candidates}
            for candidate in official_rss_candidates:
                merged.setdefault(candidate.url, candidate)
            official_candidates = list(merged.values())[:args.official_limit]
        except Exception as exc:
            _progress(f"官方 RSS 跳过：{exc}")
    else:
        _progress("⑥ 官方检索已通过配置关闭")

    official_documents = []
    if official_candidates:
        _progress(f"⑧ 正在读取 {len(official_candidates)} 篇官方文件……")
    for index, candidate in enumerate(official_candidates, 1):
        _progress(
            f"  [{index}/{len(official_candidates)}] 官方：{candidate.title[:60]}"
        )
        try:
            state, _ = agent.research_official_url(candidate.url, save_state=False)
            if state.event_fact is None or state.source_document is None:
                raise ValueError("未提取到官方事实")
            official_documents.append(
                {
                    "official_url": state.source_document.final_url,
                    "event_fact": asdict(state.event_fact),
                }
            )
        except Exception as exc:
            _progress(f"官方网页跳过：{candidate.title}（{exc}）")

    selection = media_config["selection"]
    read_limit = int(selection.get("read_limit", 8))
    social_read_limit = int(selection.get("social_read_limit", 5))
    reader = WebReader(
        timeout=settings.WEB_REQUEST_TIMEOUT,
        max_content_bytes=settings.WEB_MAX_CONTENT_BYTES,
        max_text_length=settings.WEB_MAX_TEXT_LENGTH,
        user_agent=settings.WEB_USER_AGENT,
    )
    media_documents = []
    # 控制传给 MediaNode 的总正文量，避免候选数量放大提示词。
    per_document_limit = max(3_000, settings.SEARCH_CONTENT_MAX_LENGTH // max(read_limit, 1))
    news_candidates = [
        item for item in media_candidates
        if item.source_group in {"news_media", "official_media"}
    ][:read_limit]
    social_candidates = [
        item for item in media_candidates if item.source_group == "social_media"
    ][:social_read_limit]
    selected_media = news_candidates + social_candidates
    if selected_media:
        _progress(f"⑨ 正在读取 {len(selected_media)} 篇媒体文章……")
    for index, candidate in enumerate(selected_media, 1):
        _progress(
            f"  [{index}/{len(selected_media)}] 媒体：{candidate.source_name}｜"
            f"{candidate.title[:50]}"
        )
        try:
            result = reader.read(candidate.url)
            media_documents.append(
                MediaDocument(
                    candidate=candidate,
                    final_url=result.final_url,
                    fetched_at=result.fetched_at,
                    content_type=result.content_type,
                    content=result.content[:per_document_limit],
                )
            )
        except Exception as exc:
            _progress(f"媒体网页跳过：{candidate.title}（{exc}）")

    media_insights = []
    social_insights = []
    if media_documents:
        try:
            _progress(
                f"⑩ 正在提炼观点（已读 {len(media_documents)} 篇）……"
            )
            insights = [
                asdict(item) for item in MediaNode(agent.llm_client).run(media_documents)
            ]
            media_insights = [
                item for item in insights
                if item["source_group"] in {"news_media", "official_media"}
            ]
            social_insights = [
                item for item in insights if item["source_group"] == "social_media"
            ]
            _progress(
                f"   新闻/官媒观点 {len(media_insights)}；社交观点 {len(social_insights)}"
            )
        except Exception as exc:
            _progress(f"媒体观点提炼失败：{exc}")

    if not official_documents and not media_insights and not social_insights:
        print("简报生成失败：没有可用的媒体或社交平台内容。", file=sys.stderr)
        return 1
    try:
        _progress("⑪ 正在生成联合简报……")
        brief = BriefNode(agent.llm_client).run(
            {
                "topic": plan["topic"],
                "official_documents": official_documents,
                "media_insights": media_insights,
                "social_insights": social_insights,
            }
        )
        output_path = None
        if not args.no_save:
            output_dir = PROJECT_ROOT / "reports"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / (
                f"topic_brief_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            )
            output_path.write_text(brief.strip() + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"简报生成失败：{exc}", file=sys.stderr)
        return 1

    print(brief)
    if output_path:
        print(f"\n简报文件：{output_path}")
    return 0


def _parse_document_ids(raw_ids: str) -> list[int]:
    result = []
    for item in raw_ids.split(","):
        value = item.strip()
        if not value or not value.isdigit() or int(value) < 1:
            raise ValueError("--ids 必须是逗号分隔的正整数")
        document_id = int(value)
        if document_id not in result:
            result.append(document_id)
    if not result:
        raise ValueError("--ids 不能为空")
    return result


def _run_brief(args: argparse.Namespace) -> int:
    from .agent import PROJECT_ROOT
    from .nodes import BriefNode
    from .storage import Database

    try:
        document_ids = _parse_document_ids(args.ids)
        documents = Database().get_documents(document_ids)
        agent = _create_agent()
    except Exception as exc:
        print(f"简报准备失败：{exc}", file=sys.stderr)
        return 1

    successful = []
    for document in documents:
        try:
            state, _ = agent.research_official_url(document.url, save_state=True)
            if state.event_fact is None:
                raise ValueError("事实提取未返回 EventFact")
            official_url = (
                state.source_document.final_url
                if state.source_document
                else document.url
            )
            successful.append(
                {
                    "document_id": document.id,
                    "official_url": official_url,
                    "event_fact": asdict(state.event_fact),
                }
            )
            print(f"ID {document.id}：事实提取成功")
        except Exception as exc:
            print(f"ID {document.id}：跳过，原因：{exc}", file=sys.stderr)

    if not successful:
        print("简报生成失败：没有可用的已验证事实。", file=sys.stderr)
        return 1

    try:
        brief = BriefNode(agent.llm_client).run(
            {
                "topic": "所选国务院政策文件综合主题",
                "documents": successful,
            }
        )
        output_dir = PROJECT_ROOT / "reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / (
            f"policy_brief_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        )
        output_path.write_text(brief.strip() + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"简报生成失败：{exc}", file=sys.stderr)
        return 1

    print(brief)
    print(f"\n简报文件：{output_path}")
    return 0


def _run_process(args: argparse.Namespace) -> int:
    from .storage import Database

    database = Database()
    document = database.get_document(args.document_id)
    if document is None:
        print(f"处理失败：未找到候选文件 ID：{args.document_id}", file=sys.stderr)
        return 1
    if document.status == "rejected":
        print("处理失败：该候选已被拒绝。", file=sys.stderr)
        return 1

    try:
        agent = _create_agent()
        state, state_path = agent.research_official_url(document.url, save_state=True)
        if state.event_fact is None:
            raise ValueError("事实提取未返回 EventFact")
        fact_id = database.save_event_fact(document.id, state.event_fact)
        database.update_status(document.id, "processed", None)
    except Exception as exc:
        database.update_status(document.id, "failed", str(exc))
        print(f"处理失败：{exc}", file=sys.stderr)
        return 1

    print(f"候选 {document.id} 已处理，event_fact ID：{fact_id}")
    if state_path:
        print(f"state JSON：{state_path}")
    return 0


def _run_extract(args: argparse.Namespace) -> int:
    """按候选 ID 调用现有事实链路，不写数据库或改变候选状态。"""
    from .storage import Database

    document = Database().get_document(args.document_id)
    if document is None:
        print(f"提取失败：未找到候选文件 ID：{args.document_id}", file=sys.stderr)
        return 1

    print(f"候选 ID：{document.id}")
    print(f"候选标题：{document.title}")
    print(f"候选状态：{document.status}")
    print(f"候选 URL：{document.url}")
    if document.status == "rejected":
        print(
            "注意：该候选当前状态为 rejected，本次仅执行验证，不修改状态。",
            file=sys.stderr,
        )

    try:
        agent = _create_agent()
        state, state_path = agent.research_official_url(
            document.url,
            save_state=True,
        )
        if state.event_fact is None:
            raise ValueError("事实提取未返回 EventFact")
    except Exception as exc:
        print(f"提取失败：{exc}", file=sys.stderr)
        return 1

    source = state.source_document
    if source:
        print(f"来源验证：{source.verification_status}")
        print(f"最终 URL：{source.final_url}")
        print(f"是否重定向：{'是' if source.redirected else '否'}")
        print(f"内容类型：{source.content_type}")
        print(f"正文长度：{len(source.content)}")
    print("LLM 调用：成功")
    print("提取结果：")
    print(json.dumps(asdict(state.event_fact), ensure_ascii=False, indent=2))
    if state_path:
        print(f"state JSON：{state_path}")
    return 0


def _run_reject(args: argparse.Namespace) -> int:
    from .storage import Database

    try:
        Database().update_status(args.document_id, "rejected", None)
    except Exception as exc:
        print(f"拒绝失败：{exc}", file=sys.stderr)
        return 1
    print(f"候选 {args.document_id} 已标记为 rejected。")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "discover":
        return _run_discover(args)
    if args.command == "search-web":
        return _run_search_web(args)
    if args.command == "media-search":
        return _run_media_search(args)
    if args.command == "topic-brief":
        return _run_topic_brief(args)
    if args.command == "list":
        return _run_list()
    if args.command == "brief":
        return _run_brief(args)
    if args.command == "extract":
        return _run_extract(args)
    if args.command == "process":
        return _run_process(args)
    if args.command == "reject":
        return _run_reject(args)
    return _run_url(args)


if __name__ == "__main__":
    raise SystemExit(main())
