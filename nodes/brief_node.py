"""基于多个 EventFact 生成可追溯简报。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_MULTI_FACT_BRIEF
from ..utils.text_processing import clean_markdown_tags


class BriefNode(BaseNode):
    def run(self, input_data: dict) -> str:
        documents = input_data.get("documents")
        if not isinstance(documents, list) or not documents:
            raise ValueError("BriefNode 至少需要一个 EventFact")
        for document in documents:
            if not isinstance(document, dict) or "event_fact" not in document:
                raise ValueError("BriefNode 输入缺少 event_fact")
            if "content" in document or "search_summary" in document:
                raise ValueError("BriefNode 不接收网页正文或搜索摘要")

        response = self.llm_client.invoke(
            SYSTEM_PROMPT_MULTI_FACT_BRIEF,
            json.dumps(input_data, ensure_ascii=False),
        )
        brief = clean_markdown_tags(response)
        if not brief:
            raise ValueError("LLM 未返回简报")
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
        return brief
