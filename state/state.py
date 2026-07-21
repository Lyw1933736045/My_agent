"""研究流程的最小状态模型。"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Search:
    query: str
    title: str
    url: str
    published_date: Optional[str]
    source: str
    content: str
    score: Optional[float] = None
    timestamp: str = field(default_factory=now_iso)


@dataclass
class SourceDocument:
    """用户指定的官方原始文件及实际抓取内容。"""

    official_url: str
    final_url: str
    fetched_at: str
    content_type: str
    content: str
    requested_url: str = ""
    redirected: bool = False
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    trust_level: Optional[str] = None
    source_priority: Optional[int] = None
    domain_verified: bool = False
    path_verified: bool = False
    verification_status: str = "unverified"
    verification_message: str = ""


@dataclass
class EventFact:
    """仅从官方原文中能够确认的事件事实。"""

    title: Optional[str] = None
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    document_number: Optional[str] = None
    core_facts: Optional[list[str]] = None


@dataclass
class Research:
    search_history: list[Search] = field(default_factory=list)
    latest_summary: str = ""
    reflection_iteration: int = 0
    is_completed: bool = False

    def add_search_results(self, query: str, results: list[dict]) -> None:
        for result in results:
            self.search_history.append(
                Search(
                    query=query,
                    title=result.get("title", ""),
                    url=result.get("url", ""),
                    published_date=result.get("published_date"),
                    source=result.get("source", ""),
                    content=result.get("content", ""),
                    score=result.get("score"),
                )
            )


@dataclass
class Paragraph:
    title: str
    content: str
    research: Research = field(default_factory=Research)


@dataclass
class State:
    query: str = ""
    report_title: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)
    final_report: str = ""
    data_cutoff: str = ""
    started_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    is_completed: bool = False
    source_document: Optional[SourceDocument] = None
    event_fact: Optional[EventFact] = None

    def touch(self) -> None:
        self.updated_at = now_iso()

    def source_list(self) -> list[Search]:
        unique: dict[str, Search] = {}
        for paragraph in self.paragraphs:
            for search in paragraph.research.search_history:
                if search.url:
                    unique.setdefault(search.url, search)
        return list(unique.values())

    def save_to_file(self, filepath: str | Path) -> None:
        Path(filepath).write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
