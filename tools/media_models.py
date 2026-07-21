"""媒体发现、正文读取和观点提炼使用的统一数据模型。"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class MediaCandidate:
    title: str
    url: str
    source_name: str
    published_at: Optional[str]
    snippet: str = ""
    discovered_by: str = "newsnow"
    source_group: str = "news_media"


@dataclass(frozen=True)
class MediaDocument:
    candidate: MediaCandidate
    final_url: str
    fetched_at: str
    content_type: str
    content: str


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
