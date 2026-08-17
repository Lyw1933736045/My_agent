"""从少量媒体正文中提炼报道事实与媒体观点。"""

import json
from typing import Any, Optional

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_MEDIA_ANALYSIS
from ..tools.media_models import MediaDocument, MediaInsight
from ..tools.text_chunking import split_text
from ..utils.text_processing import extract_json


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _dict_list(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class MediaNode(BaseNode):
    """从已经筛选过的正文中提炼结构化媒体观点。

    输入是 MediaDocument 列表，输出是 MediaInsight 列表；
    它不负责搜索，也不负责生成最终报告。
    """

    def run(self, input_data: list[MediaDocument]) -> list[MediaInsight]:
        if not isinstance(input_data, list) or not input_data:
            raise ValueError("MediaNode 至少需要一篇媒体正文")
        insights = []
        for document in input_data:
            if not isinstance(document, MediaDocument) or not document.content.strip():
                raise ValueError("MediaNode 需要包含真实正文的 MediaDocument")
            items = []
            for content in self._analysis_chunks(document):
                response = self.llm_client.invoke(
                    SYSTEM_PROMPT_MEDIA_ANALYSIS,
                    json.dumps({"documents": [self._payload_item(document, content)]}, ensure_ascii=False),
                )
                parsed = extract_json(response)
                if not isinstance(parsed, list):
                    raise ValueError("LLM 未返回有效的媒体观点列表")
                items.extend(item for item in parsed if isinstance(item, dict))
            insight = self._build_insight(document, items)
            if insight is not None:
                insights.append(insight)
        if not insights:
            raise ValueError("LLM 未提取到可用媒体观点")
        return insights

    @staticmethod
    def _analysis_chunks(document: MediaDocument) -> list[str]:
        if document.candidate.source_group == "social_media":
            return [document.content]
        raw = (document.raw_content or document.content).strip()
        if len(raw) <= 12_000:
            return [raw]
        return split_text(raw, chunk_size=5_000, overlap=300)

    @staticmethod
    def _payload_item(document: MediaDocument, content: str) -> dict:
        item = {
            "title": document.candidate.title,
            "source_name": document.candidate.source_name,
            "url": document.final_url,
            "published_at": document.candidate.published_at,
            "source_group": document.candidate.source_group,
            "content": content,
        }
        if document.candidate.source_group == "social_media":
            metadata = document.candidate.metadata
            item["social_metrics"] = {
                key: metadata.get(key)
                for key in ("likes_count", "comments_count", "reposts_count", "platform_rank", "search_sort")
                if key in metadata
            }
            item["comments"] = metadata.get("comments", [])
        return item

    @classmethod
    def _build_insight(cls, document: MediaDocument, items: list[dict]) -> MediaInsight | None:
        valid = [item for item in items if _text(item.get("title")) and _text(item.get("source_name"))]
        if not valid:
            return None
        first = valid[0]

        def merge_list(key: str) -> list:
            merged, seen = [], set()
            for item in valid:
                values = item.get(key, []) if isinstance(item.get(key), list) else []
                for value in values:
                    marker = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else str(value)
                    if marker not in seen:
                        seen.add(marker)
                        merged.append(value)
            return merged

        source_metadata = {}
        if document.candidate.source_group == "social_media":
            source_metadata = {
                key: document.candidate.metadata[key]
                for key in (
                    "wid", "mblogid", "user_id", "user_name", "likes_count",
                    "comments_count", "reposts_count", "platform_rank", "search_sort",
                    "comments_fetch", "comments",
                ) if key in document.candidate.metadata
            }
        return MediaInsight(
            title=_text(first.get("title")) or document.candidate.title,
            source_name=_text(first.get("source_name")) or document.candidate.source_name,
            url=document.final_url,
            published_at=_text(first.get("published_at")) or document.candidate.published_at,
            source_group=document.candidate.source_group,
            reported_facts=_text_list(merge_list("reported_facts")),
            interpretations=_text_list(merge_list("interpretations")),
            affected_parties=_text_list(merge_list("affected_parties")),
            risks_or_disagreements=_text_list(merge_list("risks_or_disagreements")),
            statistics=_dict_list(merge_list("statistics")),
            named_views=_dict_list(merge_list("named_views")),
            metadata={**source_metadata, "timeline_events": merge_list("timeline_events")},
        )


def document_group(item: dict, documents: list[MediaDocument]) -> str:
    """LLM 不得改变来源分组，以最终 URL 对应的程序标记为准。"""
    url = _text(item.get("url"))
    for document in documents:
        if url == document.final_url:
            return document.candidate.source_group
    return "news_media"
