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
    """一次研究运行的共享状态。

    流程中的每个步骤都从 RunState 读取输入，并把自己的结果写回其中。
    它相当于流程中的“工作台”或“文件夹”，记录任务当前走到哪一步。
    """

    query: str
    # 用户最初的问题，以及 LLM 规划出的主题和检索词。
    topic: str = ""
    media_queries: list[str] = field(default_factory=list)
    provider_queries: dict[str, str | list[str]] = field(default_factory=dict)
    # 媒体检索结果：候选文章、数据源状态、Reflection 记录等。
    discovery: DiscoveryResult | None = None
    # 后续阶段依次填充：正文、相关性判断、观点和最终报告。
    selected_documents: list[MediaDocument] = field(default_factory=list)
    content_decisions: list[RelevanceDecision] = field(default_factory=list)
    insights: list[MediaInsight] = field(default_factory=list)
    brief: str = ""
    read_attempted_count: int = 0
    read_success_count: int = 0
    weibo_raw: list[dict] = field(default_factory=list)
    retrieval_reflection: dict[str, dict] = field(default_factory=dict)

    @property
    def relevant_documents_count(self) -> int:
        return sum(item.relevant for item in self.content_decisions)
