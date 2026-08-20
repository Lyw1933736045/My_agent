"""Reuse the existing case brief pipeline without a new Report Agent."""

from __future__ import annotations

from dataclasses import asdict

from ..agent import FinancialMediaAgent
from ..run_repository import RunRepository
from ..state import RunState
from ..tools.knowledge_indexer import index_case_safely
from ..tools.media_models import MediaInsight
from ..utils.config import Settings


def run_prepared_case_brief(
    repository: RunRepository,
    case_id: str,
    *,
    settings: Settings,
    agent: FinancialMediaAgent,
    save_markdown_file: bool = False,
) -> dict:
    case = repository.get_case(case_id)
    if case is None:
        raise ValueError("研究案例不存在")
    prepared = repository.aggregate_case_prepared_analysis(case.case_id)
    if not prepared:
        raise ValueError("案例下没有可用于生成简报的结构化分析")
    state = RunState(query=case.query, topic=case.topic)
    state.insights = [
        MediaInsight(**item)
        for item in [
            *list(prepared.get("media_insights") or []),
            *list(prepared.get("social_insights") or []),
        ]
        if isinstance(item, dict)
    ]
    result = agent.generate_brief(state)
    repository.complete_case_brief(case.case_id, result.brief, result.brief_data)
    index_case_safely(repository, case.case_id, settings)
    saved_name = ""
    if save_markdown_file:
        saved = FinancialMediaAgent.save_brief(result.brief)
        saved_name = saved.name
        repository.update_case_progress(case.case_id, f"统一研究完成，已保存 {saved.name}")
    data = result.brief_data if isinstance(result.brief_data, dict) else {}
    return {
        "ok": True,
        "case_id": case.case_id,
        "topic": case.topic,
        "title": data.get("title") or case.topic,
        "summary": list(data.get("executive_summary") or [])[:5],
        "saved_file": saved_name,
    }
