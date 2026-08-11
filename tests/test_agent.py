import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from My_agent.agent import FinancialMediaAgent
from My_agent.state import RunState
from My_agent.tools.media_models import (
    DiscoveryResult,
    MediaCandidate,
    MediaInsight,
    RelevanceDecision,
)


def _settings():
    return SimpleNamespace(
        QUERY_ENGINE_API_KEY="test",
        QUERY_ENGINE_MODEL_NAME="test-model",
        QUERY_ENGINE_BASE_URL=None,
        LLM_REQUEST_TIMEOUT=30,
        TAVILY_API_KEY="",
        WEB_REQUEST_TIMEOUT=10,
        WEB_MAX_CONTENT_BYTES=100_000,
        WEB_MAX_TEXT_LENGTH=20_000,
        WEB_USER_AGENT="test",
        SEARCH_CONTENT_MAX_LENGTH=10_000,
    )


def _media_config():
    return {
        "newsnow": {"enabled": False, "sources": []},
        "rss": {"enabled": False, "feeds": [], "max_age_days": 30},
        "tavily": {"enabled": False},
        "selection": {
            "candidate_limit": 20,
            "max_per_source": 3,
            "read_limit": 8,
            "social_read_limit": 5,
            "content_filter_max_chars": 3_000,
            "relevance_model_min_score": 60,
        },
    }


class FinancialMediaAgentTests(unittest.TestCase):
    def setUp(self):
        self.llm = MagicMock()
        self.agent = FinancialMediaAgent(_settings(), llm_client=self.llm)
        self.candidate = MediaCandidate(
            "央行宣布降准", "https://example.com/a", "测试媒体", None,
            discovered_by=("rss",), query="央行 降准",
        )

    @patch("My_agent.agent.load_media_sources", side_effect=_media_config)
    @patch("My_agent.agent.MediaDiscovery")
    def test_discover_orchestrates_plan_and_providers(self, discovery_cls, _config):
        self.agent.query_plan_node.run = MagicMock(return_value={
            "topic": "央行降准", "media_queries": ["央行 降准"],
            "provider_queries": {"weibo": "央行 降准"},
        })
        self.agent._build_providers = MagicMock(
            return_value={"rss": object(), "weibo": object()}
        )
        discovery_cls.return_value.run.return_value = DiscoveryResult(
            [self.candidate], {"selected_count": 1}, {}
        )
        state = self.agent.discover("央行降准")
        self.assertEqual(state.topic, "央行降准")
        self.assertEqual(state.discovery.candidates, [self.candidate])
        self.agent._build_providers.assert_called_once()
        discovery_cls.return_value.run.assert_called_once()

    def test_create_plan_does_not_call_providers(self):
        self.agent.query_plan_node.run = MagicMock(return_value={
            "topic": "央行降准", "media_queries": ["央行 降准"],
            "provider_queries": {"weibo": "央行 降准"},
        })
        self.agent._build_providers = MagicMock()

        state = self.agent.create_plan("央行降准")

        self.assertEqual(state.topic, "央行降准")
        self.assertEqual(state.media_queries, ["央行 降准"])
        self.assertEqual(state.provider_queries, {"weibo": "央行 降准"})
        self.assertIsNone(state.discovery)
        self.agent._build_providers.assert_not_called()

    @patch("My_agent.agent.load_media_sources", side_effect=_media_config)
    @patch("My_agent.agent.MediaDiscovery")
    def test_discover_from_plan_uses_approved_queries(self, discovery_cls, _config):
        state = RunState(
            query="央行降准",
            topic="央行降准",
            media_queries=["  央行   降准  ", "央行 降准", "债券 市场"],
            provider_queries={"weibo": "央行 降准"},
        )
        self.agent._build_providers = MagicMock(
            return_value={"rss": object(), "weibo": object()}
        )
        discovery_cls.return_value.run.return_value = DiscoveryResult(
            [self.candidate], {"selected_count": 1}, {}
        )

        result = self.agent.discover_from_plan(state)

        self.assertEqual(result.media_queries, ["央行 降准", "债券 市场"])
        discovery_cls.return_value.run.assert_called_once()
        self.assertEqual(
            discovery_cls.return_value.run.call_args.kwargs["provider_queries"],
            {"weibo": ["央行 降准"]},
        )

    @patch("My_agent.agent.load_media_sources", side_effect=_media_config)
    def test_run_updates_state_through_filter_extract_and_brief(self, _config):
        state = RunState(
            query="央行降准",
            topic="央行降准",
            media_queries=["央行 降准"],
            discovery=DiscoveryResult([self.candidate], {"selected_count": 1}, {}),
        )
        self.agent.discover = MagicMock(return_value=state)
        self.agent.reader.read = MagicMock(return_value=SimpleNamespace(
            final_url=self.candidate.url,
            fetched_at="2026-07-22T12:00:00+08:00",
            content_type="text/html",
            content="央行宣布降低存款准备金率。",
        ))
        self.agent.candidate_filter_node.run = MagicMock(return_value=[
            RelevanceDecision(self.candidate, "content", True, 95, "正文直接相关")
        ])
        insight = MediaInsight(
            title=self.candidate.title,
            source_name=self.candidate.source_name,
            url=self.candidate.url,
            reported_facts=["央行宣布降准"],
        )
        self.agent.media_node.run = MagicMock(return_value=[insight])
        self.agent.brief_node.run = MagicMock(return_value="# 简报")

        result = self.agent.run("央行降准")

        self.assertEqual(result.read_success_count, 1)
        self.assertEqual(result.relevant_documents_count, 1)
        self.assertEqual(result.insights, [insight])
        self.assertEqual(result.brief, "# 简报")

    @patch("My_agent.agent.load_media_sources", side_effect=_media_config)
    def test_content_ready_candidate_skips_web_reader(self, _config):
        candidate = MediaCandidate(
            "微博正文", "https://weibo.com/1/A", "微博", None,
            snippet="微博完整正文", discovered_by=("weibo",),
            source_group="social_media", metadata={"content_ready": True},
        )
        state = RunState(
            query="微博测试", topic="微博测试", media_queries=["微博测试"],
            discovery=DiscoveryResult([candidate], {"selected_count": 1}, {}),
        )
        self.agent.reader.read = MagicMock()
        self.agent.candidate_filter_node.run = MagicMock(return_value=[
            RelevanceDecision(candidate, "content", True, 95, "直接相关")
        ])
        insight = MediaInsight(
            title=candidate.title, source_name="微博", url=candidate.url,
            source_group="social_media",
        )
        self.agent.media_node.run = MagicMock(return_value=[insight])
        self.agent.brief_node.run = MagicMock(return_value="# 微博简报")

        result = self.agent.complete(state)

        self.agent.reader.read.assert_not_called()
        self.assertEqual(result.selected_documents[0].content, "微博完整正文")


if __name__ == "__main__":
    unittest.main()
