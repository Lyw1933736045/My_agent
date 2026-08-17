"""跨来源综合信息并生成结构化报告与固定 Markdown。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_MULTI_FACT_BRIEF
from ..tools.brief_models import (
    BriefResult,
    normalize_brief_data,
    render_brief_markdown,
)
from ..utils.text_processing import extract_json


class BriefNode(BaseNode):
    def run(self, input_data: dict) -> str:
        """Compatibility entry point returning Markdown."""
        return self.generate(input_data).markdown

    def generate(self, input_data: dict) -> BriefResult:
        # 保留旧字段仅为兼容历史调用；主流程不再传入独立官方材料。
        documents = input_data.get("official_documents", input_data.get("documents", []))
        media_insights = input_data.get("media_insights", [])
        social_insights = input_data.get("social_insights", [])
        if not all(isinstance(items, list) for items in (documents, media_insights, social_insights)):
            raise ValueError("BriefNode 输入列表格式无效")
        if not documents and not media_insights and not social_insights:
            raise ValueError("BriefNode 至少需要媒体或社交平台材料")
        for document in documents:
            if not isinstance(document, dict) or "event_fact" not in document:
                raise ValueError("BriefNode 输入缺少 event_fact")
            if "content" in document or "search_summary" in document:
                raise ValueError("BriefNode 不接收网页正文或搜索摘要")
        for insight in media_insights + social_insights:
            if not isinstance(insight, dict) or not insight.get("url"):
                raise ValueError("BriefNode 输入缺少媒体 URL")
            if "content" in insight or "search_summary" in insight:
                raise ValueError("BriefNode 不接收网页正文或搜索摘要")

        query = str(input_data.get("query") or input_data.get("topic") or "").strip()
        query = " ".join(query.splitlines())
        if not query:
            raise ValueError("BriefNode 缺少报告标题")
        sources, source_by_url = self._source_catalog(documents, media_insights, social_insights)
        payload = dict(input_data)
        # 只有历史调用显式提供时才保留该字段；新主流程完全从媒体内容提取官方信息。
        if documents:
            payload["official_documents"] = self._with_source_ids(documents, source_by_url)
        else:
            payload.pop("official_documents", None)
        payload["media_insights"] = self._with_source_ids(media_insights, source_by_url)
        payload["social_insights"] = self._with_source_ids(social_insights, source_by_url)
        payload["source_catalog"] = sources

        response = self.llm_client.invoke(
            SYSTEM_PROMPT_MULTI_FACT_BRIEF,
            json.dumps(payload, ensure_ascii=False),
        )
        parsed = extract_json(response)
        if not isinstance(parsed, dict):
            raise ValueError("LLM 未返回 brief_data JSON 对象")
        data = normalize_brief_data(parsed, title=query, sources=sources)
        markdown = render_brief_markdown(data)
        return BriefResult(data=data.model_dump(mode="json"), markdown=markdown)

    @staticmethod
    def _source_catalog(
        documents: list[dict],
        media_insights: list[dict],
        social_insights: list[dict],
    ) -> tuple[list[dict], dict[str, str]]:
        sources: list[dict] = []
        source_by_url: dict[str, str] = {}
        entries = [
            *((item, "official") for item in documents),
            *((item, "social" if item.get("source_group") == "social_media" else
               "official" if item.get("source_group") == "official_media" else "media")
              for item in [*media_insights, *social_insights]),
        ]
        for item, source_type in entries:
            url = str(item.get("url") or item.get("official_url") or "").strip()
            if not url or url in source_by_url:
                continue
            source_id = f"S{len(sources) + 1:02d}"
            event_fact = item.get("event_fact") if isinstance(item.get("event_fact"), dict) else {}
            sources.append({
                "id": source_id,
                "title": str(item.get("title") or event_fact.get("title") or "未命名来源"),
                "source_name": str(item.get("source_name") or event_fact.get("publisher") or ""),
                "url": url,
                "published_at": item.get("published_at") or event_fact.get("published_at"),
                "source_type": source_type,
            })
            source_by_url[url] = source_id
        return sources, source_by_url

    @staticmethod
    def _with_source_ids(items: list[dict], source_by_url: dict[str, str]) -> list[dict]:
        result = []
        for item in items:
            copied = dict(item)
            url = str(item.get("url") or item.get("official_url") or "")
            copied["source_id"] = source_by_url.get(url)
            result.append(copied)
        return result
