"""基于媒体报道和社交平台材料生成可追溯简报。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_MULTI_FACT_BRIEF
from ..utils.text_processing import clean_markdown_tags


class BriefNode(BaseNode):
    def run(self, input_data: dict) -> str:
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
        payload = dict(input_data)
        # 只有历史调用显式提供时才保留该字段；新主流程完全从媒体内容提取官方信息。
        if documents:
            payload["official_documents"] = documents
        else:
            payload.pop("official_documents", None)
        payload["media_insights"] = media_insights
        payload["social_insights"] = social_insights

        response = self.llm_client.invoke(
            SYSTEM_PROMPT_MULTI_FACT_BRIEF,
            json.dumps(payload, ensure_ascii=False),
        )
        brief = clean_markdown_tags(response)
        if not brief:
            raise ValueError("LLM 未返回简报")
        lines = brief.splitlines()
        first_content_index = next(
            (index for index, line in enumerate(lines) if line.strip()),
            None,
        )
        title = f"# {query}"
        if first_content_index is not None and lines[first_content_index].startswith("# "):
            lines[first_content_index] = title
        else:
            lines = [title, "", *lines]
        brief = "\n".join(lines)
        missing_media = [
            insight for insight in media_insights + social_insights
            if insight.get("url") and insight["url"] not in brief
        ]
        if missing_media:
            lines = [brief.rstrip(), "", "## 补充媒体与社交平台来源", ""]
            for insight in missing_media:
                label = insight.get("title") or insight.get("source_name") or "媒体报道"
                lines.append(f"- [{label}]({insight['url']})")
            brief = "\n".join(lines)
        return brief
