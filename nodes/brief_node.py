"""基于官方事实和媒体观点生成可追溯简报。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_MULTI_FACT_BRIEF
from ..utils.text_processing import clean_markdown_tags


class BriefNode(BaseNode):
    def run(self, input_data: dict) -> str:
        # documents 是旧版字段，继续作为 official_documents 的兼容入口。
        documents = input_data.get("official_documents", input_data.get("documents", []))
        media_insights = input_data.get("media_insights", [])
        social_insights = input_data.get("social_insights", [])
        if not all(isinstance(items, list) for items in (documents, media_insights, social_insights)):
            raise ValueError("BriefNode 输入列表格式无效")
        if not documents and not media_insights and not social_insights:
            raise ValueError("BriefNode 至少需要官方事实或媒体观点")
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
        payload["official_documents"] = documents
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
        missing_sources = [
            document
            for document in documents
            if document.get("official_url")
            and document["official_url"] not in brief
        ]
        if missing_sources:
            lines = [brief.rstrip(), "", "## 补充官方来源", ""]
            for document in missing_sources:
                fact = document.get("event_fact") or {}
                title = fact.get("title") or f"候选文件 {document.get('document_id', '')}"
                lines.append(f"- [{title}]({document['official_url']})")
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
