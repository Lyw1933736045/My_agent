"""媒体发现、正文读取和观点提炼使用的统一数据模型。"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class MediaCandidate:
    title: str
    url: str
    source_name: str
    published_at: Optional[str]
    snippet: str = ""
    discovered_by: tuple[str, ...] = ()
    source_group: str = "news_media"
    query: Optional[str] = None
    guid: Optional[str] = None
    max_age_days: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderDiagnostics:
    failed_sources: dict[str, str] = field(default_factory=dict)
    successful_sources: dict[str, str] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceFetchResult:
    provider: str
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class DiscoveryResult:
    candidates: list[MediaCandidate]
    stats: dict[str, int]
    errors: dict[str, str] = field(default_factory=dict)
    sources: tuple[SourceFetchResult, ...] = ()
    raw_candidates: list[MediaCandidate] = field(default_factory=list)
    provider_candidates: dict[str, list[MediaCandidate]] = field(default_factory=dict)
    retrieval_reflection: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class MediaDocument:
    candidate: MediaCandidate
    final_url: str
    fetched_at: str
    content_type: str
    content: str


@dataclass(frozen=True)
class RelevanceDecision:
    candidate: MediaCandidate
    stage: str
    relevant: bool
    score: int
    reason: str
    matched_terms: tuple[str, ...] = ()


@dataclass
class MediaInsight:
    title: str
    source_name: str
    url: str
    published_at: Optional[str] = None
    source_group: str = "news_media"
    reported_facts: list[str] = field(default_factory=list)
    interpretations: list[str] = field(default_factory=list)
    affected_parties: list[str] = field(default_factory=list)
    risks_or_disagreements: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
