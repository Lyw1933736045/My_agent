"""Process-local short-term session memory for Case Assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4


MAX_TURNS = 8
MAX_TOOL_SUMMARIES = 6


@dataclass
class SessionMemory:
    session_id: str
    case_id: str | None = None
    messages: list[dict] = field(default_factory=list)
    tool_summaries: list[str] = field(default_factory=list)
    pending_topic: str | None = None
    last_qa_mode: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    run_topic: str | None = None
    run_progress: str | None = None

    def add_message(self, role: str, content: str) -> None:
        text = str(content or "").strip()
        if not text:
            return
        self.messages.append({"role": role, "content": text})
        overflow = len(self.messages) - MAX_TURNS * 2
        if overflow > 0:
            self.messages = self.messages[overflow:]

    def add_tool_summary(self, summary: str) -> None:
        text = " ".join(str(summary or "").split())
        if not text:
            return
        self.tool_summaries.append(text[:400])
        overflow = len(self.tool_summaries) - MAX_TOOL_SUMMARIES
        if overflow > 0:
            self.tool_summaries = self.tool_summaries[overflow:]

    def bind_case(self, case_id: str | None) -> None:
        if case_id:
            self.case_id = case_id

    def bind_job(
        self,
        *,
        run_id: str | None,
        status: str | None = None,
        topic: str | None = None,
        case_id: str | None = None,
    ) -> None:
        if run_id:
            self.run_id = run_id
        if status:
            self.run_status = status
        if topic:
            self.run_topic = topic
        self.bind_case(case_id)

    def job_snapshot(self) -> dict | None:
        if not self.run_id:
            return None
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "topic": self.run_topic,
            "status": self.run_status,
            "progress": self.run_progress,
        }

    def recent_messages(self) -> list[dict]:
        return list(self.messages)


class SessionMemoryStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionMemory] = {}
        self._lock = Lock()

    def get_or_create(self, session_id: str | None, case_id: str | None = None) -> SessionMemory:
        key = str(session_id or "").strip() or uuid4().hex
        with self._lock:
            memory = self._sessions.get(key)
            if memory is None:
                memory = SessionMemory(session_id=key, case_id=case_id)
                self._sessions[key] = memory
            elif case_id:
                memory.case_id = case_id
            return memory
