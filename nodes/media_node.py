"""从少量媒体正文中提炼报道事实与媒体观点。"""

import json
from typing import Any, Optional

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_MEDIA_ANALYSIS
from ..tools.media_models import MediaDocument, MediaInsight
from ..utils.text_processing import extract_json


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class MediaNode(BaseNode):
    def run(self, input_data: list[MediaDocument]) -> list[MediaInsight]:
        if not isinstance(input_data, list) or not input_data:
            raise ValueError("MediaNode 至少需要一篇媒体正文")
        payload = []
        for document in input_data:
            if not isinstance(document, MediaDocument) or not document.content.strip():
                raise ValueError("MediaNode 需要包含真实正文的 MediaDocument")
            item = {
                "title": document.candidate.title,
                "source_name": document.candidate.source_name,
                "url": document.final_url,
                "published_at": document.candidate.published_at,
                "source_group": document.candidate.source_group,
                "content": document.content,
            }
            if document.candidate.source_group == "social_media":
                metadata = document.candidate.metadata
                item["social_metrics"] = {
                    key: metadata.get(key)
                    for key in (
                        "likes_count", "comments_count", "reposts_count",
                        "platform_rank", "search_sort",
                    )
                    if key in metadata
                }
                item["comments"] = metadata.get("comments", [])
            payload.append(item)
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_MEDIA_ANALYSIS,
            json.dumps({"documents": payload}, ensure_ascii=False),
        )
        parsed = extract_json(response)
        if not isinstance(parsed, list):
            raise ValueError("LLM 未返回有效的媒体观点列表")

        insights = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            title = _text(item.get("title"))
            source_name = _text(item.get("source_name"))
            url = _text(item.get("url"))
            if not title or not source_name or not url:
                continue
            matching_document = next(
                (document for document in input_data if document.final_url == url),
                None,
            )
            source_metadata = {}
            if matching_document is not None and matching_document.candidate.source_group == "social_media":
                candidate_metadata = matching_document.candidate.metadata
                source_metadata = {
                    key: candidate_metadata[key]
                    for key in (
                        "wid", "mblogid", "user_id", "user_name",
                        "likes_count", "comments_count", "reposts_count",
                        "platform_rank", "search_sort", "comments_fetch", "comments",
                    )
                    if key in candidate_metadata
                }
            insights.append(
                MediaInsight(
                    title=title,
                    source_name=source_name,
                    url=url,
                    published_at=_text(item.get("published_at")),
                    source_group=document_group(item, input_data),
                    reported_facts=_text_list(item.get("reported_facts")),
                    interpretations=_text_list(item.get("interpretations")),
                    affected_parties=_text_list(item.get("affected_parties")),
                    risks_or_disagreements=_text_list(item.get("risks_or_disagreements")),
                    metadata=source_metadata,
                )
            )
        if not insights:
            raise ValueError("LLM 未提取到可用媒体观点")
        return insights


def document_group(item: dict, documents: list[MediaDocument]) -> str:
    """LLM 不得改变来源分组，以最终 URL 对应的程序标记为准。"""
    url = _text(item.get("url"))
    for document in documents:
        if url == document.final_url:
            return document.candidate.source_group
    return "news_media"
