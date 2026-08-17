"""PostgreSQL persistence for research events and raw news documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlparse

from sqlalchemy import create_engine, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from .knowledge.models import Document, Event, EventDocument, new_id
from .utils.dedup import canonical_url


def normalize_case_query(query: str) -> str:
    """Normalize only superficial formatting for safe exact-case lookup."""
    value = unicodedata.normalize("NFKC", str(query or "")).casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value


def case_query_fingerprint(query: str) -> str:
    return hashlib.sha256(normalize_case_query(query).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    query: str
    topic: str
    tavily_queries: list[str]
    approved_tavily_queries: list[str]
    status: str
    progress: str
    report: str | None
    error: str | None
    source_results: list[dict]
    retrieval_reflection: dict
    enabled_sources: list[str]
    report_data: dict = field(default_factory=dict)
    weibo_query: str = ""
    newsnow_rss_core: list[str] = field(default_factory=list)
    newsnow_rss_support: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    search_history: list[dict] = field(default_factory=list)
    parent_event_id: str | None = None
    event_type: str = "run"


@dataclass(frozen=True)
class CaseRecord:
    case_id: str
    case_key: str
    query: str
    topic: str
    status: str
    progress: str
    report: str | None
    report_data: dict
    error: str | None
    metadata: dict
    updated_at: datetime | None = None
    child_runs: list[RunRecord] = field(default_factory=list)


@dataclass(frozen=True)
class CaseLookupMatch:
    case: CaseRecord
    matched_terms: list[str] = field(default_factory=list)
    match_type: str = "term"


def _hash(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _clean_text(value: str | None) -> str | None:
    """PostgreSQL text cannot contain NUL bytes returned by some web decoders."""
    if value is None:
        return None
    return value.replace("\x00", "")


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class RunRepository:
    """Keep the existing repository interface while storing everything in PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str) or not database_url.startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            raise ValueError("DATABASE_URL 必须是 PostgreSQL 连接")
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        self.database_url = database_url
        self.engine = create_engine(database_url, pool_pre_ping=True)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _record(event: Event) -> RunRecord:
        plan = event.search_plan or {}
        return RunRecord(
            run_id=event.id,
            query=event.question,
            topic=event.title,
            tavily_queries=list(plan.get("tavily_queries") or []),
            approved_tavily_queries=list(plan.get("approved_tavily_queries") or []),
            status=event.status,
            progress=event.progress,
            report=event.report_markdown,
            report_data=dict(event.report_data or {}),
            error=event.error_message,
            source_results=list(event.source_results or []),
            retrieval_reflection=dict(event.retrieval_reflection or {}),
            enabled_sources=list(event.enabled_sources or []),
            newsnow_rss_core=list(plan.get("newsnow_rss_core") or []),
            newsnow_rss_support=list(plan.get("newsnow_rss_support") or []),
            weibo_query=str(plan.get("weibo_query") or ""),
            metadata=dict(event.metadata_json or {}),
            search_history=list(plan.get("search_history") or []),
            parent_event_id=event.parent_event_id,
            event_type=event.event_type,
        )

    def _get_event(self, session: Session, run_id: str) -> Event | None:
        return session.get(Event, run_id)

    def create_case(
        self,
        *,
        case_id: str,
        case_key: str,
        query: str,
        topic: str,
    ) -> None:
        """Create the stable parent record that owns independent source runs."""
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            session.add(Event(
                id=case_id,
                event_type="case",
                case_key=case_key,
                query_fingerprint=case_query_fingerprint(query),
                question=query,
                title=topic,
                status="case_open",
                progress="案例已创建，等待数据源子 run 完成",
                enabled_sources=[],
                search_plan={},
                source_results=[],
                retrieval_reflection={},
                metadata_json={"case_key": case_key},
                created_at=now,
                updated_at=now,
            ))

    def create(
        self,
        *,
        run_id: str,
        query: str,
        topic: str,
        tavily_queries: list[str],
        newsnow_rss_core: list[str] | None = None,
        newsnow_rss_support: list[str] | None = None,
        weibo_query: str = "",
        enabled_sources: set[str] | None = None,
        parent_event_id: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        plan = {
            "tavily_queries": tavily_queries,
            "newsnow_rss_core": newsnow_rss_core or [],
            "newsnow_rss_support": newsnow_rss_support or [],
            "weibo_query": weibo_query,
            "approved_tavily_queries": [],
            "review_status": "pending",
        }
        with self._sessions.begin() as session:
            session.add(Event(
                id=run_id,
                event_type="run",
                parent_event_id=parent_event_id,
                question=query,
                title=topic,
                status="waiting_for_review",
                progress="等待人工审核检索词",
                enabled_sources=sorted(enabled_sources or {"newsnow", "rss"}),
                search_plan=plan,
                source_results=[],
                retrieval_reflection={},
                metadata_json={},
                created_at=now,
                updated_at=now,
            ))

    def get(self, run_id: str) -> RunRecord | None:
        with self._sessions() as session:
            event = self._get_event(session, run_id)
            return self._record(event) if event else None

    def get_case(self, case_ref: str) -> CaseRecord | None:
        """Resolve a case by its parent id or human-readable case key."""
        with self._sessions() as session:
            parent = session.scalar(
                select(Event).where(
                    Event.event_type == "case",
                    or_(Event.id == case_ref, Event.case_key == case_ref),
                )
            )
            if parent is None:
                return None
            children = session.scalars(
                select(Event)
                .where(Event.parent_event_id == parent.id, Event.event_type == "run")
                .order_by(Event.created_at, Event.id)
            ).all()
            return CaseRecord(
                case_id=parent.id,
                case_key=str(parent.case_key or parent.id),
                query=parent.question,
                topic=parent.title,
                status=parent.status,
                progress=parent.progress,
                report=parent.report_markdown,
                report_data=dict(parent.report_data or {}),
                error=parent.error_message,
                metadata=dict(parent.metadata_json or {}),
                updated_at=parent.updated_at,
                child_runs=[self._record(child) for child in children],
            )

    def find_cases_by_query(self, query: str) -> list[CaseRecord]:
        """Find exact normalized-query matches, newest case first."""
        return [item.case for item in self.find_cases_for_lookup(query)
                if item.match_type == "exact"]

    def find_cases_for_lookup(self, query: str) -> list[CaseLookupMatch]:
        """Find exact cases and cases whose saved core/support terms are hit."""
        fingerprint = case_query_fingerprint(query)
        normalized_query = normalize_case_query(query)
        compact_query = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized_query)
        with self._sessions() as session:
            parents = session.scalars(
                select(Event)
                .where(Event.event_type == "case")
                .order_by(Event.updated_at.desc(), Event.id)
            ).all()
            result = []
            for parent in parents:
                children = session.scalars(
                    select(Event)
                    .where(
                        Event.parent_event_id == parent.id,
                        Event.event_type == "run",
                    )
                    .order_by(Event.created_at, Event.id)
                ).all()
                case = CaseRecord(
                    case_id=parent.id,
                    case_key=str(parent.case_key or parent.id),
                    query=parent.question,
                    topic=parent.title,
                    status=parent.status,
                    progress=parent.progress,
                    report=parent.report_markdown,
                    report_data=dict(parent.report_data or {}),
                    error=parent.error_message,
                    metadata=dict(parent.metadata_json or {}),
                    updated_at=parent.updated_at,
                    child_runs=[self._record(child) for child in children],
                )
                if parent.query_fingerprint == fingerprint or normalize_case_query(parent.question) == normalized_query:
                    result.append(CaseLookupMatch(
                        case=case,
                        matched_terms=[parent.question],
                        match_type="exact",
                    ))
                    continue

                terms = [parent.question]
                for child in case.child_runs:
                    terms.extend(child.newsnow_rss_core)
                    terms.extend(child.newsnow_rss_support)
                matched_terms = []
                seen_terms: set[str] = set()
                for term in terms:
                    display_term = " ".join(str(term or "").split()).strip()
                    compact_term = re.sub(
                        r"[^\w\u4e00-\u9fff]+", "",
                        normalize_case_query(display_term),
                    )
                    if len(compact_term) < 2 or compact_term not in compact_query:
                        continue
                    if compact_term in seen_terms:
                        continue
                    seen_terms.add(compact_term)
                    matched_terms.append(display_term)
                if matched_terms:
                    result.append(CaseLookupMatch(
                        case=case,
                        matched_terms=matched_terms,
                        match_type="term",
                    ))
            result.sort(key=lambda item: (
                0 if item.match_type == "exact" else 1,
                -(item.case.updated_at.timestamp() if item.case.updated_at else 0),
            ))
            return result

    def backfill_case_query_fingerprints(self) -> int:
        """Backfill fingerprints for parent cases created before the lookup field."""
        changed = 0
        with self._sessions.begin() as session:
            parents = session.scalars(
                select(Event).where(
                    Event.event_type == "case",
                    Event.query_fingerprint.is_(None),
                )
            ).all()
            for parent in parents:
                parent.query_fingerprint = case_query_fingerprint(parent.question)
                parent.updated_at = datetime.now(timezone.utc)
                changed += 1
        return changed

    def attach_runs_to_case(self, case_ref: str, run_ids: list[str]) -> bool:
        """Attach existing legacy runs to a case without moving their documents."""
        with self._sessions.begin() as session:
            parent = session.scalar(
                select(Event).where(
                    Event.event_type == "case",
                    or_(Event.id == case_ref, Event.case_key == case_ref),
                )
            )
            if parent is None:
                return False
            for run_id in run_ids:
                child = session.get(Event, run_id)
                if child is None or child.event_type != "run":
                    return False
                if child.parent_event_id not in {None, parent.id}:
                    return False
            now = datetime.now(timezone.utc)
            for run_id in run_ids:
                child = session.get(Event, run_id)
                child.parent_event_id = parent.id
                child.updated_at = now
            parent.progress = f"已归档 {len(run_ids)} 个数据源子 run，等待生成统一简报"
            parent.updated_at = now
            return True

    def aggregate_case_prepared_analysis(self, case_ref: str) -> dict | None:
        """Merge accepted structured insights from all usable child runs."""
        case = self.get_case(case_ref)
        if case is None:
            return None

        media: dict[str, dict] = {}
        social: dict[str, dict] = {}
        child_summaries: list[dict] = []
        read_attempted = 0
        read_success = 0

        def insight_key(item: dict) -> str:
            url = str(item.get("url") or "").strip()
            if url:
                return canonical_url(url)
            return "|".join([
                str(item.get("source_name") or ""),
                str(item.get("title") or ""),
            ])

        def add_items(target: dict[str, dict], items: list, run_id: str) -> None:
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                metadata = dict(item.get("metadata") or {})
                run_ids = list(metadata.get("case_run_ids") or [])
                if run_id not in run_ids:
                    run_ids.append(run_id)
                metadata["case_run_ids"] = run_ids
                item["metadata"] = metadata
                key = insight_key(item)
                if key not in target:
                    target[key] = item
                    continue
                existing = target[key]
                existing_metadata = dict(existing.get("metadata") or {})
                existing_run_ids = list(existing_metadata.get("case_run_ids") or [])
                for value in run_ids:
                    if value not in existing_run_ids:
                        existing_run_ids.append(value)
                existing_metadata["case_run_ids"] = existing_run_ids
                existing["metadata"] = existing_metadata

        for child in case.child_runs:
            prepared = dict(child.metadata.get("prepared_analysis") or {})
            if not prepared:
                continue
            media_items = list(prepared.get("media_insights") or [])
            social_items = list(prepared.get("social_insights") or [])
            add_items(media, media_items, child.run_id)
            add_items(social, social_items, child.run_id)
            read_attempted += int(prepared.get("read_attempted_count") or 0)
            read_success += int(prepared.get("read_success_count") or 0)
            child_summaries.append({
                "run_id": child.run_id,
                "status": child.status,
                "enabled_sources": child.enabled_sources,
                "media_insight_count": len(media_items),
                "social_insight_count": len(social_items),
            })

        prepared = {
            "query": case.query,
            "topic": case.topic,
            "case_id": case.case_id,
            "case_key": case.case_key,
            "child_run_ids": [item["run_id"] for item in child_summaries],
            "child_runs": child_summaries,
            "read_attempted_count": read_attempted,
            "read_success_count": read_success,
            "relevant_count": len(media) + len(social),
            "media_insights": list(media.values()),
            "social_insights": list(social.values()),
        }
        if not prepared["media_insights"] and not prepared["social_insights"]:
            return None
        return prepared

    def begin_case_brief(self, case_ref: str) -> bool:
        prepared = self.aggregate_case_prepared_analysis(case_ref)
        if not prepared:
            return False
        with self._sessions.begin() as session:
            parent = session.scalar(
                select(Event).where(
                    Event.event_type == "case",
                    or_(Event.id == case_ref, Event.case_key == case_ref),
                )
            )
            if parent is None or parent.status == "running":
                return False
            metadata = dict(parent.metadata_json or {})
            metadata["prepared_analysis"] = prepared
            metadata["report_stale"] = bool(parent.report_markdown or parent.report_data)
            parent.metadata_json = metadata
            parent.status = "running"
            parent.progress = "正在根据所有子 run 的结构化分析生成统一简报"
            parent.error_message = None
            parent.updated_at = datetime.now(timezone.utc)
            return True

    def complete_case_brief(self, case_ref: str, report: str, report_data: dict) -> None:
        case = self.get_case(case_ref)
        if case is None:
            return
        self.complete(case.case_id, report, report_data=report_data)
        with self._sessions.begin() as session:
            parent = session.get(Event, case.case_id)
            if parent:
                metadata = dict(parent.metadata_json or {})
                metadata["report_stale"] = False
                parent.metadata_json = metadata

    def fail_case_brief(self, case_ref: str, error: str) -> None:
        case = self.get_case(case_ref)
        if case is None:
            return
        with self._sessions.begin() as session:
            parent = session.get(Event, case.case_id)
            if parent:
                parent.status = "case_open"
                parent.progress = "统一简报生成失败，可重试"
                parent.error_message = error
                parent.updated_at = datetime.now(timezone.utc)

    def update_case_progress(self, case_ref: str, message: str) -> None:
        case = self.get_case(case_ref)
        if case is None:
            return
        with self._sessions.begin() as session:
            parent = session.get(Event, case.case_id)
            if parent:
                parent.progress = message
                parent.updated_at = datetime.now(timezone.utc)

    def save_prepared_analysis(self, run_id: str, prepared_analysis: dict) -> None:
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event:
                metadata = dict(event.metadata_json or {})
                metadata["prepared_analysis"] = prepared_analysis
                metadata["report_stale"] = bool(event.report_markdown or event.report_data)
                event.metadata_json = metadata
                event.updated_at = datetime.now(timezone.utc)

    def approve(self, run_id: str, approved_tavily_queries: list[str]) -> bool:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None or event.status != "waiting_for_review":
                return False
            plan = dict(event.search_plan or {})
            plan.update({
                "approved_tavily_queries": approved_tavily_queries,
                "review_status": "approved",
                "reviewed_at": now.isoformat(),
            })
            event.search_plan = plan
            event.status = "running"
            event.progress = "已批准，等待后台执行"
            event.updated_at = now
            return True

    def begin_additional_search(
        self, run_id: str, tavily_queries: list[str]
    ) -> bool:
        """在同一事件上开始补充搜索，不创建第二份候选结果集。"""
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None or event.status in {"waiting_for_review", "running", "canceled"}:
                return False
            plan = dict(event.search_plan or {})
            history = list(plan.get("search_history") or [])
            history.append({
                "started_at": now.isoformat(),
                "tavily_queries": tavily_queries,
            })
            plan["tavily_queries"] = tavily_queries
            plan["search_history"] = history
            event.search_plan = plan
            event.status = "running"
            event.progress = "补充搜索已提交"
            event.error_message = None
            event.updated_at = now
            return True

    def begin_source_search(
        self,
        run_id: str,
        sources: set[str],
        tavily_queries: list[str] | None = None,
    ) -> str | None:
        """Start another provider execution while keeping one stable case/run id."""
        now = datetime.now(timezone.utc)
        execution_id = new_id()
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None or event.status in {"waiting_for_review", "running", "canceled"}:
                return None
            plan = dict(event.search_plan or {})
            history = list(plan.get("search_history") or [])
            history.append({
                "execution_id": execution_id,
                "sources": sorted(sources),
                "started_at": now.isoformat(),
                "completed_at": None,
                "status": "running",
                "new_count": 0,
                "duplicate_count": 0,
                "total_unique_count": 0,
                "error": None,
            })
            if tavily_queries is not None:
                plan["tavily_queries"] = tavily_queries
                plan["approved_tavily_queries"] = tavily_queries
            plan["search_history"] = history
            metadata = dict(event.metadata_json or {})
            metadata["report_stale"] = bool(event.report_markdown or event.report_data)
            metadata["active_execution_id"] = execution_id
            event.search_plan = plan
            event.metadata_json = metadata
            event.enabled_sources = sorted(set(event.enabled_sources or []) | set(sources))
            event.status = "running"
            event.progress = f"正在追加来源：{'、'.join(sorted(sources))}"
            event.error_message = None
            event.updated_at = now
            return execution_id

    def complete_source_search(
        self,
        run_id: str,
        execution_id: str,
        stats: dict,
        prepared_analysis: dict,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None:
                return
            plan = dict(event.search_plan or {})
            history = list(plan.get("search_history") or [])
            for item in history:
                if item.get("execution_id") == execution_id:
                    item.update({
                        "status": "completed",
                        "completed_at": now.isoformat(),
                        "new_count": int(stats.get("new_count", 0)),
                        "duplicate_count": int(stats.get("duplicate_count", 0)),
                        "total_unique_count": int(stats.get("total_unique_count", 0)),
                        "error": None,
                    })
                    break
            plan["search_history"] = history
            metadata = dict(event.metadata_json or {})
            metadata["prepared_analysis"] = prepared_analysis
            metadata["active_execution_id"] = None
            metadata["report_stale"] = bool(event.report_markdown or event.report_data)
            event.search_plan = plan
            event.metadata_json = metadata
            event.status = "analysis_ready"
            event.progress = "数据抓取和结构化分析完成，等待生成简报"
            event.error_message = None
            event.updated_at = now

    def begin_analysis_resume(self, run_id: str) -> str | None:
        """Start Stage 2 recovery from the candidates already stored in PostgreSQL."""
        now = datetime.now(timezone.utc)
        execution_id = new_id()
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None or event.status in {"waiting_for_review", "running", "canceled"}:
                return None
            metadata = dict(event.metadata_json or {})
            history = list(metadata.get("analysis_resume_history") or [])
            history.append({
                "execution_id": execution_id,
                "started_at": now.isoformat(),
                "completed_at": None,
                "status": "running",
                "error": None,
            })
            metadata["analysis_resume_history"] = history
            metadata["active_analysis_resume_id"] = execution_id
            metadata["report_stale"] = bool(event.report_markdown or event.report_data)
            event.metadata_json = metadata
            event.status = "running"
            event.progress = "正在从 PostgreSQL 恢复 Stage 2"
            event.error_message = None
            event.updated_at = now
            return execution_id

    def complete_analysis_resume(
        self,
        run_id: str,
        execution_id: str,
        prepared_analysis: dict,
        stats: dict[str, int] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        stats = stats or {}
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None:
                return
            metadata = dict(event.metadata_json or {})
            history = [dict(item) for item in (metadata.get("analysis_resume_history") or [])]
            for item in history:
                if item.get("execution_id") == execution_id:
                    item.update({
                        "status": "completed",
                        "completed_at": now.isoformat(),
                        **{key: int(value) for key, value in stats.items()},
                        "error": None,
                    })
                    break
            metadata["analysis_resume_history"] = history
            metadata["active_analysis_resume_id"] = None
            metadata["prepared_analysis"] = prepared_analysis
            metadata["report_stale"] = bool(event.report_markdown or event.report_data)
            event.metadata_json = metadata
            event.status = "analysis_ready"
            event.progress = "Stage 2 断点恢复完成，等待生成简报"
            event.error_message = None
            event.updated_at = now

    def fail_analysis_resume(self, run_id: str, execution_id: str, error: str) -> None:
        now = datetime.now(timezone.utc)
        error = _clean_text(error) or "Stage 2 断点恢复失败"
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None:
                return
            metadata = dict(event.metadata_json or {})
            history = [dict(item) for item in (metadata.get("analysis_resume_history") or [])]
            for item in history:
                if item.get("execution_id") == execution_id:
                    item.update({
                        "status": "failed",
                        "completed_at": now.isoformat(),
                        "error": error,
                    })
                    break
            metadata["analysis_resume_history"] = history
            metadata["active_analysis_resume_id"] = None
            event.metadata_json = metadata
            event.status = "failed"
            event.progress = "Stage 2 断点恢复失败"
            event.error_message = error
            event.updated_at = now

    def cancel_analysis_resume(self, run_id: str, execution_id: str) -> None:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None:
                return
            metadata = dict(event.metadata_json or {})
            history = [dict(item) for item in (metadata.get("analysis_resume_history") or [])]
            for item in history:
                if item.get("execution_id") == execution_id:
                    item.update({
                        "status": "canceled",
                        "completed_at": now.isoformat(),
                    })
                    break
            metadata["analysis_resume_history"] = history
            metadata["active_analysis_resume_id"] = None
            event.metadata_json = metadata
            event.updated_at = now

    def fail_source_search(self, run_id: str, execution_id: str, error: str) -> None:
        now = datetime.now(timezone.utc)
        error = _clean_text(error) or "追加来源失败"
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None:
                return
            plan = dict(event.search_plan or {})
            history = list(plan.get("search_history") or [])
            for item in history:
                if item.get("execution_id") == execution_id:
                    item.update({
                        "status": "failed",
                        "completed_at": now.isoformat(),
                        "error": error,
                    })
                    break
            plan["search_history"] = history
            metadata = dict(event.metadata_json or {})
            metadata["active_execution_id"] = None
            event.search_plan = plan
            event.metadata_json = metadata
            event.status = "failed"
            event.progress = "追加来源失败"
            event.error_message = error
            event.updated_at = now

    def complete_prepared_brief(
        self,
        run_id: str,
        report: str,
        report_data: dict,
    ) -> None:
        self.complete(run_id, report, report_data=report_data)
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event:
                metadata = dict(event.metadata_json or {})
                metadata["report_stale"] = False
                event.metadata_json = metadata

    def begin_prepared_brief(self, run_id: str) -> bool:
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            prepared = dict(event.metadata_json or {}).get("prepared_analysis") if event else None
            if event is None or event.status != "analysis_ready" or not prepared:
                return False
            event.status = "running"
            event.progress = "正在根据结构化分析生成简报"
            event.error_message = None
            event.updated_at = datetime.now(timezone.utc)
            return True

    def fail_prepared_brief(self, run_id: str, error: str) -> None:
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event:
                event.status = "analysis_ready"
                event.progress = "简报生成失败，可直接重试"
                event.error_message = error
                event.updated_at = datetime.now(timezone.utc)

    def complete_additional_search(self, run_id: str) -> None:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event:
                event.status = "completed"
                event.progress = "补充搜索完成，候选结果已累计去重"
                event.error_message = None
                event.updated_at = now

    def update_progress(self, run_id: str, message: str) -> None:
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event:
                event.progress = message
                event.updated_at = datetime.now(timezone.utc)

    def save_candidates(self, run_id: str, candidates: list[dict]) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        new_count = 0
        duplicate_count = 0
        with self._sessions.begin() as session:
            for item in candidates:
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                normalized = canonical_url(url)
                document = session.scalar(
                    select(Document).where(Document.canonical_url == normalized)
                )
                if document is None:
                    raw_content = _clean_text(item.get("raw_content"))
                    document = Document(
                        id=new_id(), canonical_url=normalized, url=url,
                        title=item.get("title", ""),
                        publisher=item.get("source", ""),
                        source_type=item.get("source_group", "news_media"),
                        published_at=_datetime(item.get("published_at")),
                        fetched_at=_datetime(item.get("fetched_at")),
                        content_type=item.get("content_type"),
                        raw_content=raw_content,
                        content_hash=_hash(raw_content) if isinstance(raw_content, str) else None,
                        fetch_status=item.get("fetch_status", "pending"),
                        metadata_json=dict(item.get("document_metadata") or {}),
                    )
                    session.add(document)
                    session.flush()
                else:
                    document.title = item.get("title") or document.title
                    document.publisher = item.get("source") or document.publisher
                    document.source_type = item.get("source_group") or document.source_type
                    if item.get("published_at") and not document.published_at:
                        document.published_at = _datetime(item["published_at"])
                    if isinstance(item.get("raw_content"), str):
                        document.raw_content = _clean_text(item["raw_content"])
                        document.content_hash = _hash(document.raw_content or "")
                    if item.get("fetched_at"):
                        document.fetched_at = _datetime(item["fetched_at"])
                    if item.get("content_type"):
                        document.content_type = item["content_type"]
                    if item.get("fetch_status"):
                        document.fetch_status = item["fetch_status"]
                    document.metadata_json = {
                        **dict(document.metadata_json or {}),
                        **dict(item.get("document_metadata") or {}),
                    }
                relation = session.scalar(select(EventDocument).where(
                    EventDocument.event_id == run_id,
                    EventDocument.document_id == document.id,
                ))
                discovery = {
                    "providers": [item.get("provider", "unknown")],
                    "queries": [item.get("query")] if item.get("query") else [],
                }
                for key in ("search_sort", "platform_rank"):
                    if item.get(key) is not None:
                        discovery[key] = item[key]
                if relation is None:
                    new_count += 1
                    session.add(EventDocument(
                        id=new_id(), event_id=run_id, document_id=document.id,
                        snippet=item.get("search_snippet", item.get("snippet", "")),
                        discovery={
                            **discovery,
                            "appearances": list(item.get("appearances") or []),
                        },
                        analysis_status=item.get("analysis_status", "pending"),
                        analysis_reason=item.get("analysis_reason"),
                        relevance_score=item.get("relevance_score"),
                        selected_for_report=bool(item.get("selected_for_report", False)),
                        metadata_json=dict(item.get("event_metadata") or {}),
                        created_at=now, updated_at=now,
                    ))
                else:
                    duplicate_count += 1
                    current = dict(relation.discovery or {})
                    appearances = []
                    seen_appearances: set[str] = set()
                    for appearance in [
                        *(current.get("appearances") or []),
                        *(item.get("appearances") or []),
                    ]:
                        if not isinstance(appearance, dict):
                            continue
                        key = json.dumps(appearance, ensure_ascii=False, sort_keys=True)
                        if key in seen_appearances:
                            continue
                        seen_appearances.add(key)
                        appearances.append(appearance)
                    relation.discovery = {
                        "providers": list(dict.fromkeys([
                            *(current.get("providers") or []),
                            *(discovery.get("providers") or []),
                        ])),
                        "queries": list(dict.fromkeys([
                            *(current.get("queries") or []),
                            *(discovery.get("queries") or []),
                        ])),
                        "appearances": appearances,
                        **{
                            key: discovery[key]
                            for key in ("search_sort", "platform_rank")
                            if key in discovery
                        },
                    }
                    incoming_snippet = item.get(
                        "search_snippet", item.get("snippet", "")
                    )
                    if len(incoming_snippet) > len(relation.snippet or ""):
                        relation.snippet = incoming_snippet
                    relation.metadata_json = {
                        **dict(relation.metadata_json or {}),
                        **dict(item.get("event_metadata") or {}),
                    }
                    if item.get("analysis_status"):
                        relation.analysis_status = item["analysis_status"]
                        relation.analysis_reason = item.get("analysis_reason")
                        relation.relevance_score = item.get("relevance_score")
                        relation.selected_for_report = bool(
                            item.get("selected_for_report", False)
                        )
                    relation.updated_at = now
            total_unique_count = session.scalar(
                select(func.count(EventDocument.id)).where(
                    EventDocument.event_id == run_id
                )
            ) or 0
        return {
            "new_count": new_count,
            "duplicate_count": duplicate_count,
            "total_unique_count": int(total_unique_count),
        }

    def update_candidate_analysis(
        self,
        candidate_id: str,
        status: str,
        reason: str | None = None,
        duplicate_of_id: str | None = None,
        relevance_score: float | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            relation = session.get(EventDocument, str(candidate_id))
            if relation:
                relation.analysis_status = status
                relation.analysis_reason = reason
                relation.duplicate_of_id = duplicate_of_id
                if relevance_score is not None:
                    relation.relevance_score = relevance_score
                relation.selected_for_report = status == "accepted"
                relation.updated_at = datetime.now(timezone.utc)

    def update_candidate_fetch(self, run_id: str, url: str, **values) -> None:
        allowed = {"content", "raw_content", "fetch_status", "fetch_error", "final_url", "content_type", "fetched_at"}
        values = {key: value for key, value in values.items() if key in allowed}
        if "content" in values and "raw_content" not in values:
            values["raw_content"] = values.pop("content")
        if "raw_content" in values:
            values["raw_content"] = _clean_text(values["raw_content"])
            values["content_hash"] = _hash(values["raw_content"] or "")
        if "fetched_at" in values:
            values["fetched_at"] = _datetime(values["fetched_at"])
        if not values:
            return
        with self._sessions.begin() as session:
            relation = session.scalar(select(EventDocument).where(
                EventDocument.event_id == run_id,
                EventDocument.document_id.in_(select(Document.id).where(
                    Document.canonical_url == canonical_url(url)
                )),
            ))
            if relation is None:
                return
            document = session.get(Document, relation.document_id)
            if document is None:
                return
            for key, value in values.items():
                setattr(document, key, value)
            document.updated_at = datetime.now(timezone.utc)

    def list_candidates(self, run_id: str) -> list[dict]:
        with self._sessions() as session:
            rows = session.execute(
                select(EventDocument, Document)
                .join(Document, Document.id == EventDocument.document_id)
                .where(EventDocument.event_id == run_id)
                .order_by(EventDocument.created_at)
            ).all()
        result = []
        for relation, document in rows:
            discovery = relation.discovery or {}
            providers = discovery.get("providers") or ["unknown"]
            queries = discovery.get("queries") or []
            result.append({
                "id": relation.id,
                "event_document_id": relation.id,
                "document_id": document.id,
                "run_id": relation.event_id,
                "title": document.title,
                "url": document.url,
                "canonical_url": document.canonical_url,
                "domain": (urlparse(document.url).hostname or "").lower().removeprefix("www."),
                "final_url": document.final_url,
                "search_snippet": relation.snippet,
                "source": document.publisher,
                "provider": providers[0],
                "providers": providers,
                "source_group": document.source_type,
                "query": queries[0] if queries else None,
                "published_at": document.published_at.isoformat() if document.published_at else None,
                "content": document.raw_content,
                "fetch_status": document.fetch_status,
                "fetch_error": document.fetch_error,
                "content_type": document.content_type,
                "fetched_at": document.fetched_at.isoformat() if document.fetched_at else None,
                "analysis_status": relation.analysis_status,
                "analysis_reason": relation.analysis_reason,
                "relevance_score": relation.relevance_score,
                "selected_for_report": relation.selected_for_report,
                "duplicate_of_id": relation.duplicate_of_id,
                "discovery": discovery,
                "document_metadata": dict(document.metadata_json or {}),
                "event_metadata": dict(relation.metadata_json or {}),
            })
        return result

    def list_case_candidates(self, case_ref: str) -> list[dict]:
        """Return accepted, fetched documents from every child run in a case."""
        case = self.get_case(case_ref)
        if case is None:
            return []
        merged: dict[str, dict] = {}
        for child in case.child_runs:
            for row in self.list_candidates(child.run_id):
                if row.get("analysis_status") != "accepted":
                    continue
                if not str(row.get("content") or "").strip():
                    continue
                key = str(row.get("canonical_url") or row.get("url") or row.get("document_id"))
                current = merged.get(key)
                if current is None or len(str(row.get("content") or "")) > len(str(current.get("content") or "")):
                    merged[key] = {
                        **row,
                        "case_id": case.case_id,
                        "case_key": case.case_key,
                        "case_run_ids": [child.run_id],
                    }
                elif child.run_id not in merged[key].get("case_run_ids", []):
                    merged[key]["case_run_ids"].append(child.run_id)
        return sorted(
            merged.values(),
            key=lambda item: str(item.get("published_at") or ""),
            reverse=True,
        )

    def save_source_results(self, run_id: str, source_results: list[dict]) -> None:
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event:
                event.source_results = source_results
                event.updated_at = datetime.now(timezone.utc)

    def save_retrieval_reflection(self, run_id: str, trace: dict) -> None:
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event:
                event.retrieval_reflection = trace or {}
                event.updated_at = datetime.now(timezone.utc)

    def complete(
        self,
        run_id: str,
        report: str,
        *,
        report_data: dict | None = None,
        source_results: list[dict] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event:
                event.status = "completed"
                event.progress = "研究完成"
                event.report_markdown = report
                if report_data is not None:
                    event.report_data = report_data
                event.error_message = None
                event.completed_at = now
                event.updated_at = now
                if source_results is not None:
                    event.source_results = source_results

    def fail(self, run_id: str, error: str, *, source_results: list[dict] | None = None) -> None:
        now = datetime.now(timezone.utc)
        error = _clean_text(error) or "研究失败"
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event:
                event.status = "failed"
                event.progress = "研究失败"
                event.error_message = error
                event.updated_at = now
                if source_results is not None:
                    event.source_results = source_results

    def cancel(self, run_id: str) -> bool:
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None or event.status != "running":
                return False
            event.status = "canceled"
            event.progress = "任务已终止"
            event.error_message = "用户终止任务"
            event.updated_at = datetime.now(timezone.utc)
            return True

    def delete_failed(self, run_id: str) -> bool:
        """Delete only a failed case record; shared documents remain available."""
        with self._sessions.begin() as session:
            event = self._get_event(session, run_id)
            if event is None or event.status != "failed":
                return False
            session.delete(event)
            return True

    def mark_interrupted_runs(self) -> int:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            result = session.execute(
                update(Event)
                .where(Event.status == "running")
                .values(
                    status="failed",
                    progress="服务重启，原后台任务已中断",
                    error_message="API服务重启导致后台任务中断，请重新创建任务",
                    updated_at=now,
                )
            )
            return int(result.rowcount or 0)
