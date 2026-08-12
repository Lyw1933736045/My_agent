"""将自然语言主题拆成少量官方站内检索词。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_QUERY_PLAN
from ..utils.text_processing import extract_json


class QueryPlanNode(BaseNode):
    """把用户的自然语言问题转换成可执行的检索计划。

    这是编排流程的起点：此节点只负责规划 Query，不负责直接搜索媒体。
    """

    def run(self, input_data: dict) -> dict:
        query = str(input_data.get("query", "")).strip()
        if not query:
            raise ValueError("检索主题不能为空")
        # 第一次调用 LLM：要求模型输出 topic 和 media_queries 等结构化内容。
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_QUERY_PLAN,
            json.dumps({"query": query}, ensure_ascii=False),
        )
        try:
            parsed = extract_json(response)
        except ValueError as exc:
            # 如果模型返回的不是合法 JSON，只额外修复一次格式，不重新设计 Query。
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

        # 控制首轮 Query 数量，避免一次任务产生过多搜索请求。
        media_queries = normalize(raw_media_queries, 3)
        if not media_queries:
            raise ValueError("LLM 未生成可用检索词")
        raw_provider_queries = parsed.get("provider_queries")
        tavily_queries: list[str] = []
        weibo_query = ""
        if isinstance(raw_provider_queries, dict):
            raw_tavily_queries = raw_provider_queries.get("tavily")
            if isinstance(raw_tavily_queries, list):
                tavily_queries = normalize(raw_tavily_queries, 2)
            elif isinstance(raw_tavily_queries, str) and raw_tavily_queries.strip():
                tavily_queries = [" ".join(raw_tavily_queries.split())]
            raw_weibo_query = raw_provider_queries.get("weibo")
            if isinstance(raw_weibo_query, str):
                weibo_query = " ".join(raw_weibo_query.split())
        # 兼容旧模型输出；首轮 Tavily 仍然必须有独立 query。
        if not tavily_queries:
            tavily_queries = media_queries[:2]
        # 兼容旧模型输出；不额外调用 LLM，也不阻断原有检索计划。
        if not weibo_query:
            weibo_query = media_queries[0]
        # 对外只返回流程需要的三个字段，供 RunState 保存。
        return {
            "topic": topic.strip(),
            "media_queries": media_queries,
            "provider_queries": {"tavily": tavily_queries, "weibo": weibo_query},
        }
