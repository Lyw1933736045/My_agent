"""My_agent 最小 FastAPI 服务：规划、人工审核、后台执行与结果查询。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from difflib import SequenceMatcher
from html import escape
import hashlib
import json
import re
from threading import Event
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

from .agent import FinancialMediaAgent
from .assistant import CaseAssistantAgent, SessionMemoryStore
from .assistant.brief_runner import run_prepared_case_brief
from .assistant.tools import AssistantToolbox
from .evaluation.snapshot import write_snapshot
from .run_repository import CaseLookupMatch, CaseRecord, RunRecord, RunRepository
from .state import RunState
from .simulation_api import router as simulation_router
from .tools.media_models import (
    DiscoveryResult,
    MediaCandidate,
    MediaDocument,
    MediaInsight,
)
from .tools.case_qa import CaseQAService
from .tools.knowledge_indexer import index_case_safely
from .utils.config import PROJECT_ROOT, Settings
from .utils.media_sources import load_media_sources
from .tools.text_chunking import select_relevance_chunks, split_text

WEB_DIR = Path(__file__).resolve().parent / "web"


RunStatus = Literal[
    "waiting_for_review",
    "running",
    "completed",
    "analysis_ready",
    "failed",
    "canceled",
]


class CreatePlanRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    sources: dict[str, bool] = Field(default_factory=lambda: {
        "newsnow": True, "rss": True, "tavily": True, "weibo": False
    })


class ApprovePlanRequest(BaseModel):
    approved_tavily_queries: list[str] = Field(default_factory=list, max_length=10)
    newsnow_rss_core: list[str] | None = None
    newsnow_rss_support: list[str] | None = None
    weibo_query: str | None = None


class PlanResponse(BaseModel):
    run_id: str
    case_id: str
    case_key: str
    query: str
    topic: str
    tavily_queries: list[str]
    newsnow_rss_core: list[str] = Field(default_factory=list)
    newsnow_rss_support: list[str] = Field(default_factory=list)
    weibo_query: str = ""
    status: RunStatus


class SourceResult(BaseModel):
    provider: str
    name: str
    ok: bool
    detail: str = ""


class RunResponse(BaseModel):
    run_id: str
    case_id: str | None = None
    query: str
    topic: str
    approved_tavily_queries: list[str]
    status: RunStatus
    progress: str
    error: str | None = None
    report: str | None = None
    report_data: dict = Field(default_factory=dict)
    sources: list[SourceResult] = Field(default_factory=list)
    source_summary: dict[str, int] = Field(default_factory=dict)
    tavily_queries: list[str] = Field(default_factory=list)
    newsnow_rss_core: list[str] = Field(default_factory=list)
    newsnow_rss_support: list[str] = Field(default_factory=list)
    weibo_query: str = ""
    retrieval_reflection: dict = Field(default_factory=dict)
    search_history: list[dict] = Field(default_factory=list)
    report_stale: bool = False


class CaseResponse(BaseModel):
    case_id: str
    case_key: str
    query: str
    topic: str
    status: str
    progress: str
    error: str | None = None
    report: str | None = None
    report_data: dict = Field(default_factory=dict)
    child_run_ids: list[str] = Field(default_factory=list)
    child_runs: list[RunResponse] = Field(default_factory=list)
    prepared_summary: dict[str, int] = Field(default_factory=dict)
    report_stale: bool = False


class CaseLookupRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)


class CaseMatchResponse(BaseModel):
    case_id: str
    case_key: str
    query: str
    topic: str
    status: str
    updated_at: str | None = None
    child_run_count: int = 0
    prepared_insight_count: int = 0
    has_report: bool = False
    can_reuse: bool = False
    match_type: str = "term"
    matched_terms: list[str] = Field(default_factory=list)


class CaseLookupResponse(BaseModel):
    query: str
    matches: list[CaseMatchResponse] = Field(default_factory=list)


class CaseListResponse(BaseModel):
    cases: list[CaseMatchResponse] = Field(default_factory=list)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=5_000)
    mode: Literal["fast", "analysis", "deep"]


class ChatCitationResponse(BaseModel):
    source_id: str
    claim: str = ""
    title: str = ""
    source_name: str = ""
    url: str = ""


class ChatEvidenceResponse(BaseModel):
    source_id: str
    quote: str
    title: str = ""
    url: str = ""
    chunk_id: str | None = None
    origin: str | None = None
    case_id: str | None = None


class ChatResponse(BaseModel):
    case_id: str
    mode: Literal["fast", "analysis", "deep"]
    answer: str
    citations: list[ChatCitationResponse] = Field(default_factory=list)
    evidence: list[ChatEvidenceResponse] = Field(default_factory=list)
    retrieved_count: int = 0
    retrieval_scope: str = ""


class RerunRequest(BaseModel):
    tavily_queries: list[str] | None = None


class AppendSourcesRequest(BaseModel):
    sources: dict[str, bool]
    tavily_queries: list[str] | None = None


class ResumeAnalysisRequest(BaseModel):
    """Resume Stage 2 using candidates already persisted for this run."""

    pass


class GenerateBriefRequest(BaseModel):
    save_markdown_file: bool = True


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=5_000)
    session_id: str | None = None
    case_id: str | None = None
    qa_mode: Literal["fast", "analysis", "deep"] | None = None


class AssistantChatResponse(BaseModel):
    session_id: str
    case_id: str | None = None
    answer: str
    citations: list[ChatCitationResponse] = Field(default_factory=list)
    evidence: list[ChatEvidenceResponse] = Field(default_factory=list)
    retrieved_count: int = 0
    retrieval_scope: str = ""
    tool_trace: list[dict] = Field(default_factory=list)
    pending_generation: bool = False
    started_job: dict | None = None
    job: dict | None = None
    open_case_id: str | None = None


app = FastAPI(
    title="My_agent API",
    version="0.1.0",
    description="金融媒体研究 Agent 的规划、人工审核与执行接口。",
)
app.include_router(simulation_router)

_settings = Settings()
_repository = RunRepository(_settings.DATABASE_URL)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="my-agent")
_cancel_events: dict[str, Event] = {}
_run_sources: dict[str, set[str]] = {}
_assistant_memory = SessionMemoryStore()


class _RunCanceled(Exception):
    pass
_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False})


def _safe_link_open(tokens, index, options, env):
    token = tokens[index]
    token.attrSet("target", "_blank")
    token.attrSet("rel", "noopener noreferrer")
    return _markdown.renderer.renderToken(tokens, index, options, env)


_markdown.renderer.rules["link_open"] = _safe_link_open


def _new_agent(progress=None, enabled_sources=None) -> FinancialMediaAgent:
    return FinancialMediaAgent(
        Settings(), progress=progress, enabled_sources=enabled_sources
    )


def _get_record(run_id: str) -> RunRecord:
    record = _repository.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="研究任务不存在")
    return record


def _get_case(case_id: str) -> CaseRecord:
    case = _repository.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="研究案例不存在")
    return case


def _start_full_research(query: str) -> dict:
    """Same path as homepage: plan → auto-approve → crawl/analyze/write brief."""
    topic_query = " ".join(str(query or "").split())
    if len(topic_query) < 2:
        raise ValueError("生成简报需要明确主题")
    state = _new_agent().create_plan(topic_query)
    run_id = uuid4().hex
    case_id = uuid4().hex
    case_key = f"case-{case_id[:8]}"
    enabled_sources = {"newsnow", "rss", "tavily"}
    _repository.create_case(
        case_id=case_id,
        case_key=case_key,
        query=state.query,
        topic=state.topic,
    )
    _run_sources[run_id] = enabled_sources
    _repository.create(
        run_id=run_id,
        query=state.query,
        topic=state.topic,
        tavily_queries=list(state.tavily_queries),
        newsnow_rss_core=list(state.newsnow_rss_core),
        newsnow_rss_support=list(state.newsnow_rss_support),
        weibo_query=state.weibo_query,
        enabled_sources=enabled_sources,
        parent_event_id=case_id,
    )
    queries = [" ".join(item.split()) for item in list(state.tavily_queries or []) if str(item).strip()]
    queries = list(dict.fromkeys(queries))
    if not _repository.approve(run_id, queries):
        raise ValueError("无法启动研究任务")
    _cancel_events[run_id] = Event()
    _executor.submit(_execute_run, run_id)
    return {
        "ok": True,
        "started": True,
        "case_id": case_id,
        "run_id": run_id,
        "topic": state.topic,
        "query": state.query,
        "status": "running",
        "message": "已开始按完整研究流程生成，进度在左侧。完成后会打开新简报看板。",
    }


def _set_progress(run_id: str, message: str) -> None:
    _repository.update_progress(run_id, message)


def _index_run_knowledge(run_id: str) -> None:
    case_id = _repository.resolve_case_id(run_id)
    if case_id:
        index_case_safely(_repository, case_id, _settings)


def _prepared_analysis_from_state(state: RunState) -> dict:
    """Persist the Stage 2 checkpoint so a parent case can consume this run."""
    return {
        "query": state.query,
        "topic": state.topic,
        "read_attempted_count": state.read_attempted_count,
        "read_success_count": state.read_success_count,
        "relevant_count": state.relevant_documents_count,
        "media_insights": [
            asdict(item) for item in state.insights
            if item.source_group != "social_media"
        ],
        "social_insights": [
            asdict(item) for item in state.insights
            if item.source_group == "social_media"
        ],
    }


def _to_case_response(case: CaseRecord) -> CaseResponse:
    prepared = _repository.aggregate_case_prepared_analysis(case.case_id) or {}
    return CaseResponse(
        case_id=case.case_id,
        case_key=case.case_key,
        query=case.query,
        topic=case.topic,
        status=case.status,
        progress=case.progress,
        error=case.error,
        report=case.report,
        report_data=case.report_data,
        child_run_ids=[item.run_id for item in case.child_runs],
        child_runs=[_to_run_response(item) for item in case.child_runs],
        prepared_summary={
            "child_run_count": len(case.child_runs),
            "prepared_child_run_count": len(prepared.get("child_run_ids") or []),
            "media_insight_count": len(prepared.get("media_insights") or []),
            "social_insight_count": len(prepared.get("social_insights") or []),
            "relevant_count": int(prepared.get("relevant_count") or 0),
        },
        report_stale=bool(case.metadata.get("report_stale", False)),
    )


def _to_case_match_response(
    case: CaseRecord,
    *,
    match_type: str = "term",
    matched_terms: list[str] | None = None,
) -> CaseMatchResponse:
    prepared = _repository.aggregate_case_prepared_analysis(case.case_id) or {}
    prepared_count = int(prepared.get("relevant_count") or 0)
    return CaseMatchResponse(
        case_id=case.case_id,
        case_key=case.case_key,
        query=case.query,
        topic=case.topic,
        status=case.status,
        updated_at=case.updated_at.isoformat() if case.updated_at else None,
        child_run_count=len(case.child_runs),
        prepared_insight_count=prepared_count,
        has_report=bool(case.report),
        can_reuse=bool(case.report or prepared_count),
        match_type=match_type,
        matched_terms=list(matched_terms or []),
    )


def _save_stage1(run_id: str, agent: FinancialMediaAgent, state: RunState) -> dict[str, int]:
    discovery = state.discovery
    if discovery is None:
        return {"new_count": 0, "duplicate_count": 0, "total_unique_count": 0}
    candidates = discovery.raw_candidates or discovery.candidates
    rows = []
    for item in candidates:
        if not item.url.strip():
            continue
        row = {
            "title": item.title,
            "url": item.url,
            "search_snippet": item.snippet,
            "source": item.source_name,
            "provider": (
                "tavily"
                if any(name.startswith("tavily_") for name in item.discovered_by)
                else (item.discovered_by[0] if item.discovered_by else "unknown")
            ),
            "source_group": item.source_group,
            "query": item.query,
            "published_at": item.published_at,
            "appearances": list(item.metadata.get("appearances") or []),
        }
        if "weibo" in item.discovered_by and item.metadata.get("content_ready"):
            metadata = item.metadata
            row.update({
                "raw_content": item.snippet,
                "fetch_status": "success",
                "content_type": "text/plain",
                "fetched_at": metadata.get("fetched_at"),
                "document_metadata": {
                    key: metadata.get(key)
                    for key in (
                        "platform", "wid", "mblogid", "user_id", "user_name",
                        "text_complete",
                    )
                    if metadata.get(key) is not None
                },
                "event_metadata": {
                    "social_snapshot": {
                        "captured_at": metadata.get("fetched_at"),
                        "post_text": item.snippet,
                        **{
                            key: metadata.get(key)
                            for key in (
                                "likes_count", "comments_count", "reposts_count",
                                "comments_fetch", "comments",
                            )
                            if metadata.get(key) is not None
                        },
                    }
                },
                "search_sort": metadata.get("search_sort"),
                "platform_rank": metadata.get("platform_rank"),
                "analysis_status": "accepted",
                "analysis_reason": metadata.get("relevance_reason") or "微博已通过保存前相关性复核",
                "relevance_score": metadata.get("relevance_score"),
                "selected_for_report": True,
            })
        rows.append(row)
    saved = _repository.save_candidates(run_id, rows)
    _set_progress(
        run_id,
        "Stage 1 搜索完成："
        f"新增 {saved['new_count']} 条，重复 {saved['duplicate_count']} 条，"
        f"当前累计 {saved['total_unique_count']} 条",
    )
    return saved


def _reload_stage1_from_database(run_id: str, state: RunState) -> None:
    """Make PostgreSQL the only Stage 1 → Stage 2 candidate boundary."""
    if state.discovery is None:
        raise ValueError("尚未完成 Stage 1，无法加载数据库候选")
    rows = _repository.list_candidates(run_id)
    candidates = []
    for row in rows:
        if not str(row.get("url") or "").strip():
            continue
        providers = tuple(row.get("providers") or [row.get("provider") or "unknown"])
        document_metadata = dict(row.get("document_metadata") or {})
        event_metadata = dict(row.get("event_metadata") or {})
        social_snapshot = dict(event_metadata.get("social_snapshot") or {})
        is_weibo = "weibo" in providers
        metadata = {
            **document_metadata,
            **social_snapshot,
            "appearances": list((row.get("discovery") or {}).get("appearances") or []),
        }
        if is_weibo and row.get("content"):
            metadata["content_ready"] = True
            metadata["fetched_at"] = row.get("fetched_at") or social_snapshot.get("captured_at")
            metadata["prechecked_relevance"] = row.get("analysis_status") == "accepted"
            metadata["relevance_score"] = row.get("relevance_score")
            metadata["relevance_reason"] = row.get("analysis_reason")
        candidates.append(MediaCandidate(
            title=str(row.get("title") or ""),
            url=str(row.get("url") or ""),
            source_name=str(row.get("source") or ""),
            published_at=row.get("published_at"),
            snippet=str(social_snapshot.get("post_text") or row.get("content") or row.get("search_snippet") or ""),
            discovered_by=providers,
            source_group=str(row.get("source_group") or "news_media"),
            query=row.get("query"),
            guid=f"weibo:{document_metadata.get('wid')}" if is_weibo and document_metadata.get("wid") else None,
            metadata=metadata,
        ))
    state.discovery = replace(
        state.discovery,
        candidates=candidates,
        raw_candidates=candidates,
    )
    _set_progress(run_id, f"Stage 2 已从 PostgreSQL 加载 {len(candidates)} 条候选")


def _save_completed_documents(run_id: str, state: RunState) -> None:
    """Persist complete fetched bodies; no derived chunks or claims are stored."""
    decisions = {
        document.candidate.url: decision
        for document, decision in zip(state.selected_documents, state.content_decisions)
    }
    failed = 0
    for document in state.selected_documents:
        candidate = document.candidate
        try:
            _repository.update_candidate_fetch(
                run_id,
                candidate.url,
                raw_content=document.raw_content or document.content,
                fetch_status="fetched",
                final_url=document.final_url,
                content_type=document.content_type,
                fetched_at=document.fetched_at,
            )
            decision = decisions.get(candidate.url)
            relation_id = _candidate_relation_id(run_id, candidate.url)
            if decision is not None and relation_id:
                _repository.update_candidate_analysis(
                    relation_id,
                    "accepted" if decision.relevant else "rejected",
                    decision.reason,
                    relevance_score=decision.score,
                )
        except Exception as exc:
            failed += 1
            _set_progress(run_id, f"正文入库警告：{candidate.title[:40]}（{exc}）")
    if failed:
        _set_progress(run_id, f"正文入库完成，跳过 {failed} 条异常正文，其余继续保存")


def _candidate_relation_id(run_id: str, url: str) -> str:
    for row in _repository.list_candidates(run_id):
        if row.get("url") == url or row.get("final_url") == url:
            return str(row["id"])
    return ""


def _evaluation_dir(run_id: str) -> Path:
    return PROJECT_ROOT / "data" / "evaluations" / run_id


def _sources_payload(discovery: DiscoveryResult | None) -> list[dict]:
    if discovery is None:
        return []
    return [
        {
            "provider": item.provider,
            "name": item.name,
            "ok": item.ok,
            "detail": item.detail,
        }
        for item in discovery.sources
    ]


def _filename_stem(topic: str, run_id: str) -> str:
    cleaned = "".join(
        char if ("a" <= char.lower() <= "z") or char.isdigit() or char in {"-", "_"}
        else "_"
        for char in topic.strip()
    ).strip("_")
    stem = cleaned[:40] or "topic_brief"
    return f"{stem}_{run_id[:8]}"


def _content_disposition(filename: str) -> str:
    ascii_name = "".join(
        char if 32 <= ord(char) < 127 and char not in {'"', "\\"} else "_"
        for char in filename
    )
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )


def _require_completed_report(record: RunRecord | CaseRecord) -> str:
    if record.status == "failed":
        raise HTTPException(status_code=409, detail=record.error or "研究任务失败")
    if record.status not in {"completed", "analysis_ready"}:
        raise HTTPException(status_code=409, detail="研究任务尚未完成")
    if not record.report:
        raise HTTPException(status_code=409, detail="当前 case 尚未生成简报")
    return record.report or ""


def _dashboard_report_html(report_data: dict) -> str:
    sources = {
        str(item.get("id")): item
        for item in report_data.get("sources", [])
        if isinstance(item, dict) and item.get("id")
    }

    def citations(source_ids) -> str:
        links = []
        for source_id in source_ids if isinstance(source_ids, list) else []:
            source = sources.get(str(source_id))
            if not source or not source.get("url"):
                continue
            links.append(
                f'<a class="cite" href="{escape(str(source["url"]), quote=True)}" '
                f'target="_blank" rel="noopener noreferrer">{escape(str(source_id))}</a>'
            )
        return " " + " ".join(links) if links else ""

    def evidence_list(values) -> str:
        rows = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict) or not item.get("text"):
                continue
            rows.append(f'<li>{escape(str(item["text"]))}{citations(item.get("source_ids"))}</li>')
        return f'<ul class="evidence">{"".join(rows)}</ul>' if rows else ""

    def topic_section(title: str, section: object) -> str:
        if not isinstance(section, dict):
            return ""
        cards = []
        for topic in section.get("topics", []) if isinstance(section.get("topics"), list) else []:
            if not isinstance(topic, dict):
                continue
            views = []
            for view in [*(topic.get("supporting_views") or []), *(topic.get("social_views") or [])]:
                if not isinstance(view, dict) or not view.get("point"):
                    continue
                who = view.get("account") or " / ".join(
                    str(value) for value in (view.get("speaker"), view.get("organization")) if value
                )
                metrics = [
                    f'{label} {view[key]}'
                    for label, key in (("赞", "likes"), ("转发", "shares"), ("评论", "comments"))
                    if view.get(key) is not None
                ]
                suffix = f'（{escape("，".join(metrics))}）' if metrics else ""
                views.append(
                    f'<li>{escape((who + "：") if who else "")}{escape(str(view["point"]))}{suffix}'
                    f'{citations([view.get("source_id")] if view.get("source_id") else [])}</li>'
                )
            summary = escape(str(topic.get("summary") or ""))
            cards.append(
                '<details class="topic" open>'
                f'<summary>{escape(str(topic.get("title") or "主要关注"))}</summary>'
                f'<p>{summary}{citations(topic.get("source_ids"))}</p>'
                f'{"<ul>" + "".join(views) + "</ul>" if views else ""}</details>'
            )
        overview = str(section.get("overview") or "")
        if not overview and not cards:
            return ""
        return (
            f'<section class="block"><h2>{escape(title)}</h2>'
            f'{f"<p class=overview>{escape(overview)}</p>" if overview else ""}'
            f'<div class="topics">{"".join(cards)}</div></section>'
        )

    counts = {"official": 0, "media": 0, "social": 0}
    for source in sources.values():
        source_type = source.get("source_type")
        if source_type in counts:
            counts[source_type] += 1
    parts = [
        f'<h1>{escape(str(report_data.get("title") or "研究简报"))}</h1>',
        '<div class="meta">',
        *(
            f'<div><strong>{value}</strong><span>{label}</span></div>'
            for label, value in (
                ("材料", len(sources)), ("官方", counts["official"]),
                ("媒体", counts["media"]), ("社交", counts["social"]),
            )
        ),
        '</div>',
    ]
    if report_data.get("generated_at"):
        parts.append(f'<p class="updated">更新时间：{escape(str(report_data["generated_at"]))}</p>')

    summary = evidence_list(report_data.get("executive_summary"))
    if summary:
        parts.append(f'<section class="block summary"><h2>核心摘要</h2>{summary}</section>')

    metrics = []
    for item in report_data.get("key_metrics", []):
        if isinstance(item, dict) and (item.get("label") or item.get("value")):
            metrics.append(
                '<div class="metric">'
                f'<span>{escape(str(item.get("label") or "指标"))}</span>'
                f'<strong>{escape(str(item.get("value") or "-"))}</strong>'
                f'<p>{escape(str(item.get("context") or ""))}{citations(item.get("source_ids"))}</p></div>'
            )
    if metrics:
        parts.append(f'<section class="block"><h2>关键数据</h2><div class="metrics">{"".join(metrics)}</div></section>')

    timeline = []
    for item in report_data.get("timeline", []):
        if isinstance(item, dict) and item.get("event"):
            timeline.append(
                f'<li><time>{escape(str(item.get("date") or "时间未明确"))}</time>'
                f'<p>{escape(str(item["event"]))}{citations(item.get("source_ids"))}</p></li>'
            )
    if timeline:
        parts.append(f'<section class="block"><h2>事件时间线</h2><ol class="timeline">{"".join(timeline)}</ol></section>')

    parts.append(topic_section("一、官方层面", report_data.get("official")))
    media = report_data.get("media") if isinstance(report_data.get("media"), dict) else {}
    domestic = topic_section("境内媒体", media.get("domestic"))
    overseas = topic_section("境外及港澳媒体", media.get("overseas"))
    if media.get("overview") or domestic or overseas:
        parts.append(
            '<section class="block"><h2>二、媒体层面</h2>'
            f'{f"<p class=overview>{escape(str(media.get("overview")))}</p>" if media.get("overview") else ""}'
            f'{domestic}{overseas}</section>'
        )
    parts.append(topic_section("三、社会舆论层面", report_data.get("public_opinion")))

    synthesis = report_data.get("synthesis") if isinstance(report_data.get("synthesis"), dict) else {}
    synthesis_parts = []
    for heading, key in (("主要共识", "consensus"), ("主要差异", "differences"),
                         ("争议与风险", "risks"), ("后续观察", "watch_points")):
        content = evidence_list(synthesis.get(key))
        if content:
            synthesis_parts.append(f'<div class="synthesis"><h3>{heading}</h3>{content}</div>')
    if synthesis_parts:
        parts.append(f'<section class="block"><h2>四、综合研判</h2><div class="synthesis-grid">{"".join(synthesis_parts)}</div></section>')

    source_rows = []
    labels = {"official": "官方", "media": "媒体", "social": "社交"}
    for source in sources.values():
        source_rows.append(
            '<li>'
            f'<a href="{escape(str(source.get("url") or ""), quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'<strong>{escape(str(source.get("id")))}｜{escape(str(source.get("title") or "未命名来源"))}</strong>'
            f'<span>{escape(str(source.get("source_name") or ""))} · '
            f'{escape(str(source.get("published_at") or ""))} · {labels.get(source.get("source_type"), "媒体")}</span>'
            '</a></li>'
        )
    if source_rows:
        parts.append(f'<section class="block"><h2>来源</h2><ul class="sources">{"".join(source_rows)}</ul></section>')
    return "".join(parts)


def _report_html_page(
    *,
    topic: str,
    report_markdown: str,
    report_data: dict | None = None,
    auto_print: bool = False,
) -> str:
    report_html = (
        _dashboard_report_html(report_data)
        if isinstance(report_data, dict) and report_data
        else _markdown.render(report_markdown)
    )
    safe_topic = escape(topic)
    print_script = (
        "<script>window.addEventListener('load',()=>setTimeout(()=>window.print(),250));</script>"
        if auto_print
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_topic}｜研究报告</title>
  <style>
    body {{ margin: 0; background: #f5f7fa; color: #1f2937;
            font: 16px/1.75 -apple-system, BlinkMacSystemFont, "Segoe UI",
            "PingFang SC", "Microsoft YaHei", sans-serif; }}
    main {{ max-width: 880px; margin: 32px auto; padding: 40px 52px;
            background: white; border-radius: 12px;
            box-shadow: 0 4px 20px rgba(15, 23, 42, .08); }}
    h1, h2, h3 {{ color: #0f172a; line-height: 1.35; }}
    h1 {{ margin-top: 0; padding-bottom: 16px; border-bottom: 1px solid #e5e7eb; }}
    h2 {{ margin-top: 32px; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    blockquote {{ margin-left: 0; padding: 4px 16px; color: #475569;
                  border-left: 4px solid #94a3b8; background: #f8fafc; }}
    code {{ padding: 2px 5px; background: #f1f5f9; border-radius: 4px; }}
    .meta, .metrics, .topics, .synthesis-grid {{ display: grid; gap: 12px; }}
    .meta {{ grid-template-columns: repeat(4, 1fr); }}
    .meta div, .metric, .topic, .synthesis {{ border: 1px solid #e2e8f0; border-radius: 9px; padding: 14px; }}
    .meta strong, .meta span, .metric strong, .metric span {{ display: block; }}
    .meta strong, .metric strong {{ font-size: 1.35rem; }}
    .meta span, .metric span, .updated, .sources span {{ color: #64748b; font-size: .84rem; }}
    .updated {{ text-align: right; }}
    .block {{ margin-top: 18px; padding: 20px; border: 1px solid #e2e8f0; border-radius: 12px; }}
    .block h2 {{ margin-top: 0; }}
    .summary {{ border-left: 4px solid #0f7a6c; }}
    .metrics {{ grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }}
    .topics {{ grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
    .topic summary {{ cursor: pointer; font-weight: 700; }}
    .overview {{ padding: 10px 12px; background: #f8fafc; border-radius: 8px; }}
    .timeline {{ border-left: 2px solid #99d1c9; list-style: none; padding-left: 22px; }}
    .timeline li {{ padding: 0 0 14px 10px; }} .timeline p {{ margin-top: 2px; }}
    .timeline time {{ color: #0f766e; font-size: .84rem; font-weight: 700; }}
    .synthesis-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .cite {{ display: inline-block; padding: 0 5px; border: 1px solid #99d1c9; border-radius: 4px; font-size: .75rem; }}
    .sources {{ list-style: none; padding: 0; }} .sources li {{ margin: 7px 0; }}
    .sources a {{ display: grid; padding: 9px 11px; border: 1px solid #e2e8f0; border-radius: 7px; }}
    .print-hint {{ margin: 0 0 18px; color: #64748b; font-size: 0.92rem; }}
    @media (max-width: 720px) {{
      main {{ margin: 0; padding: 24px 20px; border-radius: 0; }}
      .meta, .synthesis-grid {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    @media print {{
      body {{ background: white; }}
      main {{ margin: 0; padding: 0; box-shadow: none; border-radius: 0; max-width: none; }}
      .print-hint {{ display: none; }}
      a {{ color: inherit; text-decoration: none; }}
    }}
  </style>
</head>
<body>
  <main>
    {"<p class='print-hint'>浏览器打印对话框中选择「存储为 PDF」即可导出。</p>" if auto_print else ""}
    {report_html}
  </main>
  {print_script}
</body>
</html>"""


def _execute_run(run_id: str) -> None:
    record = _get_record(run_id)
    sources: list[dict] = []
    cancel_event = _cancel_events.setdefault(run_id, Event())

    def progress(message: str) -> None:
        if cancel_event.is_set():
            raise _RunCanceled()
        _set_progress(run_id, message)

    try:
        state = RunState(
            query=record.query,
            topic=record.topic,
            tavily_queries=list(record.approved_tavily_queries or record.tavily_queries),
            newsnow_rss_core=list(record.newsnow_rss_core or []),
            newsnow_rss_support=list(record.newsnow_rss_support or []),
            weibo_query=record.weibo_query,
        )
        agent = _new_agent(
            progress=progress,
            enabled_sources=_run_sources.get(run_id, set(record.enabled_sources or ["newsnow", "rss", "tavily"])),
        )
        state = agent.discover_from_plan(
            state, cancel_check=cancel_event.is_set
        )
        if cancel_event.is_set():
            raise _RunCanceled()
        _save_stage1(run_id, agent, state)
        _reload_stage1_from_database(run_id, state)
        sources = _sources_payload(state.discovery)
        _repository.save_source_results(run_id, sources)
        _repository.save_retrieval_reflection(run_id, state.retrieval_reflection)
        result = agent.complete(state, cancel_check=cancel_event.is_set)
        if cancel_event.is_set():
            raise _RunCanceled()
        write_snapshot(state, _evaluation_dir(run_id))
        _save_completed_documents(run_id, state)
        _repository.save_prepared_analysis(
            run_id,
            _prepared_analysis_from_state(state),
        )
        _index_run_knowledge(run_id)
        _repository.complete(
            run_id,
            result.brief,
            report_data=result.brief_data,
            source_results=sources,
        )
        if record.parent_event_id:
            _repository.complete_case_brief(
                record.parent_event_id,
                result.brief,
                result.brief_data or {},
            )
        try:
            saved = FinancialMediaAgent.save_brief(result.brief)
            _set_progress(run_id, f"研究完成，已保存 {saved.name}")
        except Exception as exc:
            _set_progress(run_id, f"研究完成，但保存报告文件失败：{exc}")
    except _RunCanceled:
        _repository.cancel(run_id)
    except Exception as exc:
        if cancel_event.is_set():
            _repository.cancel(run_id)
        else:
            _repository.fail(run_id, str(exc), source_results=sources or None)


def _execute_additional_search(run_id: str) -> None:
    """只执行正式发现阶段，并把新候选合并进原事件。"""
    record = _get_record(run_id)
    sources: list[dict] = []
    cancel_event = _cancel_events.setdefault(run_id, Event())

    def progress(message: str) -> None:
        if cancel_event.is_set():
            raise _RunCanceled()
        _set_progress(run_id, message)

    try:
        state = RunState(
            query=record.query,
            topic=record.topic,
            tavily_queries=list(record.approved_tavily_queries or record.tavily_queries),
            newsnow_rss_core=list(record.newsnow_rss_core or []),
            newsnow_rss_support=list(record.newsnow_rss_support or []),
            weibo_query=record.weibo_query,
        )
        agent = _new_agent(
            progress=progress,
            enabled_sources=_run_sources.get(
                run_id,
                set(record.enabled_sources or ["newsnow", "rss", "tavily"]),
            ),
        )
        state = agent.discover_from_plan(state, cancel_check=cancel_event.is_set)
        if cancel_event.is_set():
            raise _RunCanceled()
        _save_stage1(run_id, agent, state)
        sources = _sources_payload(state.discovery)
        _repository.save_source_results(run_id, sources)
        _repository.save_retrieval_reflection(run_id, state.retrieval_reflection)
        _repository.complete_additional_search(run_id)
    except _RunCanceled:
        _repository.cancel(run_id)
    except Exception as exc:
        _repository.fail(run_id, str(exc), source_results=sources or None)


def _merge_source_results(existing: list[dict], current: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for item in [*existing, *current]:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("provider") or ""), str(item.get("name") or ""))
        merged[key] = item
    return list(merged.values())


def _execute_source_search(
    run_id: str,
    execution_id: str,
    selected_sources: set[str],
) -> None:
    """Append selected providers to one case and stop immediately before BriefNode."""
    record = _get_record(run_id)
    cancel_event = _cancel_events.setdefault(run_id, Event())

    def progress(message: str) -> None:
        if cancel_event.is_set():
            raise _RunCanceled()
        _set_progress(run_id, message)

    try:
        state = RunState(
            query=record.query,
            topic=record.topic,
            tavily_queries=list(record.approved_tavily_queries or record.tavily_queries),
            newsnow_rss_core=list(record.newsnow_rss_core or []),
            newsnow_rss_support=list(record.newsnow_rss_support or []),
            weibo_query=record.weibo_query,
        )
        agent = _new_agent(progress=progress, enabled_sources=selected_sources)
        state = agent.discover_from_plan(state, cancel_check=cancel_event.is_set)
        saved = _save_stage1(run_id, agent, state)
        _reload_stage1_from_database(run_id, state)
        current_sources = _sources_payload(state.discovery)
        merged_sources = _merge_source_results(record.source_results, current_sources)
        _repository.save_source_results(run_id, merged_sources)
        _repository.save_retrieval_reflection(run_id, state.retrieval_reflection)
        state = agent.prepare_analysis(state, cancel_check=cancel_event.is_set)
        _save_completed_documents(run_id, state)
        prepared_analysis = _prepared_analysis_from_state(state)
        _repository.complete_source_search(
            run_id,
            execution_id,
            saved,
            prepared_analysis,
        )
        _index_run_knowledge(run_id)
    except _RunCanceled:
        _repository.cancel(run_id)
    except Exception as exc:
        _repository.fail_source_search(run_id, execution_id, str(exc))


def _candidate_from_database_row(row: dict) -> MediaCandidate:
    """Rebuild a candidate from one PostgreSQL event_documents row."""
    document_metadata = dict(row.get("document_metadata") or {})
    event_metadata = dict(row.get("event_metadata") or {})
    social_snapshot = dict(event_metadata.get("social_snapshot") or {})
    providers = tuple(row.get("providers") or [row.get("provider") or "unknown"])
    is_weibo = "weibo" in providers
    metadata = {
        **document_metadata,
        **social_snapshot,
        "appearances": list((row.get("discovery") or {}).get("appearances") or []),
    }
    if is_weibo and row.get("content"):
        metadata.update({
            "content_ready": True,
            "prechecked_relevance": row.get("analysis_status") == "accepted",
            "relevance_score": row.get("relevance_score"),
            "relevance_reason": row.get("analysis_reason"),
            "fetched_at": row.get("fetched_at") or social_snapshot.get("captured_at"),
        })
    return MediaCandidate(
        title=str(row.get("title") or ""),
        url=str(row.get("url") or ""),
        source_name=str(row.get("source") or ""),
        published_at=row.get("published_at"),
        snippet=str(
            social_snapshot.get("post_text")
            or row.get("content")
            or row.get("search_snippet")
            or ""
        ),
        discovered_by=providers,
        source_group=str(row.get("source_group") or "news_media"),
        query=row.get("query"),
        guid=(
            f"weibo:{document_metadata.get('wid')}"
            if is_weibo and document_metadata.get("wid")
            else None
        ),
        metadata=metadata,
    )


def _resume_document(row: dict, candidate: MediaCandidate, content: str) -> MediaDocument:
    content = str(content or "").replace("\x00", "")
    return MediaDocument(
        candidate=candidate,
        final_url=str(row.get("final_url") or candidate.url),
        fetched_at=str(row.get("fetched_at") or ""),
        content_type=str(row.get("content_type") or "text/plain"),
        content=content,
        raw_content=content,
    )


def _resume_fingerprint(content: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", content).encode("utf-8")).hexdigest()


def _execute_analysis_resume(run_id: str, execution_id: str) -> None:
    """Resume Stage 2 from PostgreSQL without running any Provider search."""
    record = _get_record(run_id)
    cancel_event = _cancel_events.setdefault(run_id, Event())

    def progress(message: str) -> None:
        if cancel_event.is_set():
            raise _RunCanceled()
        _set_progress(run_id, message)

    try:
        rows = _repository.list_candidates(run_id)
        if not rows:
            raise ValueError("该 run 没有可恢复的 Stage 1 候选")
        agent = _new_agent(progress=progress, enabled_sources=set(record.enabled_sources))
        selection = load_media_sources()["selection"]
        core_terms = list(record.newsnow_rss_core or [])
        support_terms = list(record.newsnow_rss_support or [])

        accepted_documents: list[MediaDocument] = []
        stored_documents: list[tuple[dict, MediaDocument]] = []
        pending_rows: list[tuple[dict, MediaCandidate]] = []
        reference_documents: list[tuple[dict, str]] = []

        for row in rows:
            candidate = _candidate_from_database_row(row)
            content = str(row.get("content") or "").replace("\x00", "")
            status_value = str(row.get("analysis_status") or "pending")
            if content:
                reference_documents.append((row, content))
            if status_value == "accepted" and content:
                document = _resume_document(row, candidate, content)
                stored_documents.append((row, document))
                accepted_documents.append(document)
            elif status_value == "pending" or (status_value == "accepted" and not content):
                pending_rows.append((row, candidate))

        progress(
            f"Stage 2 断点恢复：已复用 {len(stored_documents)} 条正文，"
            f"继续处理 {len(pending_rows)} 条 pending"
        )

        pending_documents: list[tuple[dict, MediaDocument]] = []
        pending_failed = 0
        pending_duplicates = 0
        for index, (row, candidate) in enumerate(pending_rows, 1):
            if cancel_event.is_set():
                raise _RunCanceled()
            progress(
                f"  [{index}/{len(pending_rows)}] 恢复正文："
                f"{candidate.title[:50]}"
            )
            try:
                existing_content = str(row.get("content") or "").replace("\x00", "")
                if existing_content:
                    content = existing_content
                    final_url = str(row.get("final_url") or candidate.url)
                    fetched_at = str(row.get("fetched_at") or "")
                    content_type = str(row.get("content_type") or "text/plain")
                else:
                    result = agent.reader.read(candidate.url)
                    content = str(result.content or "").replace("\x00", "")
                    final_url = result.final_url
                    fetched_at = result.fetched_at
                    content_type = result.content_type
                    _repository.update_candidate_fetch(
                        run_id,
                        candidate.url,
                        raw_content=content,
                        fetch_status="fetched",
                        final_url=final_url,
                        content_type=content_type,
                        fetched_at=fetched_at,
                    )
                if not content.strip():
                    raise ValueError("正文为空")
                document = MediaDocument(
                    candidate=candidate,
                    final_url=final_url,
                    fetched_at=fetched_at,
                    content_type=content_type,
                    content=content,
                    raw_content=content,
                )
                duplicate_of = None
                if candidate.source_group != "social_media":
                    fingerprint = _resume_fingerprint(content)
                    for reference_row, reference_content in reference_documents:
                        if reference_row.get("id") == row.get("id"):
                            continue
                        if _resume_fingerprint(reference_content) == fingerprint or SequenceMatcher(
                            None, reference_content[:12000], content[:12000]
                        ).ratio() >= 0.92:
                            duplicate_of = reference_row
                            break
                if duplicate_of is not None:
                    _repository.update_candidate_analysis(
                        row["id"],
                        "duplicate",
                        "正文与已处理候选高度相似",
                        duplicate_of_id=duplicate_of["id"],
                    )
                    pending_duplicates += 1
                    continue
                pending_documents.append((row, document))
                if candidate.source_group != "social_media":
                    reference_documents.append((row, content))
            except Exception as exc:
                pending_failed += 1
                _repository.update_candidate_fetch(
                    run_id,
                    candidate.url,
                    fetch_status="failed",
                    fetch_error=str(exc),
                )
                _repository.update_candidate_analysis(
                    row["id"], "fetch_failed", str(exc)
                )

        relevance_pairs = []
        for row, document in pending_documents:
            raw = document.raw_content or document.content
            chunks = split_text(raw, chunk_size=1500, overlap=200)
            selected_chunks = select_relevance_chunks(
                chunks,
                topic=record.topic,
                core_terms=core_terms,
                support_terms=support_terms,
                top_k=int(selection.get("content_relevance_top_k", 5)),
            )
            relevance_pairs.append((
                row,
                replace(document, content="\n\n".join(selected_chunks)),
                document,
            ))

        decisions = []
        if relevance_pairs:
            decisions = agent.candidate_filter_node.run({
                "stage": "content",
                "topic": record.topic,
                "newsnow_rss_core": core_terms,
                "newsnow_rss_support": support_terms,
                "documents": [item[1] for item in relevance_pairs],
                "max_content_chars": int(selection.get("content_filter_max_chars", 5_000)),
                "model_min_score": int(selection.get("relevance_model_min_score", 30)),
            })
            if len(decisions) != len(relevance_pairs):
                raise ValueError("正文相关性复核返回数量与候选不一致")
        for (row, _review_document, full_document), decision in zip(relevance_pairs, decisions):
            _repository.update_candidate_analysis(
                row["id"],
                "accepted" if decision.relevant else "rejected",
                decision.reason,
                relevance_score=decision.score,
            )
            if decision.relevant:
                accepted_documents.append(full_document)

        if not accepted_documents:
            raise ValueError("没有可用于 MediaNode 的 accepted 正文")
        progress(f"正文恢复完成，开始 MediaNode：{len(accepted_documents)} 条")
        insights = agent.media_node.run(accepted_documents)
        prepared_analysis = {
            "query": record.query,
            "topic": record.topic,
            "read_attempted_count": len(rows),
            "read_success_count": len(stored_documents) + len(pending_documents),
            "relevant_count": len(accepted_documents),
            "media_insights": [
                asdict(item) for item in insights
                if item.source_group != "social_media"
            ],
            "social_insights": [
                asdict(item) for item in insights
                if item.source_group == "social_media"
            ],
        }
        _repository.complete_analysis_resume(
            run_id,
            execution_id,
            prepared_analysis,
            stats={
                "pending_count": len(pending_rows),
                "pending_saved_count": len(pending_documents),
                "pending_failed_count": pending_failed,
                "pending_duplicate_count": pending_duplicates,
                "accepted_count": len(accepted_documents),
            },
        )
        _index_run_knowledge(run_id)
    except _RunCanceled:
        _repository.cancel_analysis_resume(run_id, execution_id)
    except Exception as exc:
        _repository.fail_analysis_resume(run_id, execution_id, str(exc))


def _execute_prepared_brief(run_id: str, save_markdown_file: bool) -> None:
    record = _get_record(run_id)
    prepared = dict(record.metadata.get("prepared_analysis") or {})
    try:
        state = RunState(query=record.query, topic=record.topic)
        state.insights = [
            MediaInsight(**item)
            for item in [
                *list(prepared.get("media_insights") or []),
                *list(prepared.get("social_insights") or []),
            ]
            if isinstance(item, dict)
        ]
        result = _new_agent().generate_brief(state)
        _repository.complete_prepared_brief(
            run_id,
            result.brief,
            result.brief_data,
        )
        if save_markdown_file:
            saved = FinancialMediaAgent.save_brief(result.brief)
            _set_progress(run_id, f"研究完成，已保存 {saved.name}")
    except Exception as exc:
        _repository.fail_prepared_brief(run_id, str(exc))


def _execute_case_prepared_brief(case_id: str, save_markdown_file: bool) -> None:
    """Generate one report from all prepared child-run insights in a case."""
    try:
        run_prepared_case_brief(
            _repository,
            case_id,
            settings=_settings,
            agent=_new_agent(),
            save_markdown_file=save_markdown_file,
        )
    except Exception as exc:
        _repository.fail_case_brief(case_id, str(exc))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/cases/lookup", response_model=CaseLookupResponse)
def lookup_cases(request: CaseLookupRequest) -> CaseLookupResponse:
    matches = _repository.find_cases_for_lookup(request.query)
    return CaseLookupResponse(
        query=request.query,
        matches=[_to_case_match_response(
            item.case,
            match_type=item.match_type,
            matched_terms=item.matched_terms,
        ) for item in matches],
    )


@app.get("/api/v1/cases", response_model=CaseListResponse)
def list_brief_cases() -> CaseListResponse:
    cases = []
    for case in _repository.list_brief_cases():
        cases.append(CaseMatchResponse(
            case_id=case.case_id,
            case_key=case.case_key,
            query=case.query,
            topic=case.topic,
            status=case.status,
            updated_at=case.updated_at.isoformat() if case.updated_at else None,
            has_report=True,
            can_reuse=True,
            match_type="list",
        ))
    return CaseListResponse(cases=cases)


@app.post(
    "/api/v1/plans",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_plan(request: CreatePlanRequest) -> PlanResponse:
    try:
        state = _new_agent().create_plan(request.query)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"生成检索计划失败：{exc}") from exc
    run_id = uuid4().hex
    enabled_sources = {
        name for name, enabled in request.sources.items()
        if enabled and name in {"newsnow", "rss", "tavily", "weibo"}
    }
    if not enabled_sources:
        raise HTTPException(status_code=422, detail="至少需要开启一个数据源")
    case_id = uuid4().hex
    case_key = f"case-{case_id[:8]}"
    _repository.create_case(
        case_id=case_id,
        case_key=case_key,
        query=state.query,
        topic=state.topic,
    )
    _run_sources[run_id] = enabled_sources
    _repository.create(
        run_id=run_id,
        query=state.query,
        topic=state.topic,
        tavily_queries=list(state.tavily_queries),
        newsnow_rss_core=list(state.newsnow_rss_core),
        newsnow_rss_support=list(state.newsnow_rss_support),
        weibo_query=state.weibo_query,
        enabled_sources=enabled_sources,
        parent_event_id=case_id,
    )
    record = _get_record(run_id)
    return PlanResponse(
        run_id=run_id,
        case_id=case_id,
        case_key=case_key,
        query=record.query,
        topic=record.topic,
        tavily_queries=record.tavily_queries,
        newsnow_rss_core=list(record.newsnow_rss_core or []),
        newsnow_rss_support=list(record.newsnow_rss_support or []),
        weibo_query=record.weibo_query,
        status=record.status,
    )


@app.post(
    "/api/v1/plans/{run_id}/approve",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_plan(run_id: str, request: ApprovePlanRequest) -> RunResponse:
    record = _get_record(run_id)
    queries = [
        " ".join(query.split())
        for query in request.approved_tavily_queries
        if query.strip()
    ]
    queries = list(dict.fromkeys(queries))
    if not queries:
        queries = [" ".join(item.split()) for item in (record.tavily_queries or []) if item.strip()]
    if not queries:
        raise HTTPException(status_code=422, detail="缺少 Tavily Query")
    if not _repository.approve(
        run_id,
        queries,
        newsnow_rss_core=request.newsnow_rss_core,
        newsnow_rss_support=request.newsnow_rss_support,
        weibo_query=request.weibo_query,
    ):
        raise HTTPException(status_code=409, detail="该任务已经审核，不能重复批准")
    _executor.submit(_execute_run, run_id)
    return _to_run_response(_get_record(run_id))


@app.post("/api/v1/runs/{run_id}/cancel", response_model=RunResponse)
def cancel_run(run_id: str) -> RunResponse:
    _get_record(run_id)
    _cancel_events.setdefault(run_id, Event()).set()
    if not _repository.cancel(run_id):
        raise HTTPException(status_code=409, detail="任务已经结束，不能终止")
    return _to_run_response(_get_record(run_id))


@app.delete("/api/v1/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_failed_run(run_id: str) -> Response:
    _get_record(run_id)
    if not _repository.delete_failed(run_id):
        raise HTTPException(status_code=409, detail="只能删除 failed 状态的 case")
    _run_sources.pop(run_id, None)
    _cancel_events.pop(run_id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/v1/runs/{run_id}/rerun", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
def rerun_with_tavily_queries(run_id: str, request: RerunRequest) -> RunResponse:
    original = _get_record(run_id)
    original_tavily = original.tavily_queries
    tavily = request.tavily_queries
    if tavily is not None:
        tavily = [" ".join(item.split()) for item in tavily if item.strip()]
    unchanged_tavily = tavily is None or tavily == (original_tavily if isinstance(original_tavily, list) else [original_tavily])
    if unchanged_tavily:
        raise HTTPException(status_code=422, detail="请修改 Tavily Query")
    if tavily is not None:
        if not tavily:
            raise HTTPException(status_code=422, detail="Tavily Query 不能为空")
        if len(tavily) != 1:
            raise HTTPException(status_code=422, detail="每次 Tavily 搜索只使用一条 Query")
    if not _repository.begin_additional_search(run_id, tavily):
        raise HTTPException(status_code=409, detail="当前任务状态不能开始补充搜索")
    _executor.submit(_execute_additional_search, run_id)
    return _to_run_response(_get_record(run_id))


@app.post(
    "/api/v1/runs/{run_id}/sources",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def append_sources(run_id: str, request: AppendSourcesRequest) -> RunResponse:
    record = _get_record(run_id)
    allowed = {"newsnow", "rss", "tavily", "weibo"}
    selected = {
        name for name, enabled in request.sources.items()
        if enabled and name in allowed
    }
    if not selected:
        raise HTTPException(status_code=422, detail="至少选择一个追加数据源")
    tavily_queries = request.tavily_queries
    if tavily_queries is not None:
        tavily_queries = list(dict.fromkeys(
            " ".join(item.split()) for item in tavily_queries if item.strip()
        ))
        if "tavily" in selected and len(tavily_queries) != 1:
            raise HTTPException(status_code=422, detail="Tavily 每次只使用一条 Query")
    elif "tavily" in selected:
        tavily_queries = list(record.approved_tavily_queries or record.tavily_queries)
        if not tavily_queries:
            raise HTTPException(status_code=422, detail="当前 case 没有可用 Tavily Query")
    execution_id = _repository.begin_source_search(
        run_id,
        selected,
        tavily_queries=tavily_queries if "tavily" in selected else None,
    )
    if execution_id is None:
        raise HTTPException(status_code=409, detail="当前 case 状态不能追加来源")
    _cancel_events[run_id] = Event()
    _executor.submit(_execute_source_search, run_id, execution_id, selected)
    return _to_run_response(_get_record(run_id))


@app.post(
    "/api/v1/runs/{run_id}/resume-analysis",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_analysis(run_id: str, request: ResumeAnalysisRequest | None = None) -> RunResponse:
    """Resume incomplete Stage 2 work from PostgreSQL without provider crawling."""
    del request
    _get_record(run_id)
    execution_id = _repository.begin_analysis_resume(run_id)
    if execution_id is None:
        raise HTTPException(status_code=409, detail="当前 case 状态不能恢复 Stage 2")
    _cancel_events[run_id] = Event()
    _executor.submit(_execute_analysis_resume, run_id, execution_id)
    return _to_run_response(_get_record(run_id))


@app.post(
    "/api/v1/runs/{run_id}/brief",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_prepared_brief(
    run_id: str,
    request: GenerateBriefRequest,
) -> RunResponse:
    _get_record(run_id)
    if not _repository.begin_prepared_brief(run_id):
        raise HTTPException(status_code=409, detail="当前 case 尚未完成结构化分析")
    _executor.submit(_execute_prepared_brief, run_id, request.save_markdown_file)
    return _to_run_response(_get_record(run_id))


@app.get("/api/v1/cases/{case_id}", response_model=CaseResponse)
def get_case(case_id: str) -> CaseResponse:
    payload = _to_case_response(_get_case(case_id))
    response = JSONResponse(content=jsonable_encoder(payload))
    response.set_cookie("ma_open_case", payload.case_id, max_age=7 * 24 * 3600, path="/", samesite="lax")
    return response


@app.post("/api/v1/cases/{case_id}/chat", response_model=ChatResponse)
def chat_in_case(case_id: str, request: ChatRequest) -> ChatResponse:
    case = _get_case(case_id)
    try:
        result = CaseQAService(
            _repository,
            _new_agent().llm_client,
            settings=_settings,
        ).answer(case, request.question, request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"案例问答失败：{exc}") from exc
    return ChatResponse(
        case_id=result.case_id,
        mode=result.mode,
        answer=result.answer,
        citations=[ChatCitationResponse(**item.__dict__) for item in result.citations],
        evidence=[ChatEvidenceResponse(**item.__dict__) for item in result.evidence],
        retrieved_count=result.retrieved_count,
        retrieval_scope=result.retrieval_scope,
    )


@app.post("/api/v1/assistant/chat", response_model=AssistantChatResponse)
def assistant_chat(request: AssistantChatRequest) -> AssistantChatResponse:
    if request.case_id:
        _get_case(request.case_id)
    try:
        result = CaseAssistantAgent(
            _repository,
            _new_agent().llm_client,
            settings=_settings,
            memory_store=_assistant_memory,
            toolbox=AssistantToolbox(
                _repository,
                _new_agent().llm_client,
                settings=_settings,
                start_research=_start_full_research,
            ),
        ).chat(
            request.message,
            session_id=request.session_id,
            case_id=request.case_id,
            qa_mode=request.qa_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"助手问答失败：{exc}") from exc
    return AssistantChatResponse(
        session_id=result.session_id,
        case_id=result.case_id,
        answer=result.answer,
        citations=[ChatCitationResponse(**item) for item in result.citations if isinstance(item, dict)],
        evidence=[ChatEvidenceResponse(**item) for item in result.evidence if isinstance(item, dict)],
        retrieved_count=result.retrieved_count,
        retrieval_scope=result.retrieval_scope,
        tool_trace=result.tool_trace,
        pending_generation=result.pending_generation,
        started_job=result.started_job,
        job=result.job,
        open_case_id=result.open_case_id,
    )


@app.post(
    "/api/v1/cases/{case_id}/brief",
    response_model=CaseResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_case_brief(
    case_id: str,
    request: GenerateBriefRequest,
) -> CaseResponse:
    case = _get_case(case_id)
    if not _repository.begin_case_brief(case.case_id):
        raise HTTPException(status_code=409, detail="案例下尚未完成可用的结构化分析")
    _executor.submit(
        _execute_case_prepared_brief,
        case.case_id,
        request.save_markdown_file,
    )
    return _to_case_response(_get_case(case.case_id))


@app.get("/api/v1/cases/{case_id}/report")
def get_case_report(case_id: str) -> dict:
    case = _get_case(case_id)
    report = _require_completed_report(case)
    return {
        "case_id": case.case_id,
        "case_key": case.case_key,
        "report": report,
        "report_data": case.report_data,
    }


@app.get("/api/v1/cases/{case_id}/report.md")
def download_case_report_markdown(case_id: str) -> Response:
    case = _get_case(case_id)
    report = _require_completed_report(case)
    filename = f"{_filename_stem(case.topic, case.case_id)}.md"
    return Response(
        content=(report.strip() + "\n").encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@app.get("/api/v1/cases/{case_id}/report/view", response_class=HTMLResponse)
def view_case_report(
    case_id: str,
    print: bool = Query(False, description="打开后自动唤起打印/另存为 PDF"),
) -> HTMLResponse:
    case = _get_case(case_id)
    report = _require_completed_report(case)
    return HTMLResponse(
        _report_html_page(
            topic=case.topic,
            report_markdown=report,
            report_data=case.report_data,
            auto_print=print,
        )
    )


@app.get("/api/v1/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: str) -> RunResponse:
    return _to_run_response(_get_record(run_id))


@app.get("/api/v1/runs/{run_id}/candidates")
def get_candidates(run_id: str) -> dict:
    _get_record(run_id)
    rows = _repository.list_candidates(run_id)
    providers: dict[str, int] = {}
    for row in rows:
        providers[row["provider"]] = providers.get(row["provider"], 0) + 1
    return {"run_id": run_id, "counts": providers, "total": len(rows), "candidates": rows}


@app.get("/api/v1/runs/{run_id}/report")
def get_report(run_id: str) -> dict:
    record = _get_record(run_id)
    report = _require_completed_report(record)
    return {"run_id": run_id, "report": report, "report_data": record.report_data}


@app.get("/api/v1/runs/{run_id}/report.md")
def download_report_markdown(run_id: str) -> Response:
    record = _get_record(run_id)
    report = _require_completed_report(record)
    filename = f"{_filename_stem(record.topic, run_id)}.md"
    return Response(
        content=(report.strip() + "\n").encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@app.get(
    "/api/v1/runs/{run_id}/report/view",
    response_class=HTMLResponse,
)
def view_report(
    run_id: str,
    print: bool = Query(False, description="打开后自动唤起打印/另存为 PDF"),
) -> HTMLResponse:
    record = _get_record(run_id)
    report = _require_completed_report(record)
    return HTMLResponse(
        _report_html_page(
            topic=record.topic,
            report_markdown=report,
            report_data=record.report_data,
            auto_print=print,
        )
    )


@app.get(
    "/api/v1/runs/{run_id}/report.pdf",
    response_class=HTMLResponse,
)
def export_report_pdf(run_id: str) -> HTMLResponse:
    """打开排版页并自动唤起系统打印对话框，用户可选择「存储为 PDF」。"""
    record = _get_record(run_id)
    report = _require_completed_report(record)
    return HTMLResponse(
        _report_html_page(
            topic=record.topic,
            report_markdown=report,
            report_data=record.report_data,
            auto_print=True,
        )
    )


def _to_run_response(record: RunRecord) -> RunResponse:
    sources = [
        SourceResult(
            provider=str(item.get("provider") or ""),
            name=str(item.get("name") or ""),
            ok=bool(item.get("ok")),
            detail=str(item.get("detail") or ""),
        )
        for item in record.source_results
        if isinstance(item, dict)
    ]
    return RunResponse(
        run_id=record.run_id,
        case_id=record.parent_event_id,
        query=record.query,
        topic=record.topic,
        approved_tavily_queries=list(record.approved_tavily_queries),
        status=record.status,
        progress=record.progress,
        error=record.error,
        report=record.report,
        report_data=record.report_data,
        sources=sources,
        source_summary={
            "total": len(sources),
            "success": sum(1 for item in sources if item.ok),
            "failed": sum(1 for item in sources if not item.ok),
        },
        tavily_queries=list(record.tavily_queries),
        newsnow_rss_core=list(record.newsnow_rss_core or []),
        newsnow_rss_support=list(record.newsnow_rss_support or []),
        weibo_query=record.weibo_query,
        retrieval_reflection=record.retrieval_reflection,
        search_history=list(record.search_history or []),
        report_stale=bool(record.metadata.get("report_stale", False)),
    )


class _NoStoreStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


def _web_page(name: str) -> FileResponse:
    path = WEB_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="前端页面不存在")
    return FileResponse(path, headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/")
def frontend_index() -> FileResponse:
    return _web_page("index.html")


@app.get("/graph")
def graph_workspace() -> FileResponse:
    return _web_page("graph.html")


@app.get("/simulation")
def simulation_workspace() -> FileResponse:
    return _web_page("simulation.html")


@app.on_event("startup")
def initialize_database() -> None:
    _repository.backfill_case_query_fingerprints()
    _repository.mark_interrupted_runs()


def _workspace_case_from_request(request: Request, case: str | None) -> str:
    opened = (request.cookies.get("ma_open_case") or "").strip()
    requested = (case or "").strip()
    if opened and requested in {"", "case1"} and opened != "case1":
        return opened
    return requested


@app.get("/assets/graph.html")
def legacy_graph_html(request: Request, case: str | None = None):
    case_ref = _workspace_case_from_request(request, case)
    target = "/graph"
    if case_ref:
        target += f"?case={quote(case_ref)}"
    return RedirectResponse(target, status_code=302)


@app.get("/assets/simulation.html")
def legacy_simulation_html(request: Request, case: str | None = None):
    case_ref = _workspace_case_from_request(request, case)
    target = "/simulation"
    if case_ref:
        target += f"?case={quote(case_ref)}"
    return RedirectResponse(target, status_code=302)


if WEB_DIR.is_dir():
    app.mount("/assets", _NoStoreStaticFiles(directory=WEB_DIR), name="web-assets")


def main() -> None:
    import uvicorn
    from .utils.rsshub_runtime import ensure_local_rsshub

    ensure_local_rsshub()

    uvicorn.run(
        "financial_single_agent.api:app",
        host="127.0.0.1",
        port=8000,
    )
