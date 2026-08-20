"""Small, serialization-friendly models used by Gate 1 and Gate 2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any


def jsonable(value: Any) -> Any:
    """Convert repository dataclasses and datetimes into JSON-safe values."""
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class ScenarioConfig:
    as_of: datetime
    horizon_hours: int
    question: str
    # Temporary case-level exception: undated social posts may enter as
    # claims, but only when their trace is explicitly social/Weibo. Facts,
    # timelines, and non-social sources remain date-gated.
    allow_undated_social: bool = False

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("scenario.as_of must include a timezone")
        if self.horizon_hours <= 0:
            raise ValueError("scenario.horizon_hours must be positive")
        if not self.question.strip():
            raise ValueError("scenario.question must not be empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "horizon_hours": self.horizon_hours,
            "question": self.question.strip(),
            "allow_undated_social": self.allow_undated_social,
        }


@dataclass
class CaseBundle:
    """In-memory Gate 1 result. Accepted document bodies never get serialized."""

    case: dict[str, Any]
    child_runs: list[dict[str, Any]]
    accepted_documents: list[dict[str, Any]]
    prepared_analysis: dict[str, Any]
    brief_data: dict[str, Any]
    source_catalog: list[dict[str, Any]]
    all_case_documents: list[dict[str, Any]] = field(default_factory=list)
    quality_warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def case_id(self) -> str:
        return str(self.case.get("case_key") or self.case.get("case_id") or "")

    def audit_dict(self) -> dict[str, Any]:
        """Return counts and metadata only; omit document full text by design."""
        source_groups: dict[str, int] = {}
        all_source_groups: dict[str, int] = {}
        for document in self.all_case_documents:
            group = str(document.get("source_group") or "unknown")
            all_source_groups[group] = all_source_groups.get(group, 0) + 1
        accepted_with_content = 0
        for document in self.accepted_documents:
            group = str(document.get("source_group") or "unknown")
            source_groups[group] = source_groups.get(group, 0) + 1
            accepted_with_content += bool(str(document.get("content") or "").strip())
        media = list(self.prepared_analysis.get("media_insights") or [])
        social = list(self.prepared_analysis.get("social_insights") or [])
        insight_counts = {
            "reported_facts": 0,
            "interpretations": 0,
            "named_views": 0,
            "risks_or_disagreements": 0,
        }
        for insight in [*media, *social]:
            for key in insight_counts:
                insight_counts[key] += len(insight.get(key) or [])
        return {
            "case": self.case,
            "counts": {
                "child_runs": len(self.child_runs),
                "related_documents": len(self.all_case_documents) or len(self.accepted_documents),
                "accepted_documents": len(self.accepted_documents),
                "accepted_with_content": accepted_with_content,
                "related_source_groups": all_source_groups or source_groups,
                "source_groups": source_groups,
                "media_insights": len(media),
                "social_insights": len(social),
                "prepared_items": insight_counts,
                "brief_sources": len(self.source_catalog),
                "brief_timeline": len(self.brief_data.get("timeline") or []),
                "brief_key_metrics": len(self.brief_data.get("key_metrics") or []),
            },
            "quality_warnings": self.quality_warnings,
        }
