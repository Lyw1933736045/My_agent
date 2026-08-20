"""Case-scoped research Q&A over brief, structured insights, and raw documents."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from ..prompts.qa_prompts import SYSTEM_PROMPT_QA_ANSWER
from ..run_repository import CaseRecord, RunRepository
from ..utils.config import Settings
from ..utils.text_processing import extract_json
from .embedding_service import EmbeddingService
from .knowledge_indexer import KnowledgeIndexer, index_case_safely
from .reranker_service import RerankerService
from .vector_retriever import VectorRetriever


ANALYSIS_TYPES = ("media_insight", "social_insight", "structured_analysis")
DEEP_TYPES = ("raw_document", "media_insight", "social_insight", "structured_analysis")
CASE_RAW_TYPES = ("raw_document",)


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
    origin: str | None = None
    case_id: str | None = None


@dataclass(frozen=True)
class QAResult:
    case_id: str
    mode: str
    answer: str
    citations: list[QACitation] = field(default_factory=list)
    evidence: list[QAEvidence] = field(default_factory=list)
    retrieved_count: int = 0
    retrieval_scope: str = ""


class CaseQAService:
    """One bounded retrieval-and-answer pass for a selected case."""

    def __init__(
        self,
        repository: RunRepository,
        llm_client,
        settings: Settings | None = None,
        embedding_service: EmbeddingService | None = None,
        reranker_service: RerankerService | None = None,
        retriever: VectorRetriever | None = None,
        indexer: KnowledgeIndexer | None = None,
    ) -> None:
        self.repository = repository
        self.llm_client = llm_client
        self.settings = settings
        self.embedding = embedding_service
        self.reranker = reranker_service
        self.retriever = retriever or VectorRetriever(repository)
        self.indexer = indexer
        self.vector_top_k = getattr(settings, "RAG_VECTOR_TOP_K", 30) if settings else 30
        self.case_top_k = getattr(settings, "RAG_CASE_TOP_K", 20) if settings else 20
        self.global_top_k = getattr(settings, "RAG_GLOBAL_TOP_K", 15) if settings else 15
        self.rerank_top_n = getattr(settings, "RAG_RERANK_TOP_N", 8) if settings else 8

    def _embedding(self) -> EmbeddingService:
        if self.embedding is None:
            self.embedding = EmbeddingService.from_settings(self.settings)
        return self.embedding

    def _reranker(self) -> RerankerService:
        if self.reranker is None:
            self.reranker = RerankerService.from_settings(self.settings)
        return self.reranker

    def answer(
        self,
        case: CaseRecord,
        question: str,
        mode: str,
        *,
        include_historical: bool | None = None,
        source_types: tuple[str, ...] | None = None,
    ) -> QAResult:
        if mode not in {"fast", "analysis", "deep"}:
            raise ValueError("问答模式必须是 fast、analysis 或 deep")
        question = " ".join(str(question or "").split())
        if len(question) < 2:
            raise ValueError("问题至少需要两个字符")

        retrieval_scope = "brief" if mode == "fast" else "case"
        if mode == "fast":
            sources = self._source_catalog(case.report_data)
            context, retrieval_evidence = self._fast_context(case, question, sources)
        elif mode == "analysis":
            context, retrieval_evidence, sources = self._vector_context(
                case,
                question,
                source_types=source_types or ANALYSIS_TYPES,
                include_historical=False,
            )
        else:
            historical = True if include_historical is None else include_historical
            types = source_types or DEEP_TYPES
            retrieval_scope = "case+global" if historical else "case_raw"
            context, retrieval_evidence, sources = self._vector_context(
                case, question, source_types=types, include_historical=historical
            )
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
            retrieval_scope=retrieval_scope,
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

    def _fast_context(
        self,
        case: CaseRecord,
        question: str,
        sources: dict[str, dict],
    ) -> tuple[list[dict], list[QAEvidence]]:
        del question, sources
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
        return [{"kind": "brief", "content": selected, "origin": "current"}], []

    def _vector_context(
        self,
        case: CaseRecord,
        question: str,
        *,
        source_types: tuple[str, ...],
        include_historical: bool,
    ) -> tuple[list[dict], list[QAEvidence], dict[str, dict]]:
        chunks = self._retrieve(case, question, source_types, include_historical)
        if not chunks:
            index_case_safely(self.repository, case.case_id, self.settings)
            chunks = self._retrieve(case, question, source_types, include_historical)
        if not chunks:
            return [], [], {}
        ranked = self._rerank(question, chunks)
        selected = ranked[: self.rerank_top_n]
        context = []
        evidence = []
        sources: dict[str, dict] = {}
        for item in selected:
            origin = "historical" if item.get("case_id") != case.case_id else "current"
            source_id = str(item.get("source_id") or "")
            if origin == "historical":
                source_id = f"hist:{item.get('case_id')}:{source_id}"
            source = {
                "source_id": source_id,
                "title": str(item.get("title") or "未命名来源"),
                "source_name": "",
                "url": str(item.get("url") or ""),
                "published_at": item.get("published_at"),
                "source_type": item.get("source_type"),
                "case_id": item.get("case_id"),
                "origin": origin,
            }
            sources[source_id] = source
            context.append({
                "kind": "knowledge_chunk",
                "chunk_id": item.get("chunk_id"),
                "source_id": source_id,
                "source_type": item.get("source_type"),
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "content": item.get("content"),
                "origin": origin,
                "case_id": item.get("case_id"),
                "similarity_score": item.get("similarity_score"),
                "rerank_score": item.get("rerank_score"),
            })
            evidence.append(QAEvidence(
                source_id=source_id,
                quote=str(item.get("content") or "")[:280],
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                chunk_id=str(item.get("chunk_id") or "") or None,
                origin=origin,
                case_id=str(item.get("case_id") or "") or None,
            ))
        return context, evidence, sources

    def _retrieve(
        self,
        case: CaseRecord,
        question: str,
        source_types: tuple[str, ...],
        include_historical: bool,
    ) -> list[dict]:
        query_vector = self._embedding().embed_query(question)
        current = self.retriever.search_case(
            query_vector,
            case.case_id,
            source_types=list(source_types),
            top_k=self.vector_top_k if not include_historical else self.case_top_k,
        )
        if not include_historical:
            return current
        historical = self.retriever.search_global(
            query_vector,
            case.case_id,
            source_types=list(source_types),
            top_k=self.global_top_k,
        )
        merged = []
        seen = set()
        for item in [*current, *historical]:
            key = (item.get("case_id"), item.get("chunk_id") or item.get("content_hash"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged

    def _rerank(self, question: str, chunks: list[dict]) -> list[dict]:
        try:
            return self._reranker().rerank(question, chunks, top_n=self.rerank_top_n)
        except Exception:
            return list(chunks)

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
                origin=context_item.get("origin"),
                case_id=context_item.get("case_id"),
            ))
        return result
