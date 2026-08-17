"""多源金融媒体研究命令行入口。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence
from uuid import uuid4

from .agent import FinancialMediaAgent
from .evaluation.snapshot import write_snapshot
from .run_repository import RunRepository
from .utils.config import Settings


COMMANDS = {"media-search", "topic-brief"}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多源金融政策与市场热点发现")
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser(
        "media-search", help="检索 NewsNow、RSS 与 Tavily 候选"
    )
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=None)
    brief = subparsers.add_parser(
        "topic-brief", help="检索 NewsNow、RSS 与 Tavily 并生成主题简报"
    )
    brief.add_argument("--query", required=True)
    brief.add_argument("--media-limit", type=int, default=None)
    brief.add_argument("--no-save", action="store_true")
    brief.add_argument("--evaluation-case", type=Path, default=None)
    return parser.parse_args(list(sys.argv[1:] if argv is None else argv))


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _print_discovery(state) -> None:
    result = state.discovery
    if result is None:
        return
    stats = result.stats
    print(f"主题：{state.topic}")
    for provider in ("newsnow", "rss", "tavily"):
        key = f"{provider}_count"
        if key in stats:
            detail = ""
            failed = stats.get(f"{provider}_failed_sources")
            if failed is not None:
                detail += f"，失败源 {failed} 个"
            if provider == "newsnow":
                detail += (
                    f"，success {stats.get('newsnow_success_responses', 0)} 个"
                    f"，cache {stats.get('newsnow_cache_responses', 0)} 个"
                )
            print(f"{provider}: {stats[key]} 条{detail}")
    print(
        f"合计 {stats['fetched_count']} 条；时间过滤 {stats['time_filtered_count']} 条；"
        f"URL 重复 {stats['url_duplicates']} 条；标题重复 "
        f"{stats['title_duplicates']} 条；标题相关 {stats['relevant_count']} 条；"
        f"最终候选 {stats['selected_count']} 条。"
    )
    for provider, error in result.errors.items():
        print(f"{provider} 失败：{error}", file=sys.stderr)


def _run_media_search(args: argparse.Namespace) -> int:
    try:
        state = FinancialMediaAgent(
            Settings(), progress=_progress, enabled_sources={"newsnow", "rss", "tavily"}
        ).discover(
            args.query, args.limit
        )
    except Exception as exc:
        print(f"媒体检索失败：{exc}", file=sys.stderr)
        return 1
    _print_discovery(state)
    print("来源\t来源分组\t发现方式\t匹配词\t发布时间\t标题\tURL")
    for candidate in state.discovery.candidates:
        print(
            f"{candidate.source_name}\t{candidate.source_group}\t"
            f"{','.join(candidate.discovered_by)}\t{candidate.query or '-'}\t"
            f"{candidate.published_at or '-'}\t{candidate.title}\t{candidate.url}"
        )
    return 0


def _run_topic_brief(args: argparse.Namespace) -> int:
    settings = Settings()
    agent = FinancialMediaAgent(
        settings, progress=_progress, enabled_sources={"newsnow", "rss", "tavily"}
    )
    run_id = None
    repository = None
    try:
        state = agent.create_plan(args.query)
        run_id = uuid4().hex
        case_id = uuid4().hex
        case_key = f"case-{case_id[:8]}"
        repository = RunRepository(settings.DATABASE_URL)
        repository.create_case(
            case_id=case_id,
            case_key=case_key,
            query=state.query,
            topic=state.topic,
        )
        repository.create(
            run_id=run_id,
            query=state.query,
            topic=state.topic,
            tavily_queries=list(state.tavily_queries),
            newsnow_rss_core=list(state.newsnow_rss_core),
            newsnow_rss_support=list(state.newsnow_rss_support),
            weibo_query=state.weibo_query,
            enabled_sources={"newsnow", "rss", "tavily"},
            parent_event_id=case_id,
        )
        repository.approve(run_id, list(state.tavily_queries))
        state = agent.discover_from_plan(state, args.media_limit)
        state = agent.complete(state)
    except Exception as exc:
        if repository is not None and run_id is not None:
            repository.fail(run_id, str(exc))
        print(f"主题简报失败：{exc}", file=sys.stderr)
        return 1
    _print_discovery(state)
    print(
        f"正文读取成功 {state.read_success_count}/{state.read_attempted_count}；"
        f"正文高相关 {state.relevant_documents_count} 条。"
    )
    print(state.brief)
    candidates = state.discovery.raw_candidates or state.discovery.candidates
    repository.save_candidates(run_id, [
        {
            "title": item.title,
            "url": item.url,
            "snippet": item.snippet,
            "source": item.source_name,
            "provider": item.discovered_by[0] if item.discovered_by else "unknown",
            "source_group": item.source_group,
            "query": item.query,
            "published_at": item.published_at,
        }
        for item in candidates if item.url.strip()
    ])
    if state.discovery is not None:
        repository.save_source_results(run_id, [
            {
                "provider": item.provider,
                "name": item.name,
                "ok": item.ok,
                "detail": item.detail,
            }
            for item in state.discovery.sources
        ])
        repository.save_retrieval_reflection(run_id, state.retrieval_reflection)
    for document in state.selected_documents:
        repository.update_candidate_fetch(
            run_id, document.candidate.url,
            raw_content=document.raw_content or document.content,
            fetch_status="fetched", final_url=document.final_url,
            content_type=document.content_type, fetched_at=document.fetched_at,
        )
    for document, decision in zip(state.selected_documents, state.content_decisions):
        for row in repository.list_candidates(run_id):
            if row.get("url") == document.candidate.url:
                repository.update_candidate_analysis(
                    row["id"],
                    "accepted" if decision.relevant else "rejected",
                    decision.reason,
                )
                break
    repository.complete(run_id, state.brief, report_data=state.brief_data)
    print(f"PostgreSQL 事件已保存：{run_id}", file=sys.stderr)
    if args.evaluation_case:
        documents_path, report_path = write_snapshot(state, args.evaluation_case)
        print(f"评测材料：{documents_path}；{report_path}")
    if not args.no_save:
        output_path = agent.save_brief(state.brief)
        agent.save_retrieval_reflection(state.retrieval_reflection, output_path)
        print(f"\n简报文件：{output_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "media-search":
        return _run_media_search(args)
    return _run_topic_brief(args)


if __name__ == "__main__":
    raise SystemExit(main())
