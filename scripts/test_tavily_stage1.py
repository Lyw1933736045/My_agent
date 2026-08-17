"""运行 Tavily Stage 1，并把规范化 URL 去重后的候选保存到 PostgreSQL。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from ..llms import LLMClient
from ..nodes.query_plan_node import QueryPlanNode
from ..run_repository import RunRepository
from ..tools.media_discovery import MediaDiscovery
from ..tools.tavily_provider import TavilyMediaProvider
from ..utils.config import Settings
from ..utils.media_sources import load_media_sources


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--query")
    source.add_argument("--case", type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit 必须是正整数")

    query = args.query
    case_id = None
    if args.case is not None:
        case_file = args.case / "case.yaml" if args.case.is_dir() else args.case
        case_data = yaml.safe_load(case_file.read_text(encoding="utf-8")) or {}
        query = str(case_data.get("query") or "").strip()
        case_id = str(case_data.get("id") or case_file.parent.name)
    if not query:
        parser.error("query 不能为空")

    settings = Settings()
    api_key = settings.TAVILY_API_KEY.strip()
    if not api_key:
        parser.error("未配置 TAVILY_API_KEY，请填写 My_agent/.env")

    config = load_media_sources()["tavily"]
    llm = LLMClient(
        api_key=settings.QUERY_ENGINE_API_KEY,
        model_name=settings.QUERY_ENGINE_MODEL_NAME,
        base_url=settings.QUERY_ENGINE_BASE_URL,
        timeout=settings.LLM_REQUEST_TIMEOUT,
    )
    plan = QueryPlanNode(llm).run({"query": query})
    tavily_query = plan["tavily_queries"][0]

    trusted_domains = list(config.get("trusted_media_domains", []))
    for domain in list(config.get("domestic_finance_domains", [])) + list(
        config.get("overseas_finance_domains", [])
    ):
        if domain not in trusted_domains:
            trusted_domains.append(domain)
    provider = TavilyMediaProvider(
        api_key,
        search_rounds=int(config.get("search_rounds", 2)),
        max_results_per_query=(
            args.limit
            if args.limit is not None
            else int(config.get("max_results_per_query", 5))
        ),
        targeted_search_enabled=bool(config.get("targeted_search_enabled", True)),
        targeted_max_results=(
            args.limit
            if args.limit is not None
            else int(config.get("targeted_max_results", 10))
        ),
        trusted_media_domains=trusted_domains,
        search_depth=str(config.get("search_depth", "basic")),
        days=int(config["days"]) if config.get("days") is not None else None,
    )
    discovery = MediaDiscovery({"tavily": provider}).run(
        [tavily_query],
        tavily_queries=[tavily_query],
        topic=plan["topic"],
        progress=print,
    )
    unique = discovery.raw_candidates

    run_id = f"tavily-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    repository = RunRepository(settings.DATABASE_URL)
    repository.create(
        run_id=run_id,
        query=query,
        topic=plan["topic"],
        tavily_queries=[tavily_query],
        newsnow_rss_core=plan["newsnow_rss_core"],
        newsnow_rss_support=plan["newsnow_rss_support"],
        enabled_sources={"tavily"},
    )
    repository.approve(run_id, [tavily_query])
    saved = repository.save_candidates(run_id, [{
        "title": item.title, "url": item.url, "search_snippet": item.snippet,
        "source": item.source_name, "provider": "tavily",
        "source_group": item.source_group,
        "query": item.query or tavily_query, "published_at": item.published_at,
        "appearances": list(item.metadata.get("appearances") or []),
    } for item in unique])

    print(f"\nCase: {case_id or '-'}")
    print(f"原始输入: {query}")
    print(f"Tavily Query: {tavily_query}")
    for stats in provider.round_stats:
        print(
            f"第 {stats['round']} 轮：返回 {stats['returned_count']} 条，"
            f"轮内 URL 去重后 {stats['unique_count']} 条，"
            f"相对前轮新增 {stats['new_unique_count']} 条"
        )
    print(f"\nStage 1 完成，run_id: {run_id}")
    print(
        f"两轮合并后 {len(unique)} 条；数据库新增 {saved['new_count']} 条；"
        f"事件内累计 {saved['total_unique_count']} 条"
    )
    print("正文读取: 未执行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
