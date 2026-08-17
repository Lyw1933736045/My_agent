import json
import unittest

from My_agent.nodes.query_plan_node import QueryPlanNode


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, system_prompt, user_prompt):
        return json.dumps(self.payload, ensure_ascii=False)


class QueryPlanNodeTests(unittest.TestCase):
    def _plan(self, **extra):
        payload = {
            "topic": "虚构事件",
            "newsnow_rss_core": ["虚构核心事项"],
            "newsnow_rss_support": ["虚构背景", "虚构动作"],
            "tavily_queries": ["虚构搜索词"],
            "weibo_query": "虚构事件发生",
        }
        payload.update(extra)
        return QueryPlanNode(_FakeLLM(payload)).run({"query": "研究虚构事件，包含虚构搜索词"})

    def test_returns_direction_specific_plan(self):
        result = self._plan()
        self.assertEqual(result["newsnow_rss_core"], ["虚构核心事项"])
        self.assertEqual(result["newsnow_rss_support"], ["虚构背景", "虚构动作"])
        self.assertEqual(result["tavily_queries"], ["虚构搜索词"])
        self.assertEqual(result["weibo_query"], "虚构事件发生")
        self.assertEqual(
            set(result),
            {"topic", "newsnow_rss_core", "newsnow_rss_support", "tavily_queries", "weibo_query"},
        )

    def test_limits_and_deduplicates_newsnow_rss_terms(self):
        result = self._plan(
            newsnow_rss_core=["甲", "甲", "乙", "丙", "丁"],
            newsnow_rss_support=["一", "二", "三", "四", "五", "六", "七"],
        )
        self.assertEqual(result["newsnow_rss_core"], ["甲", "乙", "丙"])
        self.assertEqual(result["newsnow_rss_support"], ["一", "二", "三", "四", "五", "六"])

    def test_accepts_wrapped_response(self):
        payload = {"result": {
            "topic": "虚构事件",
            "newsnow_rss_core": ["虚构核心事项"],
            "newsnow_rss_support": ["虚构背景"],
            "tavily_queries": ["虚构搜索词"],
            "weibo_query": "虚构事件发生",
        }}
        result = QueryPlanNode(_FakeLLM(payload)).run({"query": "研究虚构事件，包含虚构搜索词"})
        self.assertEqual(result["newsnow_rss_core"], ["虚构核心事项"])

    def test_tavily_keeps_one_query(self):
        result = self._plan(tavily_queries=["虚构搜索词", "不应保留"])
        self.assertEqual(result["tavily_queries"], ["虚构搜索词"])

    def test_rejects_missing_newsnow_rss_core(self):
        with self.assertRaisesRegex(ValueError, "newsnow_rss_core"):
            self._plan(newsnow_rss_core=[])

    def test_invalid_json_does_not_call_llm_twice(self):
        class InvalidLLM:
            def __init__(self):
                self.calls = 0

            def invoke(self, system_prompt, user_prompt):
                self.calls += 1
                return "not json"

        llm = InvalidLLM()
        with self.assertRaises(ValueError):
            QueryPlanNode(llm).run({"query": "虚构事件"})
        self.assertEqual(llm.calls, 1)


if __name__ == "__main__":
    unittest.main()
