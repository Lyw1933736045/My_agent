"""只运行 NewsNow Stage 1，并把候选及正文状态保存到 PostgreSQL。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from uuid import uuid4

from ..agent import FinancialMediaAgent
from ..run_repository import RunRepository
from ..tools.media_relevance import is_media_candidate_relevant
from ..tools.newsnow_provider import NewsNowProvider
from ..utils.config import Settings
from ..utils.media_sources import load_media_sources


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=50,
                        help="最多保存并读取多少条候选")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须是正整数")

    settings = Settings()
    plan = FinancialMediaAgent(settings).create_plan(args.query)
    config = load_media_sources()["newsnow"]
    provider = NewsNowProvider(
        api_url=config["api_url"],
        sources=config["sources"],
        timeout=float(config["timeout_seconds"]),
        max_retries=int(config["max_retries"]),
        retry_wait_min=float(config["retry_wait_min_seconds"]),
        retry_wait_max=float(config["retry_wait_max_seconds"]),
        request_interval=float(config["request_interval_seconds"]),
    )
    candidates = provider.search([args.query], limit=args.limit, progress=print)
    candidates = [
        item for item in candidates
        if is_media_candidate_relevant(
            item.title, item.snippet, plan.newsnow_rss_core, plan.newsnow_rss_support, "newsnow"
        )
    ]
    unique = []
    seen_urls = set()
    for item in candidates:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        unique.append(item)
        if len(unique) >= args.limit:
            break
    candidates = unique

    run_id = f"newsnow-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    repository = RunRepository(settings.DATABASE_URL)
    repository.create(
        run_id=run_id,
        query=args.query,
        topic=args.query,
        tavily_queries=plan.tavily_queries,
        newsnow_rss_core=plan.newsnow_rss_core,
        newsnow_rss_support=plan.newsnow_rss_support,
    )
    repository.approve(run_id, [args.query])
    repository.save_candidates(run_id, [
        {
            "title": item.title,
            "url": item.url,
            "snippet": item.snippet,
            "source": item.source_name,
            "provider": "newsnow",
            "source_group": item.source_group,
            "query": item.query or args.query,
            "published_at": item.published_at,
        }
        for item in candidates
    ])

    print(f"\nStage 1 完成，run_id: {run_id}")
    print(f"候选保存: {len(candidates)} 条；正文读取: Stage 2 处理")
    print(f"查询候选: GET /api/v1/runs/{run_id}/candidates")
    print("PostgreSQL: DATABASE_URL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
