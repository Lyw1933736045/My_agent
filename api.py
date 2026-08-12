"""My_agent 最小 FastAPI 服务：规划、人工审核、后台执行与结果查询。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from html import escape
import json
from threading import Event
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

from .agent import FinancialMediaAgent
from .evaluation.judge import evaluate, load_case
from .evaluation.metrics import summarize
from .evaluation.rubric_builder import build_rubrics
from .evaluation.run_eval import _client
from .evaluation.snapshot import write_snapshot, write_weibo_raw
from .run_repository import RunRecord, RunRepository
from .state import RunState
from .tools.media_models import DiscoveryResult
from .utils.config import PROJECT_ROOT, Settings

WEB_DIR = Path(__file__).resolve().parent / "web"


RunStatus = Literal[
    "waiting_for_review",
    "running",
    "completed",
    "failed",
    "canceled",
]


class CreatePlanRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    sources: dict[str, bool] = Field(default_factory=lambda: {
        "newsnow": False, "rss": False, "tavily": True, "weibo": False
    })


class ApprovePlanRequest(BaseModel):
    approved_queries: list[str] = Field(min_length=1, max_length=10)


class PlanResponse(BaseModel):
    run_id: str
    query: str
    topic: str
    proposed_queries: list[str]
    status: RunStatus


class SourceResult(BaseModel):
    provider: str
    name: str
    ok: bool
    detail: str = ""


class RunResponse(BaseModel):
    run_id: str
    query: str
    topic: str
    approved_queries: list[str]
    status: RunStatus
    progress: str
    error: str | None = None
    report: str | None = None
    sources: list[SourceResult] = Field(default_factory=list)
    source_summary: dict[str, int] = Field(default_factory=dict)
    provider_queries: dict = Field(default_factory=dict)
    retrieval_reflection: dict = Field(default_factory=dict)


class RerunRequest(BaseModel):
    tavily_queries: list[str] | None = None
    weibo_query: str | None = None


class EvaluateRequest(BaseModel):
    reference: str = Field(min_length=2, max_length=100_000)


class EvaluationResponse(BaseModel):
    run_id: str
    status: Literal["pending", "running", "completed", "failed"]
    summary: dict | None = None
    error: str | None = None


app = FastAPI(
    title="My_agent API",
    version="0.1.0",
    description="金融媒体研究 Agent 的规划、人工审核与执行接口。",
)

_repository = RunRepository(PROJECT_ROOT / "data" / "my_agent.db")
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="my-agent")
_evaluation_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="my-agent-eval")
_cancel_events: dict[str, Event] = {}
_run_sources: dict[str, set[str]] = {}


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


def _set_progress(run_id: str, message: str) -> None:
    _repository.update_progress(run_id, message)


def _save_stage1(run_id: str, agent: FinancialMediaAgent, state: RunState) -> None:
    discovery = state.discovery
    if discovery is None:
        return
    candidates = discovery.raw_candidates or discovery.candidates
    rows = [
        {
            "title": item.title,
            "url": item.url,
            "snippet": item.snippet,
            "source": item.source_name,
            "provider": item.discovered_by[0] if item.discovered_by else "unknown",
            "source_group": item.source_group,
            "query": item.query,
            "published_at": item.published_at,
        }
        for item in candidates
        if item.url.strip()
    ]
    _repository.save_candidates(run_id, rows)
    _set_progress(run_id, f"Stage 1 已保存 {len(rows)} 条候选，等待 Stage 2 分析")


def _evaluation_dir(run_id: str) -> Path:
    return PROJECT_ROOT / "data" / "evaluations" / run_id


def _evaluation_status(run_id: str) -> dict:
    path = _evaluation_dir(run_id) / "status.json"
    if not path.is_file():
        return {"run_id": run_id, "status": "pending"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"run_id": run_id, "status": "pending"}
    return payload if isinstance(payload, dict) else {"run_id": run_id, "status": "pending"}


def _write_evaluation_status(run_id: str, **payload) -> None:
    directory = _evaluation_dir(run_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "status.json").write_text(
        json.dumps({"run_id": run_id, **payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _execute_evaluation(run_id: str) -> None:
    directory = _evaluation_dir(run_id)
    try:
        _write_evaluation_status(run_id, status="running")
        rubrics = build_rubrics(
            _client(), directory / "reference.md", directory / "rubrics.json", overwrite=True
        )
        documents = json.loads((directory / "retrieved_documents.json").read_text(encoding="utf-8"))
        report = (directory / "report.md").read_text(encoding="utf-8")
        result = evaluate(_client(), rubrics, documents, report)
        summary = summarize(result)
        (directory / "result.json").write_text(
            json.dumps({"summary": summary, "judgments": result.model_dump()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _write_evaluation_status(run_id, status="completed", summary=summary)
    except Exception as exc:
        _write_evaluation_status(run_id, status="failed", error=str(exc))


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


def _require_completed_report(record: RunRecord) -> str:
    if record.status == "failed":
        raise HTTPException(status_code=409, detail=record.error or "研究任务失败")
    if record.status != "completed":
        raise HTTPException(status_code=409, detail="研究任务尚未完成")
    return record.report or ""


def _report_html_page(
    *,
    topic: str,
    report_markdown: str,
    auto_print: bool = False,
) -> str:
    report_html = _markdown.render(report_markdown)
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
    .print-hint {{ margin: 0 0 18px; color: #64748b; font-size: 0.92rem; }}
    @media (max-width: 720px) {{
      main {{ margin: 0; padding: 24px 20px; border-radius: 0; }}
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
            media_queries=list(record.approved_queries),
            provider_queries=dict(record.provider_queries),
        )
        agent = _new_agent(
            progress=progress,
            enabled_sources=_run_sources.get(run_id, {"tavily"}),
        )
        state = agent.discover_from_plan(
            state, cancel_check=cancel_event.is_set
        )
        if cancel_event.is_set():
            raise _RunCanceled()
        _save_stage1(run_id, agent, state)
        sources = _sources_payload(state.discovery)
        _repository.save_source_results(run_id, sources)
        _repository.save_retrieval_reflection(run_id, state.retrieval_reflection)
        write_weibo_raw(state, _evaluation_dir(run_id))
        result = agent.complete(state, cancel_check=cancel_event.is_set)
        if cancel_event.is_set():
            raise _RunCanceled()
        write_snapshot(state, _evaluation_dir(run_id))
        _repository.complete(run_id, result.brief, source_results=sources)
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    enabled_sources = {name for name, enabled in request.sources.items() if enabled}
    if not enabled_sources:
        raise HTTPException(status_code=422, detail="至少需要开启一个数据源")
    _run_sources[run_id] = enabled_sources
    _repository.create(
        run_id=run_id,
        query=state.query,
        topic=state.topic,
        proposed_queries=list(state.media_queries),
        provider_queries=dict(state.provider_queries),
    )
    record = _get_record(run_id)
    return PlanResponse(
        run_id=run_id,
        query=record.query,
        topic=record.topic,
        proposed_queries=record.proposed_queries,
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
        for query in request.approved_queries
        if query.strip()
    ]
    queries = list(dict.fromkeys(queries))
    if not queries:
        raise HTTPException(status_code=422, detail="至少需要一个有效检索词")
    if not _repository.approve(run_id, queries):
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


@app.post("/api/v1/runs/{run_id}/rerun", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
def rerun_with_provider_queries(run_id: str, request: RerunRequest) -> RunResponse:
    original = _get_record(run_id)
    original_tavily = original.provider_queries.get("tavily", [])
    original_weibo = original.provider_queries.get("weibo", "")
    tavily = request.tavily_queries
    if tavily is not None:
        tavily = [" ".join(item.split()) for item in tavily if item.strip()]
    weibo = " ".join((request.weibo_query or "").split()) if request.weibo_query is not None else None
    unchanged_tavily = tavily is None or tavily == (original_tavily if isinstance(original_tavily, list) else [original_tavily])
    unchanged_weibo = weibo is None or weibo == original_weibo
    if unchanged_tavily and unchanged_weibo:
        raise HTTPException(status_code=422, detail="请至少修改 Tavily 或微博 Query 中的一个")
    provider_queries = dict(original.provider_queries)
    if tavily is not None:
        if not tavily:
            raise HTTPException(status_code=422, detail="Tavily Query 不能为空")
        provider_queries["tavily"] = tavily
    if weibo is not None:
        if not weibo:
            raise HTTPException(status_code=422, detail="微博 Query 不能为空")
        provider_queries["weibo"] = weibo
    new_id = uuid4().hex
    _run_sources[new_id] = _run_sources.get(run_id, {"tavily"})
    _repository.create(
        run_id=new_id,
        query=original.query,
        topic=original.topic,
        proposed_queries=list(original.proposed_queries),
        provider_queries=provider_queries,
    )
    _repository.approve(new_id, list(original.approved_queries))
    _executor.submit(_execute_run, new_id)
    return _to_run_response(_get_record(new_id))


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
def get_report(run_id: str) -> dict[str, str]:
    record = _get_record(run_id)
    report = _require_completed_report(record)
    return {"run_id": run_id, "report": report}


@app.post("/api/v1/runs/{run_id}/evaluate", response_model=EvaluationResponse, status_code=status.HTTP_202_ACCEPTED)
def start_evaluation(run_id: str, request: EvaluateRequest) -> EvaluationResponse:
    record = _get_record(run_id)
    if record.status != "completed":
        raise HTTPException(status_code=409, detail="报告尚未完成，不能开始评测")
    directory = _evaluation_dir(run_id)
    if not (directory / "retrieved_documents.json").is_file():
        raise HTTPException(status_code=409, detail="本次运行缺少检索材料快照")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reference.md").write_text(request.reference.strip() + "\n", encoding="utf-8")
    _write_evaluation_status(run_id, status="pending")
    _evaluation_executor.submit(_execute_evaluation, run_id)
    return EvaluationResponse(run_id=run_id, status="pending")


@app.get("/api/v1/runs/{run_id}/evaluation", response_model=EvaluationResponse)
def get_evaluation(run_id: str) -> EvaluationResponse:
    _get_record(run_id)
    return EvaluationResponse.model_validate(_evaluation_status(run_id))


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
        query=record.query,
        topic=record.topic,
        approved_queries=list(record.approved_queries),
        status=record.status,
        progress=record.progress,
        error=record.error,
        report=record.report,
        sources=sources,
        source_summary={
            "total": len(sources),
            "success": sum(1 for item in sources if item.ok),
            "failed": sum(1 for item in sources if not item.ok),
        },
        provider_queries=record.provider_queries,
        retrieval_reflection=record.retrieval_reflection,
    )


@app.get("/")
def frontend_index() -> FileResponse:
    index = WEB_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="前端页面不存在")
    return FileResponse(index)


if WEB_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIR), name="web-assets")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "financial_single_agent.api:app",
        host="127.0.0.1",
        port=8000,
    )
