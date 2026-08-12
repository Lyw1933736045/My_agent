"""只运行 Tavily Stage 1，并把候选及正文状态保存到 SQLite。"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv

from ..run_repository import RunRepository
from ..tools.tavily_provider import TavilyMediaProvider
from ..utils.config import ENV_FILE, PROJECT_ROOT
from ..utils.media_sources import load_media_sources


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须是正整数")
    load_dotenv(ENV_FILE)
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        parser.error("未配置 TAVILY_API_KEY，请填写 My_agent/.env")

    config = load_media_sources()["tavily"]
    provider = TavilyMediaProvider(
        api_key,
        max_results_per_query=min(int(config.get("max_results_per_query", 5)), args.limit),
        search_depth=str(config.get("search_depth", "basic")),
        days=int(config["days"]) if config.get("days") is not None else None,
    )
    candidates = provider.search([args.query], limit=args.limit, progress=print)
    unique, seen = [], set()
    for item in candidates:
        if item.url in seen:
            continue
        seen.add(item.url)
        unique.append(item)
        if len(unique) >= args.limit:
            break

    run_id = f"tavily-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    repository = RunRepository(PROJECT_ROOT / "data" / "my_agent.db")
    repository.create(run_id=run_id, query=args.query, topic=args.query,
                      proposed_queries=[args.query], provider_queries={"tavily": args.query})
    repository.approve(run_id, [args.query])
    repository.save_candidates(run_id, [{
        "title": item.title, "url": item.url, "snippet": item.snippet,
        "source": item.source_name, "provider": "tavily",
        "source_group": item.source_group,
        "query": item.query or args.query, "published_at": item.published_at,
    } for item in unique])

    print(f"\nStage 1 完成，run_id: {run_id}")
    print(f"候选保存: {len(unique)} 条；正文读取: Stage 2 处理")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
