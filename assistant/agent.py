"""Case Assistant: Function Calling loop over existing QA, RAG, and brief services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..run_repository import RunRepository
from ..utils.config import Settings
from .memory import SessionMemory, SessionMemoryStore
from .prompts import SYSTEM_PROMPT_CASE_ASSISTANT
from .tools import (
    TOOL_SCHEMAS,
    AssistantToolbox,
    parse_tool_arguments,
    summarize_tool_result,
)

MAX_TOOL_STEPS = 3


@dataclass
class AssistantResult:
    session_id: str
    case_id: str | None
    answer: str
    citations: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    retrieved_count: int = 0
    retrieval_scope: str = ""
    tool_trace: list[dict] = field(default_factory=list)
    pending_generation: bool = False
    started_job: dict | None = None
    job: dict | None = None
    open_case_id: str | None = None


class CaseAssistantAgent:
    def __init__(
        self,
        repository: RunRepository,
        llm_client,
        settings: Settings | None = None,
        *,
        memory_store: SessionMemoryStore | None = None,
        toolbox: AssistantToolbox | None = None,
    ) -> None:
        self.repository = repository
        self.llm_client = llm_client
        self.settings = settings
        self.memory_store = memory_store or SessionMemoryStore()
        self.toolbox = toolbox or AssistantToolbox(repository, llm_client, settings)

    def chat(
        self,
        message: str,
        *,
        session_id: str | None = None,
        case_id: str | None = None,
        qa_mode: str | None = None,
    ) -> AssistantResult:
        text = " ".join(str(message or "").split())
        if len(text) < 2:
            raise ValueError("问题至少需要两个字符")
        memory = self.memory_store.get_or_create(session_id, case_id)
        self._refresh_job(memory)
        memory.add_message("user", text)
        messages = self._build_messages(memory, qa_mode=qa_mode)
        tool_trace: list[dict] = []
        citations: list[dict] = []
        evidence: list[dict] = []
        retrieved_count = 0
        retrieval_scope = ""
        pending_generation = False
        started_job = None
        open_case_id = None
        answer = ""

        for _ in range(MAX_TOOL_STEPS):
            turn = self.llm_client.invoke_messages(
                messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
            )
            if not turn.tool_calls:
                answer = turn.content
                break
            messages.append(turn.raw_assistant_message)
            for call in turn.tool_calls:
                arguments = parse_tool_arguments(call.arguments)
                result = self.toolbox.execute(call.name, arguments, memory)
                tool_trace.append({
                    "name": call.name,
                    "arguments": arguments,
                    "ok": bool(result.get("ok")),
                })
                memory.add_tool_summary(summarize_tool_result(call.name, result))
                if result.get("citations"):
                    citations = list(result["citations"])
                if result.get("evidence"):
                    evidence = list(result["evidence"])
                if result.get("retrieved_count"):
                    retrieved_count = int(result["retrieved_count"])
                if result.get("retrieval_scope"):
                    retrieval_scope = str(result["retrieval_scope"])
                if result.get("found") is False:
                    pending_generation = True
                if call.name == "case_report" and arguments.get("action") == "query" and result.get("ok"):
                    mode = str(arguments.get("mode") or result.get("mode") or "")
                    if mode in {"fast", "analysis", "deep"}:
                        memory.last_qa_mode = mode
                if call.name == "case_report" and arguments.get("action") == "get" and result.get("has_report"):
                    open_case_id = str(result.get("case_id") or memory.case_id or "") or None
                if result.get("started") and result.get("run_id"):
                    started_job = {
                        "case_id": result.get("case_id"),
                        "run_id": result.get("run_id"),
                        "topic": result.get("topic"),
                        "status": result.get("status") or memory.run_status,
                        "progress": result.get("message") or memory.run_progress,
                    }
                    memory.bind_job(
                        run_id=str(result.get("run_id") or "") or None,
                        status=str(result.get("status") or "running"),
                        topic=str(result.get("topic") or "") or None,
                        case_id=str(result.get("case_id") or "") or None,
                    )
                    if started_job.get("progress"):
                        memory.run_progress = str(started_job["progress"])
                    pending_generation = False
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": _json(result),
                })
        else:
            turn = self.llm_client.invoke_messages(messages)
            answer = turn.content

        if not answer:
            answer = "当前没有得到有效回答，请换一种问法，或确认案例材料是否已经入库。"
        memory.add_message("assistant", answer)
        return AssistantResult(
            session_id=memory.session_id,
            case_id=memory.case_id,
            answer=answer,
            citations=citations,
            evidence=evidence,
            retrieved_count=retrieved_count,
            retrieval_scope=retrieval_scope,
            tool_trace=tool_trace,
            pending_generation=pending_generation,
            started_job=started_job,
            job=memory.job_snapshot(),
            open_case_id=open_case_id,
        )

    def _refresh_job(self, memory: SessionMemory) -> None:
        if not memory.run_id:
            return
        record = self.repository.get(memory.run_id)
        if record is None:
            return
        memory.run_status = record.status
        memory.run_progress = record.progress
        if record.topic:
            memory.run_topic = record.topic
        if record.parent_event_id:
            memory.bind_case(record.parent_event_id)

    def _context_text(self, memory: SessionMemory) -> str:
        lines = ["当前没有绑定案例。", "已有简报：否"]
        if memory.case_id:
            case = self.repository.get_case(memory.case_id)
            if case is not None:
                data = case.report_data if isinstance(case.report_data, dict) else {}
                has_report = bool(case.report or data)
                lines = [
                    f"当前 case_id：{case.case_id}",
                    f"主题：{case.topic}",
                    f"状态：{case.status}",
                    f"已有简报：{'是' if has_report else '否'}",
                ]
        lines.append(f"pending_topic：{memory.pending_topic or '无'}")
        if memory.last_qa_mode:
            lines.append(f"上一轮问答深度：{memory.last_qa_mode}")
        if memory.run_id:
            lines.extend([
                f"生成任务 run_id：{memory.run_id}",
                f"生成任务 status：{memory.run_status or 'unknown'}",
                f"生成任务主题：{memory.run_topic or ''}",
                f"生成任务 progress：{memory.run_progress or ''}",
            ])
        return "\n".join(lines)

    def _build_messages(self, memory: SessionMemory, *, qa_mode: str | None) -> list[dict]:
        cutoff = datetime.now().astimezone().isoformat(timespec="minutes")
        hint = ""
        if qa_mode in {"fast", "analysis", "deep"}:
            hint = f"\n本轮可参考的深度（若用户已选定）：{qa_mode}\n"
        system = (
            SYSTEM_PROMPT_CASE_ASSISTANT
            + f"\n当前数据检索时间：{cutoff}\n"
            + self._context_text(memory)
            + hint
            + (f"\n最近工具摘要：{' | '.join(memory.tool_summaries)}" if memory.tool_summaries else "")
        )
        messages = [{"role": "system", "content": system}]
        messages.extend(memory.recent_messages())
        return messages


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
