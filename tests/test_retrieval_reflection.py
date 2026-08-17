import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from My_agent.nodes.retrieval_reflection_node import (
    AdaptiveRetrievalNode,
    RetrievalCheckNode,
)
from My_agent.agent import FinancialMediaAgent
from My_agent.run_repository import RunRepository
from My_agent.tools.media_discovery import MediaDiscovery
from My_agent.tools.media_models import MediaCandidate


def _candidate(provider: str, index: int, *, url: str | None = None) -> MediaCandidate:
    target = url or f"https://example.com/{provider}/{index}"
    return MediaCandidate(
        title=f"核心事件 {provider} {index}",
        url=target,
        source_name=provider,
        published_at=None,
        snippet=f"核心事件正文 {index}",
        discovered_by=(provider,),
        source_group="social_media" if provider == "weibo" else "news_media",
        guid=f"weibo:{index}" if provider == "weibo" else None,
    )


class _SequenceLLM:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def invoke(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, json.loads(user_prompt)))
        return json.dumps(self.payloads.pop(0), ensure_ascii=False)


class _Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search(self, queries, limit=20, progress=None):
        self.calls.append(list(queries))
        return self.responses.pop(0)


class RetrievalReflectionTests(unittest.TestCase):
    def test_check_uses_provider_specific_thresholds_and_deduplicates_urls(self):
        duplicate = _candidate(
            "tavily", 2, url="https://example.com/tavily/1?utm_source=test"
        )
        trace = RetrievalCheckNode().run({
            "provider_candidates": {
                "tavily": [_candidate("tavily", 1), duplicate],
                "weibo": [_candidate("weibo", 1), _candidate("weibo", 2)],
            },
            "tavily_queries": ["首次查询"],
            "weibo_queries": ["微博查询"],
            "thresholds": {"tavily": 3, "weibo": 2},
        })

        self.assertEqual(trace["tavily"]["initial_valid_count"], 1)
        self.assertFalse(trace["tavily"]["adaptive_triggered"])
        self.assertEqual(
            trace["tavily"]["adaptive_disabled_reason"], "single_rewrite_only"
        )
        self.assertEqual(trace["weibo"]["initial_valid_count"], 2)
        self.assertFalse(trace["weibo"]["adaptive_triggered"])

    def test_adaptive_node_generates_only_queries_for_triggered_providers(self):
        llm = _SequenceLLM([
            {"supplementary_queries": ["补充查询一", "补充查询二", "忽略第三条"]}
        ])
        trace = {
            "tavily": {
                "initial_queries": ["首次查询"],
                "initial_valid_count": 1,
                "adaptive_triggered": True,
                "supplementary_queries": [],
            },
            "weibo": {
                "initial_query": "微博查询",
                "initial_valid_count": 3,
                "adaptive_triggered": False,
                "refined_query": "",
            },
        }
        retry = AdaptiveRetrievalNode(llm).run({
            "topic": "核心事件",
            "provider_candidates": {
                "tavily": [_candidate("tavily", 1)],
                "weibo": [_candidate("weibo", 1)],
            },
            "trace": trace,
        })

        self.assertEqual(retry, {"tavily": ["补充查询一", "补充查询二"]})
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0][1]["initial_valid_count"], 1)

    def test_discovery_does_not_rewrite_tavily_and_keeps_weibo_threshold(self):
        tavily = _Provider([
            [_candidate("tavily", 1), _candidate("tavily", 2)],
            [
                _candidate("tavily", 2),
                _candidate("tavily", 3),
                _candidate("tavily", 4),
            ],
        ])
        weibo = _Provider([
            [_candidate("weibo", 1), _candidate("weibo", 2)]
        ])
        adaptive = AdaptiveRetrievalNode(_SequenceLLM([
            {"supplementary_queries": ["核心事件 补充影响"]}
        ]))

        result = MediaDiscovery({"tavily": tavily, "weibo": weibo}).run(
            ["核心事件"],
            tavily_queries=["核心事件 首次"],
            weibo_queries=["核心事件 微博"],
            topic="核心事件",
            retrieval_check_node=RetrievalCheckNode(),
            adaptive_retrieval_node=adaptive,
            adaptive_config={
                "enabled": True,
                "tavily_min_valid_results": 3,
                "weibo_min_valid_results": 2,
            },
        )

        self.assertEqual(tavily.calls, [["核心事件 首次"]])
        self.assertEqual(weibo.calls, [["核心事件 微博"]])
        self.assertEqual(result.retrieval_reflection["tavily"]["retry_valid_count"], 0)
        self.assertEqual(result.retrieval_reflection["tavily"]["final_valid_count"], 2)
        self.assertEqual(result.retrieval_reflection["weibo"]["final_valid_count"], 2)
        self.assertEqual(len(result.raw_candidates), 4)

    @unittest.skipUnless(
        os.getenv("TEST_DATABASE_URL"),
        "设置 TEST_DATABASE_URL 后运行 PostgreSQL 集成测试",
    )
    def test_trace_is_persisted_for_api_and_cli_outputs(self):
        trace = {
            "tavily": {
                "initial_valid_count": 2,
                "adaptive_triggered": True,
                "retry_valid_count": 4,
                "final_valid_count": 5,
            }
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RunRepository(os.environ["TEST_DATABASE_URL"])
            run_id = uuid4().hex
            repository.create(
                run_id=run_id,
                query="核心事件",
                topic="核心事件",
                tavily_queries=["核心事件"],
            )
            repository.save_retrieval_reflection(run_id, trace)
            self.assertEqual(repository.get(run_id).retrieval_reflection, trace)

            report_path = root / "report.md"
            report_path.write_text("# 报告\n", encoding="utf-8")
            trace_path = FinancialMediaAgent.save_retrieval_reflection(
                trace, report_path
            )
            self.assertEqual(
                json.loads(trace_path.read_text(encoding="utf-8")), trace
            )


if __name__ == "__main__":
    unittest.main()
