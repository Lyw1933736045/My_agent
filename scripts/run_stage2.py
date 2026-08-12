"""从 SQLite 的 Stage 1 快照执行一次 Stage 2 分析。"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import replace
from difflib import SequenceMatcher

from ..agent import FinancialMediaAgent
from ..run_repository import RunRepository
from ..state import RunState
from ..tools.media_models import MediaCandidate, MediaDocument
from ..tools.text_chunking import select_chunks, split_text
from ..utils.config import PROJECT_ROOT, Settings


def _group(row: dict) -> str:
    if row.get("source_group"):
        return row["source_group"]
    url = row.get("url", "").lower()
    return "social_media" if any(host in url for host in ("youtube.com", "weibo.com", "zhihu.com")) else "news_media"


def _candidate(row: dict) -> MediaCandidate:
    return MediaCandidate(
        title=row["title"], url=row["url"], source_name=row["source"],
        published_at=row.get("published_at"), snippet=row.get("snippet", ""),
        discovered_by=(row.get("provider") or "unknown",),
        source_group=_group(row), query=row.get("query"),
    )


def _chunks(content: str, topic: str, query: str) -> str:
    if len(content) <= 6000:
        return content
    pieces = split_text(content, chunk_size=1500, overlap=200)
    selected = select_chunks(pieces, topic=topic, queries=[query], top_k=5)
    return "\n\n".join(selected)


def _duplicate_key(content: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", content).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    repository = RunRepository(PROJECT_ROOT / "data" / "my_agent.db")
    record = repository.get(args.run_id)
    if record is None:
        parser.error("run_id 不存在")
    rows = repository.list_candidates(args.run_id)
    if not rows:
        parser.error("该 run 没有 Stage 1 候选")

    agent = FinancialMediaAgent(Settings())
    candidates = [_candidate(row) for row in rows]
    decisions = agent.candidate_filter_node.run({
        "stage": "metadata", "topic": record.topic,
        "queries": record.approved_queries or [record.query],
        "candidates": candidates, "model_min_score": 50,
    })
    accepted = []
    for row, candidate, decision in zip(rows, candidates, decisions):
        if decision.relevant:
            repository.update_candidate_analysis(row["id"], "pending", decision.reason)
            accepted.append((row, candidate))
        else:
            repository.update_candidate_analysis(row["id"], "rejected", decision.reason)
    print(f"标题摘要初筛：保留 {len(accepted)}，筛掉 {len(rows) - len(accepted)}")

    documents = []
    for row, candidate in accepted:
        try:
            result = agent.reader.read(candidate.url)
            repository.update_candidate_fetch(
                args.run_id, candidate.url, content=result.content,
                fetch_status="success", final_url=result.final_url,
                content_type=result.content_type, fetched_at=result.fetched_at,
            )
            documents.append((row, MediaDocument(
                candidate=candidate, final_url=result.final_url,
                fetched_at=result.fetched_at, content_type=result.content_type,
                content=_chunks(result.content, record.topic, record.query),
            )))
        except Exception as exc:
            repository.update_candidate_fetch(
                args.run_id, candidate.url, fetch_status="failed", fetch_error=str(exc),
            )
            repository.update_candidate_analysis(row["id"], "fetch_failed", str(exc))
    print(f"正文读取：成功 {len(documents)}，失败 {len(accepted) - len(documents)}")

    unique = []
    fingerprints: dict[str, tuple[dict, MediaDocument]] = {}
    for row, document in documents:
        key = _duplicate_key(document.content)
        duplicate = next((item for item in unique if SequenceMatcher(
            None, item[1].content[:12000], document.content[:12000]
        ).ratio() >= 0.92), None)
        if key in fingerprints or duplicate is not None:
            original = fingerprints.get(key, duplicate)[0] if key in fingerprints else duplicate[0]
            repository.update_candidate_analysis(row["id"], "duplicate", "正文与其他候选高度相似", original["id"])
            continue
        fingerprints[key] = (row, document)
        unique.append((row, document))
    print(f"正文去重：保留 {len(unique)}，重复 {len(documents) - len(unique)}")

    relevant_docs = []
    content_decisions = agent.candidate_filter_node.run({
        "stage": "content", "topic": record.topic,
        "queries": record.approved_queries or [record.query],
        "documents": [document for _, document in unique],
        "max_content_chars": 7500, "model_min_score": 60,
    })
    for (row, document), decision in zip(unique, content_decisions):
        if decision.relevant:
            repository.update_candidate_analysis(row["id"], "accepted", decision.reason)
            relevant_docs.append(document)
        else:
            repository.update_candidate_analysis(row["id"], "rejected", decision.reason)
    print(f"正文复核：保留 {len(relevant_docs)}，筛掉 {len(unique) - len(relevant_docs)}")

    if not relevant_docs:
        repository.fail(args.run_id, "Stage 2 没有通过正文复核的文章")
        return 1
    insights = agent.media_node.run(relevant_docs)
    media = [item.__dict__ for item in insights if item.source_group != "social_media"]
    social = [item.__dict__ for item in insights if item.source_group == "social_media"]
    brief = agent.brief_node.run({
        "query": record.query, "topic": record.topic,
        "media_insights": media, "social_insights": social,
    })
    repository.complete(args.run_id, brief)
    print(f"Stage 2 完成：最终采用 {len(insights)} 篇")
    print(brief)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
