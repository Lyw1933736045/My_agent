import json
import unittest

from My_agent.nodes.query_plan_node import QueryPlanNode


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, system_prompt, user_prompt):
        return json.dumps(self.payload, ensure_ascii=False)


class _SequenceLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def invoke(self, system_prompt, user_prompt):
        return self.responses.pop(0)


class QueryPlanNodeTests(unittest.TestCase):
    def test_limits_media_queries_to_five_and_deduplicates(self):
        node = QueryPlanNode(
            _FakeLLM(
                {
                    "topic": "新能源汽车支持政策",
                    "media_queries": [
                        "新能源汽车 税收优惠",
                        "新能源汽车 税收优惠",
                        "新能源汽车 购置税",
                        "新能源汽车 消费支持",
                        "新能源汽车 市场",
                        "不应保留",
                    ],
                }
            )
        )
        result = node.run({"query": "国务院有哪些新能源汽车支持政策？"})
        self.assertEqual(result["topic"], "新能源汽车支持政策")
        self.assertEqual(len(result["media_queries"]), 5)
        self.assertEqual(
            result["media_queries"],
            [
                "新能源汽车 税收优惠",
                "新能源汽车 购置税",
                "新能源汽车 消费支持",
                "新能源汽车 市场",
                "不应保留",
            ],
        )

    def test_returns_only_media_queries(self):
        result = QueryPlanNode(
            _FakeLLM(
                {
                    "topic": "新能源汽车政策影响",
                    "media_queries": ["新能源车 政策影响", "车企 消费"],
                }
            )
        ).run({"query": "新能源汽车政策有什么影响？"})
        self.assertNotIn("official_queries", result)
        self.assertNotIn("search_queries", result)
        self.assertEqual(len(result["media_queries"]), 2)
        self.assertEqual(result["provider_queries"]["weibo"], "新能源车 政策影响")

    def test_returns_llm_generated_balanced_weibo_query(self):
        result = QueryPlanNode(
            _FakeLLM({
                "topic": "测试主题",
                "media_queries": ["测试主体 核心事项", "测试对象 相关影响"],
                "provider_queries": {"weibo": "测试主体 核心事项"},
            })
        ).run({"query": "研究测试主题"})

        self.assertEqual(
            result["provider_queries"], {"weibo": "测试主体 核心事项"}
        )

    def test_accepts_single_item_array_wrapper(self):
        result = QueryPlanNode(
            _FakeLLM(
                [{
                    "topic": "降准影响",
                    "media_queries": ["央行 降准"],
                }]
            )
        ).run({"query": "降准有什么影响？"})
        self.assertEqual(result["topic"], "降准影响")

    def test_accepts_named_object_wrapper(self):
        result = QueryPlanNode(
            _FakeLLM(
                {"result": {
                    "topic": "降准影响",
                    "media_queries": ["央行 降准"],
                }}
            )
        ).run({"query": "降准有什么影响？"})
        self.assertEqual(result["media_queries"], ["央行 降准"])

    def test_prefers_fenced_json_over_explanation_brackets(self):
        response = """分析步骤：[不要解析这个数组]\n```json
        {"topic":"降准影响","media_queries":["央行 降准"]}
        ```"""
        result = QueryPlanNode(_SequenceLLM([response])).run(
            {"query": "降准有什么影响？"}
        )
        self.assertEqual(result["topic"], "降准影响")

    def test_retries_once_to_repair_invalid_json(self):
        repaired = json.dumps(
            {
                "topic": "降准影响",
                "media_queries": ["央行 降准"],
            },
            ensure_ascii=False,
        )
        result = QueryPlanNode(_SequenceLLM(["not json", repaired])).run(
            {"query": "降准有什么影响？"}
        )
        self.assertEqual(result["media_queries"], ["央行 降准"])

    def test_rejects_empty_query(self):
        with self.assertRaisesRegex(ValueError, "不能为空"):
            QueryPlanNode(_FakeLLM({})).run({"query": ""})
