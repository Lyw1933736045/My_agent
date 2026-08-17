"""Case-scoped research Q&A over brief, structured insights, and raw documents."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from ..prompts.qa_prompts import SYSTEM_PROMPT_QA_ANSWER, SYSTEM_PROMPT_QA_RERANK
from ..run_repository import CaseRecord, RunRepository
from ..utils.dedup import canonical_url
from ..utils.text_processing import extract_json
from .text_chunking import split_text


_STOP_PHRASES = (
    "我想知道", "请问", "帮我", "请帮我", "请", "总结一下", "总结", "概括一下",
    "概括", "有哪些", "有何", "相关的", "相关新闻", "相关信息", "一下", "如何",
)


@dataclass(frozen=True)
class QACitation:
    source_id: str
    claim: str = ""
    title: str = ""
    source_name: str = ""
    url: str = ""


@dataclass(frozen=True)
class QAEvidence:
    source_id: str
    quote: str
    title: str = ""
    url: str = ""
    chunk_id: str | None = None


@dataclass(frozen=True)
class QAResult:
    case_id: str
    mode: str
    answer: str
    citations: list[QACitation] = field(default_factory=list)
    evidence: list[QAEvidence] = field(default_factory=list)
    retrieved_count: int = 0


def _compact(value: str) -> str:
    value = str(value or "").casefold()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value)


def _query_terms(question: str) -> list[str]:
    value = " ".join(str(question or "").split())
    for phrase in sorted(_STOP_PHRASES, key=len, reverse=True):
        value = value.replace(phrase, " ")
    values = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9][a-zA-Z0-9_+-]{1,}", value)
    terms = list(dict.fromkeys(item.casefold() for item in values if item.strip()))
    return terms or [_compact(question)]


def _lexical_score(question: str, text: str, title: str = "") -> int:
    terms = _query_terms(question)
    body = str(text or "").casefold()
    heading = str(title or "").casefold()
    score = 0
    for term in terms:
        if not term:
            continue
        occurrences = body.count(term)
        if occurrences:
            score += min(occurrences, 4) * (3 if len(term) >= 3 else 2)
        if term in heading:
            score += 5
    return score


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class CaseQAService:
    """One bounded retrieval-and-answer pass for a selected case."""

    def __init__(self, repository: RunRepository, llm_client) -> None:
        self.repository = repository
        self.llm_client = llm_client

    def answer(
        self,
        case: CaseRecord,
        question: str,
        mode: str,
    ) -> QAResult:
        if mode not in {"fast", "analysis", "deep"}:
            raise ValueError("问答模式必须是 fast、analysis 或 deep")
        question = " ".join(str(question or "").split())
        if len(question) < 2:
            raise ValueError("问题至少需要两个字符")

        sources = self._source_catalog(case.report_data)
        if mode == "fast":
            context, retrieval_evidence = self._fast_context(case, question, sources)
        elif mode == "analysis":
            context, retrieval_evidence = self._analysis_context(case, question, sources)
        else:
            context, retrieval_evidence = self._deep_context(case, question, sources)
        if not context:
            raise ValueError("当前案例没有可用于该模式的材料")

        payload = {
            "case_id": case.case_id,
            "case_topic": case.topic,
            "question": question,
            "mode": mode,
            "source_catalog": list(sources.values()),
            "evidence": context,
        }
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_QA_ANSWER,
            json.dumps(payload, ensure_ascii=False),
        )
        parsed = extract_json(response)
        if isinstance(parsed, dict):
            answer = str(parsed.get("answer") or "").strip()
            raw_citations = parsed.get("citations") or []
            raw_evidence = parsed.get("evidence_used") or []
        else:
            answer = str(response or "").strip()
            raw_citations = []
            raw_evidence = []
        if not answer:
            raise ValueError("问答模型没有返回有效回答")
        return QAResult(
            case_id=case.case_id,
            mode=mode,
            answer=answer,
            citations=self._normalize_citations(raw_citations, sources),
            evidence=self._normalize_evidence(raw_evidence, sources, context)
            or list(retrieval_evidence),
            retrieved_count=len(context),
        )

    @staticmethod
    def _source_catalog(report_data: dict) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for item in report_data.get("sources", []) if isinstance(report_data, dict) else []:
            if not isinstance(item, dict) or not item.get("id") or not item.get("url"):
                continue
            source = {
                "source_id": str(item["id"]),
                "title": str(item.get("title") or "未命名来源"),
                "source_name": str(item.get("source_name") or ""),
                "url": str(item.get("url") or ""),
                "published_at": item.get("published_at"),
                "source_type": item.get("source_type", "media"),
            }
            result[source["source_id"]] = source
        return result

    def _source_for_item(
        self,
        item: dict,
        sources: dict[str, dict],
        fallback_id: str,
    ) -> dict:
        url = str(item.get("url") or "").strip()
        canonical = canonical_url(url) if url else ""
        for source in sources.values():
            if canonical and canonical_url(str(source.get("url") or "")) == canonical:
                return source
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        source_id = str(item.get("source_id") or metadata.get("source_id") or fallback_id)
        source = {
            "source_id": source_id,
            "title": str(item.get("title") or "未命名来源"),
            "source_name": str(item.get("source_name") or ""),
            "url": url,
            "published_at": item.get("published_at"),
            "source_type": "social" if item.get("source_group") == "social_media" else "media",
        }
        sources.setdefault(source_id, source)
        return source

    def _fast_context(
        self,
        case: CaseRecord,
        question: str,
        sources: dict[str, dict],
    ) -> tuple[list[dict], list[QAEvidence]]:
        data = case.report_data if isinstance(case.report_data, dict) else {}
        selected = {
            "title": data.get("title") or case.topic,
            "executive_summary": data.get("executive_summary") or [],
            "official": data.get("official") or {},
            "media": data.get("media") or {},
            "public_opinion": data.get("public_opinion") or {},
            "timeline": data.get("timeline") or [],
            "key_metrics": data.get("key_metrics") or [],
            "synthesis": data.get("synthesis") or {},
        }
        if not selected["title"] and not any(selected.values()):
            return [], []
        return [{"kind": "brief", "content": selected}], []

    def _analysis_context(
        self,
        case: CaseRecord,
        question: str,
        sources: dict[str, dict],
    ) -> tuple[list[dict], list[QAEvidence]]:
        prepared = self.repository.aggregate_case_prepared_analysis(case.case_id) or {}
        items = [
            *list(prepared.get("media_insights") or []),
            *list(prepared.get("social_insights") or []),
        ]
        ranked = []
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            source = self._source_for_item(item, sources, f"I{index:02d}")
            score = _lexical_score(
                question,
                _json_text({
                    "reported_facts": item.get("reported_facts"),
                    "interpretations": item.get("interpretations"),
                    "named_views": item.get("named_views"),
                    "timeline_events": (item.get("metadata") or {}).get("timeline_events"),
                }),
                str(item.get("title") or ""),
            )
            ranked.append((score, item, source))
        ranked.sort(key=lambda value: (-value[0], str(value[1].get("published_at") or "")),)
        selected = ranked[:12]
        context = []
        for _, item, source in selected:
            context.append({
                "kind": "structured_insight",
                "source_id": source["source_id"],
                "title": item.get("title"),
                "source_name": item.get("source_name"),
                "published_at": item.get("published_at"),
                "reported_facts": item.get("reported_facts") or [],
                "interpretations": item.get("interpretations") or [],
                "affected_parties": item.get("affected_parties") or [],
                "risks_or_disagreements": item.get("risks_or_disagreements") or [],
                "statistics": item.get("statistics") or [],
                "named_views": item.get("named_views") or [],
                "timeline_events": (item.get("metadata") or {}).get("timeline_events") or [],
            })
        return context, []

    def _deep_context(
        self,
        case: CaseRecord,
        question: str,
        sources: dict[str, dict],
    ) -> tuple[list[dict], list[QAEvidence]]:
        rows = self.repository.list_case_candidates(case.case_id)
        candidates: list[dict] = []
        for row_index, row in enumerate(rows, 1):
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            source = self._source_for_item(row, sources, f"D{row_index:02d}")
            chunks = split_text(content, chunk_size=1200, overlap=150)
            for chunk_index, chunk in enumerate(chunks):
                score = _lexical_score(question, chunk, str(row.get("title") or ""))
                candidates.append({
                    "chunk_id": f"D{row_index:02d}-{chunk_index:02d}",
                    "source_id": source["source_id"],
                    "title": row.get("title") or "未命名来源",
                    "source_name": row.get("source") or "",
                    "url": row.get("url") or "",
                    "published_at": row.get("published_at"),
                    "content": chunk,
                    "lexical_score": score,
                })
        candidates.sort(key=lambda item: (-int(item["lexical_score"]), str(item.get("published_at") or "")))
        candidates = candidates[:20]
        if not candidates:
            return [], []
        candidates = self._rerank(question, candidates)
        selected = candidates[:8]
        context = [
            {
                "kind": "raw_document_chunk",
                "chunk_id": item["chunk_id"],
                "source_id": item["source_id"],
                "title": item["title"],
                "source_name": item["source_name"],
                "published_at": item["published_at"],
                "content": item["content"],
            }
            for item in selected
        ]
        evidence = [QAEvidence(
            source_id=item["source_id"],
            quote=str(item["content"])[:280],
            title=str(item["title"]),
            url=str(item["url"]),
            chunk_id=str(item["chunk_id"]),
        ) for item in selected]
        return context, evidence

    def _rerank(self, question: str, candidates: list[dict]) -> list[dict]:
        payload = {
            "question": question,
            "candidates": [
                {
                    "chunk_id": item["chunk_id"],
                    "title": item["title"],
                    "source_name": item["source_name"],
                    "preview": str(item["content"])[:420],
                    "lexical_score": item["lexical_score"],
                }
                for item in candidates
            ],
        }
        try:
            response = self.llm_client.invoke(
                SYSTEM_PROMPT_QA_RERANK,
                json.dumps(payload, ensure_ascii=False),
            )
            parsed = extract_json(response)
            ranked_ids = parsed.get("ranked_chunk_ids") if isinstance(parsed, dict) else None
            if not isinstance(ranked_ids, list):
                return candidates
            by_id = {item["chunk_id"]: item for item in candidates}
            ordered = [by_id[item] for item in ranked_ids if item in by_id]
            ordered.extend(item for item in candidates if item not in ordered)
            return ordered
        except Exception:
            return candidates

    @staticmethod
    def _normalize_citations(raw: Any, sources: dict[str, dict]) -> list[QACitation]:
        result = []
        seen = set()
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "")
            if source_id not in sources or source_id in seen:
                continue
            seen.add(source_id)
            source = sources[source_id]
            result.append(QACitation(
                source_id=source_id,
                claim=str(item.get("claim") or ""),
                title=str(source.get("title") or ""),
                source_name=str(source.get("source_name") or ""),
                url=str(source.get("url") or ""),
            ))
        return result

    @staticmethod
    def _normalize_evidence(
        raw: Any,
        sources: dict[str, dict],
        context: list[dict],
    ) -> list[QAEvidence]:
        context_by_source = {}
        for item in context:
            source_id = str(item.get("source_id") or "")
            context_by_source.setdefault(source_id, item)
        result = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "")
            if source_id not in sources:
                continue
            source = sources[source_id]
            context_item = context_by_source.get(source_id, {})
            result.append(QAEvidence(
                source_id=source_id,
                quote=str(item.get("quote") or context_item.get("content") or "")[:500],
                title=str(source.get("title") or ""),
                url=str(source.get("url") or ""),
                chunk_id=context_item.get("chunk_id"),
            ))
        return result
