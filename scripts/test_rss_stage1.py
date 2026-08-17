"""只运行 RSS Stage 1，并把候选及正文状态保存到 PostgreSQL。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv

from ..agent import FinancialMediaAgent
from ..run_repository import RunRepository
from ..tools.media_relevance import is_media_candidate_relevant
from ..tools.rss_provider import RSSProvider
from ..utils.config import ENV_FILE, Settings
from ..utils.media_sources import load_media_sources, resolve_feed_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True,
                        help="仅作为本次快照标记，不会传给普通 RSS Feed 搜索")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须是正整数")
    load_dotenv(ENV_FILE)
    settings = Settings()
    plan = FinancialMediaAgent(settings).create_plan(args.query)
    config = load_media_sources()["rss"]
    feeds = []
    for feed in config["feeds"]:
        if not feed.get("enabled", True) or not feed.get("url"):
            continue
        item = dict(feed)
        item["url"] = resolve_feed_url(str(item["url"]))
        feeds.append(item)
    provider = RSSProvider(
        feeds=feeds,
        timeout=float(config["timeout_seconds"]),
        max_age_days=int(config["max_age_days"]),
        max_content_bytes=int(config["max_content_bytes"]),
        default_max_items=int(config["default_max_items"]),
        request_interval_min=float(config["request_interval_min_seconds"]),
        request_interval_max=float(config["request_interval_max_seconds"]),
        max_retries=int(config["max_retries"]),
        retry_wait_min=float(config["retry_wait_min_seconds"]),
        retry_wait_max=float(config["retry_wait_max_seconds"]),
    )
    candidates = provider.search([args.query], limit=args.limit, progress=print)
    candidates = [
        item for item in candidates
        if is_media_candidate_relevant(
            item.title, item.snippet, plan.newsnow_rss_core, plan.newsnow_rss_support, "rss"
        )
    ]
    unique, seen = [], set()
    for item in candidates:
        if item.url in seen:
            continue
        seen.add(item.url)
        unique.append(item)
        if len(unique) >= args.limit:
            break

    run_id = f"rss-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    repository = RunRepository(settings.DATABASE_URL)
    repository.create(run_id=run_id, query=args.query, topic=args.query,
                      tavily_queries=plan.tavily_queries,
                      newsnow_rss_core=plan.newsnow_rss_core,
                      newsnow_rss_support=plan.newsnow_rss_support)
    repository.approve(run_id, [args.query])
    repository.save_candidates(run_id, [{
        "title": item.title, "url": item.url, "snippet": item.snippet,
        "source": item.source_name, "provider": "rss",
        "source_group": item.source_group,
        "query": item.query or args.query, "published_at": item.published_at,
    } for item in unique])

    print(f"\nStage 1 完成，run_id: {run_id}")
    print(f"候选保存: {len(unique)} 条；正文读取: Stage 2 处理")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
