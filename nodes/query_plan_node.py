"""将自然语言主题拆成少量官方站内检索词。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_QUERY_PLAN
from ..utils.text_processing import extract_json


class QueryPlanNode(BaseNode):
    def run(self, input_data: dict) -> dict:
        query = str(input_data.get("query", "")).strip()
        if not query:
            raise ValueError("检索主题不能为空")
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_QUERY_PLAN,
            json.dumps({"query": query}, ensure_ascii=False),
        )
        parsed = extract_json(response)
        if not isinstance(parsed, dict):
            raise ValueError("LLM 未返回有效检索计划")

        topic = parsed.get("topic")
        raw_queries = parsed.get("search_queries")
        if not isinstance(topic, str) or not topic.strip():
            topic = query
        if not isinstance(raw_queries, list):
            raise ValueError("LLM 未返回 search_queries 列表")

        search_queries = []
        for item in raw_queries:
            if not isinstance(item, str) or not item.strip():
                continue
            normalized = " ".join(item.split())
            if normalized not in search_queries:
                search_queries.append(normalized)
            if len(search_queries) == 3:
                break
        if not search_queries:
            raise ValueError("LLM 未生成可用检索词")
        return {"topic": topic.strip(), "search_queries": search_queries}
