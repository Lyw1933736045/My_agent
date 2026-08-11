"""把已有 weibo_standalone JSON 的全部帖子交给社媒简报链路。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

PROJECTS_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECTS_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECTS_ROOT))

from My_agent.llms import LLMClient
from My_agent.nodes import BriefNode, MediaNode
from My_agent.tools.media_models import MediaCandidate, MediaDocument
from My_agent.utils.config import PROJECT_ROOT, Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总已有微博JSON，不访问微博")
    parser.add_argument("input", type=Path, help="weibo_standalone JSON路径")
    parser.add_argument("--output", type=Path, help="简报输出Markdown路径")
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    posts = data.get("posts") or []
    if not posts:
        raise SystemExit("输入JSON没有posts")

    settings = Settings()
    llm = LLMClient(
        api_key=settings.QUERY_ENGINE_API_KEY,
        model_name=settings.QUERY_ENGINE_MODEL_NAME,
        base_url=settings.QUERY_ENGINE_BASE_URL,
        timeout=settings.LLM_REQUEST_TIMEOUT,
    )
    documents = []
    for post in posts:
        text = str(post.get("text") or "").strip()
        if not text or not post.get("url"):
            continue
        candidate = MediaCandidate(
            title=text[:80],
            url=str(post["url"]),
            source_name=f"微博｜{post.get('user_name') or '未知账号'}",
            published_at=post.get("published_at"),
            snippet=text,
            discovered_by=("weibo",),
            source_group="social_media",
            query=data.get("weibo_query"),
            guid=f"weibo:{post.get('wid')}",
            metadata={
                key: value
                for key, value in post.items()
                if key not in {"text", "url", "published_at"}
            },
        )
        documents.append(MediaDocument(
            candidate=candidate,
            final_url=candidate.url,
            fetched_at=str(post.get("fetched_at") or ""),
            content_type="text/plain",
            content=text,
        ))

    insights = MediaNode(llm).run(documents)
    brief = BriefNode(llm).run({
        "query": data.get("input_question") or data.get("weibo_query") or "微博社交媒体简报",
        "topic": data.get("input_question") or data.get("weibo_query") or "微博社交媒体简报",
        "media_insights": [],
        "social_insights": [asdict(item) for item in insights],
    })
    output = args.output or (
        PROJECT_ROOT / "reports" / f"weibo_brief_from_{args.input.stem}.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(brief.strip() + "\n", encoding="utf-8")
    print(json.dumps({
        "input_posts": len(posts),
        "documents_sent_to_llm": len(documents),
        "insights": len(insights),
        "comments_sent_to_llm": 0,
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
