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
        try:
            parsed = extract_json(response)
        except ValueError as exc:
            # 沿用 TrendRadar：JSON 无法解析时，只请求模型修复一次格式。
            repaired_response = self.llm_client.invoke(
                "你是 JSON 修复助手。只修复 JSON 格式，保持原始语义，只返回纯 JSON。",
                json.dumps(
                    {"parse_error": str(exc), "original_response": response},
                    ensure_ascii=False,
                ),
            )
            parsed = extract_json(repaired_response)
            response = repaired_response
        # 部分兼容模型会把唯一对象包在数组或 data/result 字段中。
        if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
            parsed = parsed[0]
        if isinstance(parsed, dict):
            for wrapper in ("data", "result", "plan"):
                wrapped = parsed.get(wrapper)
                if isinstance(wrapped, dict):
                    parsed = wrapped
                    break
        if not isinstance(parsed, dict):
            preview = " ".join(response.split())[:300] or "<空响应>"
            raise ValueError(f"LLM 未返回有效检索计划；原始响应：{preview}")

        topic = parsed.get("topic")
        raw_media_queries = parsed.get(
            "media_queries", parsed.get("search_queries", parsed.get("official_queries"))
        )
        if not isinstance(topic, str) or not topic.strip():
            topic = query
        if not isinstance(raw_media_queries, list):
            preview = " ".join(response.split())[:300]
            raise ValueError(f"LLM 未返回 media_queries 列表；原始响应：{preview}")

        def normalize(raw_queries: list, limit: int) -> list[str]:
            queries = []
            for item in raw_queries:
                if not isinstance(item, str) or not item.strip():
                    continue
                value = " ".join(item.split())
                if value not in queries:
                    queries.append(value)
                if len(queries) == limit:
                    break
            return queries

        media_queries = normalize(raw_media_queries, 5)
        if not media_queries:
            raise ValueError("LLM 未生成可用检索词")
        return {
            "topic": topic.strip(),
            "media_queries": media_queries,
        }
