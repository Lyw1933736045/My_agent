"""将自然语言主题拆成 NewsNow/RSS 预筛词和 Tavily 搜索词。"""

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
        # 第一次调用 LLM：要求模型输出 topic、预筛词和 Tavily Query。
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_QUERY_PLAN,
            json.dumps({"query": query}, ensure_ascii=False),
        )
        # Query 规划严格只调用一次 LLM；格式错误直接报错，不再发起修复调用。
        parsed = extract_json(response)
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
        if not isinstance(topic, str) or not topic.strip():
            topic = query

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

        raw_newsnow_rss_core = parsed.get("newsnow_rss_core")
        raw_newsnow_rss_support = parsed.get("newsnow_rss_support")
        if not isinstance(raw_newsnow_rss_core, list) or not isinstance(
            raw_newsnow_rss_support, list
        ):
            preview = " ".join(response.split())[:300]
            raise ValueError(
                "LLM 未返回 newsnow_rss_core 和 newsnow_rss_support 列表；"
                f"原始响应：{preview}"
            )
        newsnow_rss_core = normalize(raw_newsnow_rss_core, 3)
        newsnow_rss_support = normalize(raw_newsnow_rss_support, 6)
        if not newsnow_rss_core:
            raise ValueError("LLM 未生成可用 newsnow_rss_core")
        tavily_queries = parsed.get("tavily_queries")
        if isinstance(tavily_queries, str) and tavily_queries.strip():
            tavily_queries = [tavily_queries]
        if not isinstance(tavily_queries, list):
            tavily_queries = []
        # 直接采用 LLM 的 concise_description；为空时回退到 topic。
        tavily_queries = normalize(tavily_queries, 1) or [topic.strip()]
        raw_weibo_query = parsed.get("weibo_query")
        weibo_query = (
            " ".join(raw_weibo_query.split())
            if isinstance(raw_weibo_query, str) and raw_weibo_query.strip()
            else ""
        )
        if not weibo_query:
            raise ValueError("LLM 未生成可用 weibo_query")
        # 返回查询与轻量预筛词，供 RunState 和 Stage 1 保存流程使用。
        return {
            "topic": topic.strip(),
            "newsnow_rss_core": newsnow_rss_core,
            "newsnow_rss_support": newsnow_rss_support,
            "tavily_queries": tavily_queries,
            "weibo_query": weibo_query,
        }
