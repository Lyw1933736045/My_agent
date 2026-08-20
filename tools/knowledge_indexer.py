"""Chunk, hash, embed, and upsert case knowledge into pgvector."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger

from ..run_repository import RunRepository
from ..utils.config import Settings
from ..utils.dedup import canonical_url
from .embedding_service import EmbeddingService
from .text_chunking import split_text


CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
STRUCTURED_SECTIONS = (
    "official",
    "media",
    "public_opinion",
    "timeline",
    "key_metrics",
    "synthesis",
)
BRIEF_SOURCE_TYPES = (
    "raw_document",
    "media_insight",
    "social_insight",
    "structured_analysis",
)
SIMULATION_SOURCE_TYPE = "simulation"
SIMULATION_REPORT_SOURCE_TYPE = "simulation_report"
SIMULATION_PG_TYPES = (SIMULATION_SOURCE_TYPE, SIMULATION_REPORT_SOURCE_TYPE)
SIMULATION_ACTION_LABELS = {
    "CREATE_POST": "发帖",
    "CREATE_COMMENT": "评论",
    "QUOTE_POST": "引用",
    "REPOST": "转发",
}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class KnowledgeIndexer:
    def __init__(self, repository: RunRepository, embedding: EmbeddingService) -> None:
        self.repository = repository
        self.embedding = embedding

    def index_case(self, case_id: str) -> dict[str, int]:
        case = self.repository.get_case(case_id)
        if case is None:
            raise ValueError("研究案例不存在")
        desired = [
            *self._raw_records(case),
            *self._insight_records(case),
            *self._structured_records(case),
        ]
        existing = {
            (row["source_type"], row["source_id"], row["chunk_index"]): row
            for row in self.repository.list_knowledge_chunks(case.case_id)
        }
        pending = []
        ready = []
        for record in desired:
            key = (record["source_type"], record["source_id"], record["chunk_index"])
            current = existing.get(key)
            if (
                current
                and current["content_hash"] == record["content_hash"]
                and current["embedding_model"] == self.embedding.model
                and current.get("embedding")
            ):
                record["embedding"] = current["embedding"]
                ready.append(record)
            else:
                pending.append(record)
        if pending:
            vectors = self.embedding.embed_documents([item["content"] for item in pending])
            for record, vector in zip(pending, vectors):
                record["embedding"] = vector
                record["embedding_model"] = self.embedding.model
                ready.append(record)
        for record in ready:
            record["embedding_model"] = self.embedding.model
        self.repository.replace_knowledge_chunks(
            case.case_id,
            ready,
            source_types=list(BRIEF_SOURCE_TYPES),
        )
        return {
            "total": len(ready),
            "embedded": len(pending),
            "reused": len(ready) - len(pending),
        }

    def index_simulation_run(
        self,
        case_id: str,
        *,
        simulation_id: str,
        memory_items: list[dict[str, Any]],
        personas: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        if self.repository.get_case(case_id) is None:
            raise ValueError("研究案例不存在")
        source_id = simulation_id[:128]
        records = self._simulation_records(
            case_id,
            simulation_id=source_id,
            memory_items=memory_items,
            personas=personas or [],
        )
        return self._upsert_typed_records(case_id, records, SIMULATION_SOURCE_TYPE)

    def index_simulation_report(
        self,
        case_id: str,
        *,
        simulation_id: str,
        markdown: str = "",
        report: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        if self.repository.get_case(case_id) is None:
            raise ValueError("研究案例不存在")
        source_id = simulation_id[:128]
        records = self._simulation_report_records(
            case_id,
            simulation_id=source_id,
            markdown=markdown,
            report=report or {},
        )
        return self._upsert_typed_records(case_id, records, SIMULATION_REPORT_SOURCE_TYPE)

    def _upsert_typed_records(
        self,
        case_id: str,
        records: list[dict],
        source_type: str,
    ) -> dict[str, int]:
        existing = {
            (row["source_type"], row["source_id"], row["chunk_index"]): row
            for row in self.repository.list_knowledge_chunks(case_id)
            if row.get("source_type") == source_type
        }
        pending = []
        ready = []
        for record in records:
            key = (record["source_type"], record["source_id"], record["chunk_index"])
            current = existing.get(key)
            if (
                current
                and current["content_hash"] == record["content_hash"]
                and current["embedding_model"] == self.embedding.model
                and current.get("embedding")
            ):
                record["embedding"] = current["embedding"]
                ready.append(record)
            else:
                pending.append(record)
        if pending:
            vectors = self.embedding.embed_documents([item["content"] for item in pending])
            for record, vector in zip(pending, vectors):
                record["embedding"] = vector
                record["embedding_model"] = self.embedding.model
                ready.append(record)
        for record in ready:
            record["embedding_model"] = self.embedding.model
        self.repository.replace_knowledge_chunks(
            case_id,
            ready,
            source_types=[source_type],
        )
        return {
            "total": len(ready),
            "embedded": len(pending),
            "reused": len(ready) - len(pending),
        }

    def _simulation_records(
        self,
        case_id: str,
        *,
        simulation_id: str,
        memory_items: list[dict[str, Any]],
        personas: list[dict[str, Any]],
    ) -> list[dict]:
        approved = [item for item in personas if item.get("status") == "approved"] or list(personas)
        names = {
            index: str(item.get("display_name") or f"智能体 {index}")
            for index, item in enumerate(approved)
        }
        records = []
        for item in memory_items:
            content = self._simulation_chunk_text(item, names)
            if not content:
                continue
            agent_id = item.get("agent_id")
            actor = names.get(int(agent_id), f"智能体 {agent_id}") if str(agent_id).isdigit() else "模拟主体"
            records.append(self._record(
                case_id,
                source_type=SIMULATION_SOURCE_TYPE,
                source_id=simulation_id[:128],
                title=f"【模拟】{actor} · 第{item.get('round', 0)}轮",
                url="",
                published_at=None,
                content=content,
                chunk_index=len(records),
                document_id=None,
            ))
        return records

    @staticmethod
    def _simulation_chunk_text(item: dict[str, Any], names: dict[int, str]) -> str:
        body = str(item.get("content") or "").strip()
        if not body:
            return ""
        agent_id = item.get("agent_id")
        actor = names.get(int(agent_id), f"智能体 {agent_id}") if str(agent_id).isdigit() else "模拟主体"
        action = SIMULATION_ACTION_LABELS.get(str(item.get("action_type") or ""), "互动")
        refs = "、".join(str(value) for value in item.get("basis_real_refs") or []) or "无"
        return "\n".join([
            "【模拟推演，非真实信息】",
            f"轮次：{item.get('round', 0)}",
            f"主体：{actor}",
            f"行为：{action}",
            f"证据引用：{refs}",
            f"内容：{body}",
        ])

    def _simulation_report_records(
        self,
        case_id: str,
        *,
        simulation_id: str,
        markdown: str,
        report: dict[str, Any],
    ) -> list[dict]:
        text = str(markdown or "").strip() or self._simulation_report_fallback(report)
        if not text:
            return []
        if "【模拟分析报告，非真实信息】" not in text:
            text = "【模拟分析报告，非真实信息】\n" + text
        records = []
        chunks = self._chunks_for(text) or [text]
        for chunk_index, chunk in enumerate(chunks):
            records.append(self._record(
                case_id,
                source_type=SIMULATION_REPORT_SOURCE_TYPE,
                source_id=simulation_id[:128],
                title=f"【模拟报告】{simulation_id}",
                url="",
                published_at=None,
                content=chunk,
                chunk_index=chunk_index,
                document_id=None,
            ))
        return records

    @staticmethod
    def _simulation_report_fallback(report: dict[str, Any]) -> str:
        if not report:
            return ""
        summary = report.get("llm_summary") if isinstance(report.get("llm_summary"), dict) else {}
        lines = [
            "【模拟分析报告，非真实信息】",
            f"推演轮次：{report.get('round_count')}",
            f"行为总数：{report.get('action_count')}",
            f"智能体：{report.get('agent_count')}",
            str(summary.get("executive_summary") or report.get("disclaimer") or "").strip(),
        ]
        for item in summary.get("consensus") or []:
            lines.append(f"共识：{item}")
        for item in summary.get("disagreements") or []:
            lines.append(f"分歧：{item}")
        return "\n".join(line for line in lines if line).strip()

    def _source_catalog(self, report_data: dict) -> dict[str, dict]:
        result = {}
        for item in report_data.get("sources", []) if isinstance(report_data, dict) else []:
            if not isinstance(item, dict) or not item.get("id") or not item.get("url"):
                continue
            source = {
                "source_id": str(item["id"]),
                "title": str(item.get("title") or "未命名来源"),
                "url": str(item.get("url") or ""),
            }
            result[canonical_url(source["url"]) or source["source_id"]] = source
        return result

    def _source_for(self, item: dict, catalog: dict[str, dict], fallback_id: str) -> dict:
        url = str(item.get("url") or "").strip()
        key = canonical_url(url) if url else ""
        if key and key in catalog:
            return catalog[key]
        return {
            "source_id": str(item.get("source_id") or fallback_id),
            "title": str(item.get("title") or "未命名来源"),
            "url": url,
        }

    def _chunks_for(self, text: str) -> list[str]:
        return split_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)

    def _raw_records(self, case) -> list[dict]:
        catalog = self._source_catalog(case.report_data)
        records = []
        for index, row in enumerate(self.repository.list_case_candidates(case.case_id), 1):
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            source = self._source_for(row, catalog, f"D{index:02d}")
            for chunk_index, chunk in enumerate(self._chunks_for(content)):
                records.append(self._record(
                    case.case_id,
                    source_type="raw_document",
                    source_id=source["source_id"],
                    title=str(row.get("title") or source["title"]),
                    url=str(row.get("url") or source["url"]),
                    published_at=row.get("published_at"),
                    content=chunk,
                    chunk_index=chunk_index,
                    document_id=row.get("document_id"),
                ))
        return records

    def _insight_records(self, case) -> list[dict]:
        catalog = self._source_catalog(case.report_data)
        prepared = self.repository.aggregate_case_prepared_analysis(case.case_id) or {}
        records = []
        groups = (
            ("media_insight", prepared.get("media_insights") or []),
            ("social_insight", prepared.get("social_insights") or []),
        )
        for source_type, items in groups:
            for index, item in enumerate(items, 1):
                if not isinstance(item, dict):
                    continue
                source = self._source_for(item, catalog, f"I{index:02d}")
                text = _json_text({
                    "title": item.get("title"),
                    "reported_facts": item.get("reported_facts"),
                    "interpretations": item.get("interpretations"),
                    "named_views": item.get("named_views"),
                    "affected_parties": item.get("affected_parties"),
                    "risks_or_disagreements": item.get("risks_or_disagreements"),
                    "statistics": item.get("statistics"),
                    "timeline_events": (item.get("metadata") or {}).get("timeline_events"),
                })
                for chunk_index, chunk in enumerate(self._chunks_for(text) or [text]):
                    records.append(self._record(
                        case.case_id,
                        source_type=source_type,
                        source_id=source["source_id"],
                        title=str(item.get("title") or source["title"]),
                        url=str(item.get("url") or source["url"]),
                        published_at=item.get("published_at"),
                        content=chunk,
                        chunk_index=chunk_index,
                        document_id=None,
                    ))
        return records

    def _structured_records(self, case) -> list[dict]:
        data = case.report_data if isinstance(case.report_data, dict) else {}
        records = []
        for section in STRUCTURED_SECTIONS:
            value = data.get(section)
            if value in (None, {}, [], ""):
                continue
            text = _json_text({section: value})
            source_id = f"SA-{section}"
            for chunk_index, chunk in enumerate(self._chunks_for(text) or [text]):
                records.append(self._record(
                    case.case_id,
                    source_type="structured_analysis",
                    source_id=source_id,
                    title=str(data.get("title") or case.topic or section),
                    url="",
                    published_at=data.get("generated_at"),
                    content=chunk,
                    chunk_index=chunk_index,
                    document_id=None,
                ))
        return records

    def _record(
        self,
        case_id: str,
        *,
        source_type: str,
        source_id: str,
        title: str,
        url: str,
        published_at: Any,
        content: str,
        chunk_index: int,
        document_id: str | None,
    ) -> dict:
        content = str(content or "").strip()
        published = published_at if isinstance(published_at, datetime) else _parse_datetime(
            str(published_at) if published_at else None
        )
        return {
            "case_id": case_id,
            "document_id": document_id,
            "source_id": source_id,
            "source_type": source_type,
            "title": title,
            "url": url or "",
            "published_at": published,
            "content": content,
            "chunk_index": chunk_index,
            "content_hash": _hash(content),
            "embedding_model": self.embedding.model,
        }


def index_case_safely(repository: RunRepository, case_id: str, settings: Settings | None = None) -> None:
    if not case_id:
        return
    try:
        KnowledgeIndexer(repository, EmbeddingService.from_settings(settings)).index_case(case_id)
    except Exception as exc:
        logger.warning(f"知识库索引失败 case_id={case_id}: {exc}")


def index_simulation_safely(
    repository: RunRepository,
    case_id: str,
    run_dir: Path,
    simulation_id: str,
    settings: Settings | None = None,
) -> None:
    if not case_id or not simulation_id:
        return
    try:
        indexer = KnowledgeIndexer(repository, EmbeddingService.from_settings(settings))
        memory_path = run_dir / "memory" / "simulation_memory.json"
        if memory_path.is_file():
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
            personas_payload = {}
            personas_path = run_dir / "personas.json"
            if personas_path.is_file():
                personas_payload = json.loads(personas_path.read_text(encoding="utf-8"))
            indexer.index_simulation_run(
                case_id,
                simulation_id=simulation_id,
                memory_items=list(payload.get("items") or []),
                personas=list(personas_payload.get("personas") or []),
            )
        markdown_path = run_dir / "results" / "simulation_report.md"
        json_path = run_dir / "results" / "simulation_report.json"
        markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""
        report = {}
        if json_path.is_file():
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                report = loaded
        if markdown.strip() or report:
            indexer.index_simulation_report(
                case_id,
                simulation_id=simulation_id,
                markdown=markdown,
                report=report,
            )
    except Exception as exc:
        logger.warning(
            f"模拟记忆索引失败 case_id={case_id} simulation_id={simulation_id}: {exc}"
        )
