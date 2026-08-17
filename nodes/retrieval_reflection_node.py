"""首轮检索不足时的独立检查与单轮补充查询节点。"""

from __future__ import annotations

import json
from .base_node import BaseNode
from ..prompts import (
    SYSTEM_PROMPT_TAVILY_QUERY_EXPANSION,
    SYSTEM_PROMPT_WEIBO_QUERY_EXPANSION,
)
from ..tools.media_models import MediaCandidate
from ..utils.dedup import valid_provider_candidates
from ..utils.text_processing import extract_json


def _normalized_queries(value: object, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            continue
        query = " ".join(item.split())
        if query not in result:
            result.append(query)
        if len(result) >= limit:
            break
    return result


class RetrievalCheckNode:
    """检查首轮检索是否足够，不调用 LLM。

    它只做判断：统计有效候选数量，并决定是否触发补充检索。
    """

    def run(self, input_data: dict) -> dict[str, dict]:
        provider_candidates = input_data.get("provider_candidates") or {}
        tavily_queries = input_data.get("tavily_queries") or []
        weibo_queries = input_data.get("weibo_queries") or []
        thresholds = input_data.get("thresholds") or {}
        trace: dict[str, dict] = {}
        if "tavily" in provider_candidates:
            initial_queries = _normalized_queries(tavily_queries, 2)
            count = len(valid_provider_candidates("tavily", provider_candidates["tavily"]))
            # 候选数量低于阈值时，标记 adaptive_triggered=True。
            trace["tavily"] = {
                "initial_queries": initial_queries,
                "initial_valid_count": count,
                # Tavily Query 在首轮只改写一次，不再触发 LLM 补充改写。
                "adaptive_triggered": False,
                "adaptive_disabled_reason": "single_rewrite_only",
                "supplementary_queries": [],
                "retry_valid_count": 0,
                "final_valid_count": count,
            }
        if "weibo" in provider_candidates:
            initial = _normalized_queries(weibo_queries, 1)
            count = len(valid_provider_candidates("weibo", provider_candidates["weibo"]))
            trace["weibo"] = {
                "initial_query": initial[0] if initial else "",
                "initial_valid_count": count,
                "adaptive_triggered": count < int(thresholds.get("weibo", 2)),
                "refined_query": "",
                "retry_valid_count": 0,
                "final_valid_count": count,
            }
        return trace


class AdaptiveRetrievalNode(BaseNode):
    """根据首轮结果为不足的 Provider 生成一次补充 Query。

    这是 Reflection Loop 的“行动”部分：先看结果是否足够，
    不足时让 LLM 结合首轮结果生成新的检索词，再进行有限的一次补搜。
    """

    def run(self, input_data: dict) -> dict[str, list[str]]:
        topic = str(input_data.get("topic") or "").strip()
        provider_candidates = input_data.get("provider_candidates") or {}
        trace = input_data.get("trace") or {}
        if not topic:
            raise ValueError("AdaptiveRetrievalNode 缺少 topic")
        retry_queries: dict[str, list[str]] = {}

        # 只对被检查节点标记为不足的 Provider 生成补充 Query。
        tavily_trace = trace.get("tavily")
        if tavily_trace and tavily_trace.get("adaptive_triggered"):
            try:
                response = self.llm_client.invoke(
                    SYSTEM_PROMPT_TAVILY_QUERY_EXPANSION,
                    json.dumps({
                        "original_topic": topic,
                        "initial_queries": tavily_trace.get("initial_queries", []),
                        "initial_valid_count": tavily_trace.get("initial_valid_count", 0),
                        "initial_results": self._result_preview(
                            provider_candidates.get("tavily", [])
                        ),
                    }, ensure_ascii=False),
                )
                parsed = extract_json(response)
                generated = _normalized_queries(
                    parsed.get("supplementary_queries") if isinstance(parsed, dict) else None,
                    2,
                )
                initial = set(tavily_trace.get("initial_queries", []))
                generated = [query for query in generated if query not in initial]
                tavily_trace["supplementary_queries"] = generated
                if generated:
                    retry_queries["tavily"] = generated
            except Exception as exc:
                tavily_trace["error"] = str(exc)

        weibo_trace = trace.get("weibo")
        if weibo_trace and weibo_trace.get("adaptive_triggered"):
            try:
                response = self.llm_client.invoke(
                    SYSTEM_PROMPT_WEIBO_QUERY_EXPANSION,
                    json.dumps({
                        "original_topic": topic,
                        "initial_query": weibo_trace.get("initial_query", ""),
                        "initial_valid_count": weibo_trace.get("initial_valid_count", 0),
                        "initial_results": self._result_preview(
                            provider_candidates.get("weibo", [])
                        ),
                    }, ensure_ascii=False),
                )
                parsed = extract_json(response)
                generated = _normalized_queries(
                    parsed.get("refined_query") if isinstance(parsed, dict) else None,
                    1,
                )
                refined = generated[0] if generated else ""
                if refined == weibo_trace.get("initial_query"):
                    refined = ""
                weibo_trace["refined_query"] = refined
                if refined:
                    retry_queries["weibo"] = [refined]
            except Exception as exc:
                weibo_trace["error"] = str(exc)
        return retry_queries

    @staticmethod
    def _result_preview(candidates: list[MediaCandidate], limit: int = 8) -> list[dict]:
        return [
            {
                "title": item.title,
                "snippet": item.snippet[:500],
                "url": item.url,
            }
            for item in candidates[:limit]
        ]
