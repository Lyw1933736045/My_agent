"""Case Assistant tools. Business logic stays in existing services."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from ..run_repository import CaseRecord, RunRepository
from ..tools.case_qa import CASE_RAW_TYPES, CaseQAService
from ..tools.embedding_service import EmbeddingService
from ..tools.reranker_service import RerankerService
from ..tools.vector_retriever import VectorRetriever
from .memory import SessionMemory

MAX_REPORT_CHARS = 6_000


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "case_report",
            "description": (
                "查看或问答任意已入库简报，不限于当前工作区。"
                "get=打开指定 case_id 的简报；检索到历史简报后应传入该 case_id。"
                "query=围绕该简报问答，仅当用户已选定简洁版/分析版/深度搜索后再调用。"
                "mode：fast=简洁版，analysis=分析版，deep=该案例原文。"
                "不要用这个工具查历史新闻库，也不要生成新简报。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get", "query"]},
                    "case_id": {"type": "string", "description": "要打开或问答的案例 ID；查看历史简报时必须传入 search 得到的 case_id"},
                    "question": {"type": "string", "description": "query 时的问题"},
                    "mode": {
                        "type": "string",
                        "enum": ["fast", "analysis", "deep"],
                        "description": "query 的检索深度",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "检索整个历史新闻知识库（raw_document）。适用于过去有没有类似报道、"
                "历史机构讨论、历史政策新闻。不要用它打开或生成简报。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "历史新闻检索问题"},
                    "top_k": {"type": "integer", "description": "召回条数，可选"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_manager",
            "description": (
                "简报生命周期。search=查找某主题是否已有简报，没有时不要自动生成；"
                "generate=用户明确要求生成或重新生成时，新开案例并完整重跑研究流程"
                "（规划检索、抓取、分析、写简报），不要只用旧分析重写一页，也不要写进当前工作区。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["search", "generate"]},
                    "topic": {"type": "string"},
                    "case_id": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
]


def _has_report(case: CaseRecord | None) -> bool:
    if case is None:
        return False
    data = case.report_data if isinstance(case.report_data, dict) else {}
    return bool(case.report or data)


def _report_preview(case: CaseRecord) -> dict[str, Any]:
    data = case.report_data if isinstance(case.report_data, dict) else {}
    preview = {
        "case_id": case.case_id,
        "topic": case.topic,
        "title": data.get("title") or case.topic,
        "status": case.status,
        "executive_summary": list(data.get("executive_summary") or [])[:8],
        "key_metrics": list(data.get("key_metrics") or [])[:8],
        "synthesis": data.get("synthesis") or {},
    }
    markdown = str(case.report or "")
    if markdown:
        preview["report_markdown"] = markdown[:MAX_REPORT_CHARS]
    return preview


def _qa_payload(result) -> dict[str, Any]:
    return {
        "ok": True,
        "case_id": result.case_id,
        "mode": result.mode,
        "answer": result.answer,
        "citations": [item.__dict__ for item in result.citations],
        "evidence": [item.__dict__ for item in result.evidence],
        "retrieved_count": result.retrieved_count,
        "retrieval_scope": result.retrieval_scope,
    }


class AssistantToolbox:
    def __init__(
        self,
        repository: RunRepository,
        llm_client,
        settings: Settings | None = None,
        *,
        qa_service: CaseQAService | None = None,
        embedding_service: EmbeddingService | None = None,
        reranker_service: RerankerService | None = None,
        retriever: VectorRetriever | None = None,
        start_research: Callable[[str], dict] | None = None,
    ) -> None:
        self.repository = repository
        self.llm_client = llm_client
        self.settings = settings
        self.qa_service = qa_service or CaseQAService(
            repository,
            llm_client,
            settings=settings,
            embedding_service=embedding_service,
            reranker_service=reranker_service,
            retriever=retriever,
        )
        self.embedding = embedding_service
        self.reranker = reranker_service
        self.retriever = retriever or VectorRetriever(repository)
        self.start_research = start_research
        self.vector_top_k = getattr(settings, "RAG_VECTOR_TOP_K", 30) if settings else 30
        self.rerank_top_n = getattr(settings, "RAG_RERANK_TOP_N", 8) if settings else 8

    def execute(self, name: str, arguments: dict[str, Any], memory: SessionMemory) -> dict[str, Any]:
        handler = self.registry().get(name)
        if handler is None:
            return {"ok": False, "error": f"未知工具：{name}"}
        return handler(arguments, memory)

    def registry(self) -> dict[str, Callable[[dict[str, Any], SessionMemory], dict[str, Any]]]:
        return {
            "case_report": self.case_report,
            "search_knowledge": self.search_knowledge,
            "report_manager": self.report_manager,
        }

    def case_report(self, arguments: dict[str, Any], memory: SessionMemory) -> dict[str, Any]:
        action = str(arguments.get("action") or "get").strip()
        case_id = str(arguments.get("case_id") or memory.case_id or "").strip()
        if not case_id:
            return {"ok": False, "error": "当前没有绑定案例，无法查看或问答简报"}
        case = self.repository.get_case(case_id)
        if case is None:
            return {"ok": False, "error": "研究案例不存在"}
        memory.bind_case(case.case_id)
        if action == "get":
            if not _has_report(case):
                return {
                    "ok": True,
                    "has_report": False,
                    "case_id": case.case_id,
                    "topic": case.topic,
                    "message": "当前案例还没有简报",
                }
            preview = _report_preview(case)
            preview["ok"] = True
            preview["has_report"] = True
            return preview
        if action != "query":
            return {"ok": False, "error": "case_report.action 必须是 get 或 query"}
        question = str(arguments.get("question") or "").strip()
        mode = str(arguments.get("mode") or "fast").strip()
        if mode not in {"fast", "analysis", "deep"}:
            return {"ok": False, "error": "query.mode 必须是 fast、analysis 或 deep"}
        extra = {}
        if mode == "deep":
            extra = {"include_historical": False, "source_types": CASE_RAW_TYPES}
        try:
            result = self.qa_service.answer(case, question, mode, **extra)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return _qa_payload(result)

    def search_knowledge(self, arguments: dict[str, Any], memory: SessionMemory) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if len(query) < 2:
            return {"ok": False, "error": "search_knowledge 需要有效 query"}
        current_case_id = str(arguments.get("current_case_id") or memory.case_id or "").strip() or None
        top_k = int(arguments.get("top_k") or self.vector_top_k)
        if self.embedding is None:
            self.embedding = EmbeddingService.from_settings(self.settings)
        if self.reranker is None:
            self.reranker = RerankerService.from_settings(self.settings)
        query_vector = self.embedding.embed_query(query)
        rows = self.retriever.search_all(
            query_vector,
            source_types=["raw_document"],
            top_k=max(1, top_k),
        )
        try:
            ranked = self.reranker.rerank(query, rows, top_n=self.rerank_top_n)
        except Exception:
            ranked = list(rows)[: self.rerank_top_n]
        hits = []
        for item in ranked:
            hits.append({
                "case_id": item.get("case_id"),
                "title": item.get("title") or "",
                "source": item.get("title") or item.get("url") or "",
                "published_at": item.get("published_at"),
                "content": str(item.get("content") or "")[:500],
                "url": item.get("url") or "",
                "similarity_score": item.get("similarity_score"),
                "rerank_score": item.get("rerank_score"),
                "chunk_id": item.get("chunk_id"),
                "is_current_case": bool(current_case_id and item.get("case_id") == current_case_id),
            })
        return {"ok": True, "query": query, "count": len(hits), "results": hits}

    def report_manager(self, arguments: dict[str, Any], memory: SessionMemory) -> dict[str, Any]:
        action = str(arguments.get("action") or "search").strip()
        topic = str(arguments.get("topic") or "").strip()
        case_id = str(arguments.get("case_id") or memory.case_id or "").strip()
        if action == "search":
            query = topic or case_id
            if len(query) < 2:
                return {"ok": False, "error": "report_manager.search 需要 topic"}
            memory.pending_topic = topic or None
            matches = []
            for item in self.repository.find_cases_for_lookup(query):
                case = item.case
                prepared = self.repository.aggregate_case_prepared_analysis(case.case_id)
                has_report = _has_report(case)
                if not has_report:
                    continue
                data = case.report_data if isinstance(case.report_data, dict) else {}
                matches.append({
                    "case_id": case.case_id,
                    "topic": case.topic,
                    "title": data.get("title") or case.topic,
                    "summary": list(data.get("executive_summary") or [])[:5],
                    "match_type": item.match_type,
                    "can_generate": bool(prepared),
                })
            found = bool(matches)
            if found and matches[0].get("case_id"):
                memory.bind_case(str(matches[0]["case_id"]))
            return {
                "ok": True,
                "found": found,
                "matches": matches[:5],
                "message": None if found else "当前没有找到该主题的已有简报，是否为你生成一份新的？",
            }
        if action != "generate":
            return {"ok": False, "error": "report_manager.action 必须是 search 或 generate"}
        topic_query = topic or str(memory.pending_topic or "").strip()
        if len(topic_query) < 2:
            return {"ok": False, "can_generate": False, "error": "生成简报需要明确主题"}
        if self.start_research is None:
            return {
                "ok": False,
                "can_generate": False,
                "error": "当前服务未接入完整研究流程，无法生成新简报。",
            }
        try:
            result = self.start_research(topic_query)
        except Exception as exc:
            return {"ok": False, "can_generate": False, "error": f"启动研究流程失败：{exc}"}
        if result.get("case_id"):
            memory.bind_case(str(result["case_id"]))
        memory.pending_topic = None
        memory.bind_job(
            run_id=str(result.get("run_id") or "") or None,
            status=str(result.get("status") or "running"),
            topic=str(result.get("topic") or topic_query),
            case_id=str(result.get("case_id") or "") or None,
        )
        memory.run_progress = str(result.get("message") or "已开始完整研究流程")
        return result


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def summarize_tool_result(name: str, result: dict[str, Any]) -> str:
    if name == "case_report":
        if result.get("answer"):
            return f"case_report query/{result.get('mode')}：{str(result.get('answer'))[:180]}"
        if result.get("has_report"):
            return f"case_report get：已返回简报 {result.get('title') or result.get('topic')}"
        return f"case_report：{result.get('message') or result.get('error') or '完成'}"
    if name == "search_knowledge":
        return f"search_knowledge：命中 {result.get('count') or 0} 条历史材料"
    if name == "report_manager":
        if "found" in result:
            return f"report_manager search：found={result.get('found')} matches={len(result.get('matches') or [])}"
        if result.get("ok"):
            if result.get("started"):
                return f"report_manager generate：已启动完整研究 {result.get('topic') or ''} {result.get('run_id') or ''}"
            return f"report_manager generate：已生成 {result.get('title') or result.get('topic')}"
        return f"report_manager：{result.get('error') or '完成'}"
    return name
