import json
import unittest

from My_agent.nodes.query_plan_node import QueryPlanNode


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, system_prompt, user_prompt):
        return json.dumps(self.payload, ensure_ascii=False)


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

    def test_rejects_empty_query(self):
        with self.assertRaisesRegex(ValueError, "不能为空"):
            QueryPlanNode(_FakeLLM({})).run({"query": ""})
