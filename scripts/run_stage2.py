"""从 PostgreSQL 的 Stage 1 候选执行一次 Stage 2 分析。"""

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
from ..tools.text_chunking import select_relevance_chunks, split_text
from ..utils.config import PROJECT_ROOT, Settings
from ..utils.media_sources import load_media_sources


def _group(row: dict) -> str:
    if row.get("source_group"):
        return row["source_group"]
    url = row.get("url", "").lower()
    return "social_media" if any(host in url for host in ("youtube.com", "weibo.com", "zhihu.com")) else "news_media"


def _candidate(row: dict) -> MediaCandidate:
    document_metadata = dict(row.get("document_metadata") or {})
    event_metadata = dict(row.get("event_metadata") or {})
    social_snapshot = dict(event_metadata.get("social_snapshot") or {})
    providers = tuple(row.get("providers") or [row.get("provider") or "unknown"])
    is_weibo = "weibo" in providers
    metadata = {**document_metadata, **social_snapshot}
    if is_weibo and row.get("content"):
        metadata.update({
            "content_ready": True,
            "prechecked_relevance": row.get("analysis_status") == "accepted",
            "relevance_score": row.get("relevance_score"),
            "relevance_reason": row.get("analysis_reason"),
            "fetched_at": row.get("fetched_at") or social_snapshot.get("captured_at"),
        })
    return MediaCandidate(
        title=row["title"], url=row["url"], source_name=row["source"],
        published_at=row.get("published_at"),
        snippet=social_snapshot.get("post_text") or row.get("content") or row.get("search_snippet", row.get("snippet", "")),
        discovered_by=providers,
        source_group=_group(row), query=row.get("query"),
        guid=f"weibo:{document_metadata.get('wid')}" if is_weibo and document_metadata.get("wid") else None,
        metadata=metadata,
    )


def _chunks(content: str, topic: str, core_terms: list[str], support_terms: list[str], top_k: int = 5) -> str:
    if len(content) <= 6000:
        return content
    pieces = split_text(content, chunk_size=1500, overlap=200)
    selected = select_relevance_chunks(
        pieces, topic=topic, core_terms=core_terms, support_terms=support_terms, top_k=top_k
    )
    return "\n\n".join(selected)


def _duplicate_key(content: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", content).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    repository = RunRepository(Settings().DATABASE_URL)
    record = repository.get(args.run_id)
    if record is None:
        parser.error("run_id 不存在")
    rows = repository.list_candidates(args.run_id)
    if not rows:
        parser.error("该 run 没有 Stage 1 候选")

    agent = FinancialMediaAgent(Settings())
    selection = load_media_sources()["selection"]
    candidates = [_candidate(row) for row in rows]
    # Stage 1 已完成标题/摘要本地预筛；Stage 2 直接读取数据库中的全部候选正文。
    accepted = list(zip(rows, candidates))
    core_terms = list(record.newsnow_rss_core or [])
    support_terms = list(record.newsnow_rss_support or [])
    for row, _candidate_item in accepted:
        if "weibo" not in _candidate_item.discovered_by:
            repository.update_candidate_analysis(
                row["id"], "pending", "Stage 1 已完成，进入正文读取"
            )
    print(f"Stage 1 数据库候选：全部进入正文读取，共 {len(accepted)} 条")

    documents = []
    for row, candidate in accepted:
        try:
            if "weibo" in candidate.discovered_by and candidate.metadata.get("content_ready"):
                content = candidate.metadata.get("post_text") or row.get("content") or candidate.snippet
                final_url = candidate.url
                fetched_at = str(candidate.metadata.get("fetched_at") or row.get("fetched_at") or "")
                content_type = "text/plain"
            else:
                result = agent.reader.read(candidate.url)
                content = result.content
                final_url = result.final_url
                fetched_at = result.fetched_at
                content_type = result.content_type
                repository.update_candidate_fetch(
                    args.run_id, candidate.url, content=content,
                    fetch_status="success", final_url=final_url,
                    content_type=content_type, fetched_at=fetched_at,
                )
            documents.append((row, MediaDocument(
                candidate=candidate, final_url=final_url,
                fetched_at=fetched_at, content_type=content_type,
                content=content, raw_content=content,
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
        if document.candidate.source_group == "social_media":
            unique.append((row, document))
            continue
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

    relevance_documents = [
        (row, replace(document, content=_chunks(
            document.raw_content or document.content,
            record.topic,
            core_terms,
            support_terms,
            int(selection.get("content_relevance_top_k", 5)),
        )))
        for row, document in unique
    ]
    relevant_docs = []
    article_pairs = [
        pair for pair in relevance_documents
        if not (
            "weibo" in pair[1].candidate.discovered_by
            and pair[1].candidate.metadata.get("prechecked_relevance")
        )
    ]
    content_decisions = agent.candidate_filter_node.run({
        "stage": "content", "topic": record.topic,
        "newsnow_rss_core": core_terms,
        "newsnow_rss_support": support_terms,
        "documents": [document for _, document in article_pairs],
        "max_content_chars": int(selection.get("content_filter_max_chars", 7500)),
        "model_min_score": int(selection.get("relevance_model_min_score", 60)),
    }) if article_pairs else []
    article_decisions = {
        document.candidate.url: decision
        for (_, document), decision in zip(article_pairs, content_decisions)
    }
    for row, document in relevance_documents:
        if "weibo" in document.candidate.discovered_by and document.candidate.metadata.get("prechecked_relevance"):
            repository.update_candidate_analysis(
                row["id"], "accepted",
                str(document.candidate.metadata.get("relevance_reason") or "微博已通过保存前相关性复核"),
                relevance_score=float(document.candidate.metadata.get("relevance_score") or 30),
            )
            relevant_docs.append(document)
            continue
        decision = article_decisions[document.candidate.url]
        if decision.relevant:
            repository.update_candidate_analysis(
                row["id"], "accepted", decision.reason,
                relevance_score=decision.score,
            )
            relevant_docs.append(document)
        else:
            repository.update_candidate_analysis(
                row["id"], "rejected", decision.reason,
                relevance_score=decision.score,
            )
    print(f"正文复核：保留 {len(relevant_docs)}，筛掉 {len(unique) - len(relevant_docs)}")

    if not relevant_docs:
        repository.fail(args.run_id, "Stage 2 没有通过正文复核的文章")
        return 1
    insights = agent.media_node.run(relevant_docs)
    media = [item.__dict__ for item in insights if item.source_group != "social_media"]
    social = [item.__dict__ for item in insights if item.source_group == "social_media"]
    brief_result = agent.brief_node.generate({
        "query": record.query, "topic": record.topic,
        "media_insights": media, "social_insights": social,
    })
    brief = brief_result.markdown
    repository.complete(args.run_id, brief, report_data=brief_result.data)
    print(f"Stage 2 完成：最终采用 {len(insights)} 篇")
    print(brief)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
