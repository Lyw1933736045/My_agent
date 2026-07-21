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
    def test_limits_queries_to_three_and_deduplicates(self):
        node = QueryPlanNode(
            _FakeLLM(
                {
                    "topic": "新能源汽车支持政策",
                    "search_queries": [
                        "新能源汽车 税收优惠",
                        "新能源汽车 税收优惠",
                        "新能源汽车 购置税",
                        "新能源汽车 消费支持",
                        "不应保留",
                    ],
                }
            )
        )
        result = node.run({"query": "国务院有哪些新能源汽车支持政策？"})
        self.assertEqual(result["topic"], "新能源汽车支持政策")
        self.assertEqual(len(result["search_queries"]), 3)
        self.assertEqual(
            result["search_queries"],
            [
                "新能源汽车 税收优惠",
                "新能源汽车 购置税",
                "新能源汽车 消费支持",
            ],
        )
        self.assertEqual(result["official_queries"], result["search_queries"])
        self.assertEqual(result["media_queries"], result["search_queries"])

    def test_returns_separate_official_and_media_queries(self):
        result = QueryPlanNode(
            _FakeLLM(
                {
                    "topic": "新能源汽车政策影响",
                    "official_queries": ["新能源汽车 购置税"],
                    "media_queries": ["新能源车 政策影响", "车企 消费"],
                }
            )
        ).run({"query": "新能源汽车政策有什么影响？"})
        self.assertEqual(result["search_queries"], ["新能源汽车 购置税"])
        self.assertEqual(len(result["media_queries"]), 2)

    def test_accepts_single_item_array_wrapper(self):
        result = QueryPlanNode(
            _FakeLLM(
                [{
                    "topic": "降准影响",
                    "official_queries": ["降准 货币政策"],
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
                    "official_queries": ["降准 货币政策"],
                    "media_queries": ["央行 降准"],
                }}
            )
        ).run({"query": "降准有什么影响？"})
        self.assertEqual(result["media_queries"], ["央行 降准"])

    def test_prefers_fenced_json_over_explanation_brackets(self):
        response = """分析步骤：[不要解析这个数组]\n```json
        {"topic":"降准影响","official_queries":["降准"],"media_queries":["央行 降准"]}
        ```"""
        result = QueryPlanNode(_SequenceLLM([response])).run(
            {"query": "降准有什么影响？"}
        )
        self.assertEqual(result["topic"], "降准影响")

    def test_retries_once_to_repair_invalid_json(self):
        repaired = json.dumps(
            {
                "topic": "降准影响",
                "official_queries": ["降准"],
                "media_queries": ["央行 降准"],
            },
            ensure_ascii=False,
        )
        result = QueryPlanNode(_SequenceLLM(["not json", repaired])).run(
            {"query": "降准有什么影响？"}
        )
        self.assertEqual(result["official_queries"], ["降准"])

    def test_rejects_empty_query(self):
        with self.assertRaisesRegex(ValueError, "不能为空"):
            QueryPlanNode(_FakeLLM({})).run({"query": ""})
