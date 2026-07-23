"""单次媒体研究流程的运行状态。"""

from dataclasses import dataclass, field

from ..tools.media_models import (
    DiscoveryResult,
    MediaDocument,
    MediaInsight,
    RelevanceDecision,
)


@dataclass
class RunState:
    query: str
    topic: str = ""
    media_queries: list[str] = field(default_factory=list)
    discovery: DiscoveryResult | None = None
    selected_documents: list[MediaDocument] = field(default_factory=list)
    content_decisions: list[RelevanceDecision] = field(default_factory=list)
    insights: list[MediaInsight] = field(default_factory=list)
    brief: str = ""
    read_attempted_count: int = 0
    read_success_count: int = 0

    @property
    def relevant_documents_count(self) -> int:
        return sum(item.relevant for item in self.content_decisions)
