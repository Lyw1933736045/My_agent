"""手动运行 WeiboProvider；不会被单元测试自动发现。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


# 兼容直接执行 `python3 My_agent/scripts/manual_weibo_test.py`。
PROJECTS_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECTS_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECTS_ROOT))

from My_agent.agent import FinancialMediaAgent
from My_agent.tools.weibo_provider import WeiboProvider
from My_agent.utils.config import PROJECT_ROOT, Settings


DEFAULT_COOKIE_FILE = PROJECT_ROOT.parent / "weibo_cookie.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="单独测试微博关键词规划与抓取，不运行其他媒体 Provider。"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--question",
        help="自然语言研究问题；会先调用 LLM 生成 balanced 微博搜索词。",
    )
    source.add_argument(
        "--keyword",
        help="直接指定微博搜索词；不会调用 LLM。",
    )
    parser.add_argument(
        "--cookie-file",
        type=Path,
        default=DEFAULT_COOKIE_FILE,
        help=f"外部 Cookie 文件，默认：{DEFAULT_COOKIE_FILE}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="结果 JSON 路径；默认写入 My_agent/reports。",
    )
    parser.add_argument(
        "--target-posts",
        type=int,
        default=20,
        help="累计达到该数量后停止继续翻页，默认20。",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        choices=(1, 2, 3),
        default=3,
        help="搜索页请求上限，默认3。",
    )
    parser.add_argument(
        "--with-comments",
        action="store_true",
        help="显式开启评论抓取；默认关闭。",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="确认允许真实 LLM/微博网络请求；缺少此参数时程序立即退出。",
    )
    return parser.parse_args()


def build_weibo_query(args: argparse.Namespace) -> tuple[str | None, str]:
    if args.keyword:
        return None, " ".join(args.keyword.split())
    question = " ".join((args.question or "").split())
    agent = FinancialMediaAgent(Settings())
    plan = agent.create_plan(question)
    query = " ".join(plan.provider_queries.get("weibo", "").split())
    if not query:
        raise ValueError("LLM 未生成可用的微博 balanced 搜索词")
    return question, query


def default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "reports" / f"weibo_standalone_{timestamp}.json"


def main() -> int:
    args = parse_args()
    if not args.allow_live:
        raise SystemExit(
            "未执行：该脚本会产生真实网络请求。确认后请增加 --allow-live。"
        )
    if args.target_posts < 1:
        raise SystemExit("--target-posts 必须是正整数")

    question, weibo_query = build_weibo_query(args)
    provider = WeiboProvider(
        cookie_file=str(args.cookie_file),
        target_posts=args.target_posts,
        max_search_pages=args.max_pages,
        request_interval_min=4,
        request_interval_max=8,
        comments_enabled=args.with_comments,
        max_comment_posts=2,
        comment_interval_min=5,
        comment_interval_max=10,
        timeout=20,
        trust_env_proxy=False,
    )
    candidates = provider.search([weibo_query], progress=print)
    posts = provider.raw_results
    output = {
        "input_question": question,
        "weibo_query": weibo_query,
        "http_request_count": provider.request_count,
        "parsed_posts": len(posts),
        "returned_candidates": len(candidates),
        "comments_saved": sum(len(post.get("comments", [])) for post in posts),
        "diagnostics": {
            "successful_sources": provider.diagnostics.successful_sources,
            "failed_sources": provider.diagnostics.failed_sources,
            "status_counts": provider.diagnostics.status_counts,
        },
        "posts": posts,
    }
    output_path = args.output or default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "weibo_query": weibo_query,
        "http_request_count": provider.request_count,
        "parsed_posts": len(posts),
        "comments_saved": output["comments_saved"],
        "result_file": str(output_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
