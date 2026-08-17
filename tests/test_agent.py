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
            "topic": "央行降准",
            "newsnow_rss_core": ["虚构核心事项"],
            "newsnow_rss_support": ["虚构背景"],
            "tavily_queries": ["虚构搜索词"],
            "weibo_query": "虚构事件发生",
        })
        self.agent._build_providers = MagicMock(
            return_value={"rss": object(), "weibo": SimpleNamespace(raw_results=[])}
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
            "topic": "央行降准",
            "newsnow_rss_core": ["虚构核心事项"],
            "newsnow_rss_support": ["虚构背景"],
            "tavily_queries": ["虚构搜索词"],
            "weibo_query": "虚构事件发生",
        })
        self.agent._build_providers = MagicMock()

        state = self.agent.create_plan("央行降准")

        self.assertEqual(state.topic, "央行降准")
        self.assertEqual(state.newsnow_rss_core, ["虚构核心事项"])
        self.assertEqual(state.tavily_queries, ["虚构搜索词"])
        self.assertEqual(state.weibo_query, "虚构事件发生")
        self.assertIsNone(state.discovery)
        self.agent._build_providers.assert_not_called()

    @patch("My_agent.agent.load_media_sources", side_effect=_media_config)
    @patch("My_agent.agent.MediaDiscovery")
    def test_discover_from_plan_uses_direction_specific_queries(self, discovery_cls, _config):
        state = RunState(
            query="央行降准",
            topic="央行降准",
            newsnow_rss_core=["  虚构核心事项  "],
            newsnow_rss_support=["虚构背景"],
            tavily_queries=["虚构搜索词"],
        )
        self.agent._build_providers = MagicMock(
            return_value={"rss": object(), "weibo": SimpleNamespace(raw_results=[])}
        )
        discovery_cls.return_value.run.return_value = DiscoveryResult(
            [self.candidate], {"selected_count": 1}, {}
        )

        result = self.agent.discover_from_plan(state)

        self.assertEqual(result.newsnow_rss_core, ["虚构核心事项"])
        discovery_cls.return_value.run.assert_called_once()
        self.assertEqual(
            discovery_cls.return_value.run.call_args.kwargs["tavily_queries"],
            ["虚构搜索词"],
        )

    @patch("My_agent.agent.load_media_sources", side_effect=_media_config)
    def test_run_updates_state_through_filter_extract_and_brief(self, _config):
        state = RunState(
            query="央行降准",
            topic="央行降准",
            newsnow_rss_core=["虚构核心事项"],
            newsnow_rss_support=["虚构背景"],
            tavily_queries=["虚构搜索词"],
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
        self.agent.brief_node.generate = MagicMock(return_value=SimpleNamespace(
            markdown="# 简报", data={"title": "虚构事件"}
        ))

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
            query="微博测试", topic="微博测试", newsnow_rss_core=["虚构核心事项"],
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
        self.agent.brief_node.generate = MagicMock(return_value=SimpleNamespace(
            markdown="# 微博简报", data={"title": "微博测试"}
        ))

        result = self.agent.complete(state)

        self.agent.reader.read.assert_not_called()
        self.assertEqual(result.selected_documents[0].content, "微博完整正文")

    @patch("My_agent.agent.load_media_sources", side_effect=_media_config)
    def test_complete_reads_all_stage1_candidates_without_limit(self, _config):
        second = MediaCandidate(
            "第二篇正文", "https://example.com/b", "测试媒体", None,
            discovered_by=("rss",), query="虚构核心事项",
        )
        state = RunState(
            query="虚构事件", topic="虚构事件",
            newsnow_rss_core=["虚构核心事项"],
            discovery=DiscoveryResult(
                [self.candidate], {"selected_count": 1}, {}, (),
                [self.candidate, second],
            ),
        )
        self.agent.reader.read = MagicMock(side_effect=[
            SimpleNamespace(
                final_url=item.url,
                fetched_at="2026-07-22T12:00:00+08:00",
                content_type="text/html",
                content=f"{item.title}的完整正文",
            )
            for item in (self.candidate, second)
        ])
        self.agent.candidate_filter_node.run = MagicMock(return_value=[
            RelevanceDecision(item, "content", True, 90, "正文直接相关")
            for item in (self.candidate, second)
        ])
        self.agent.media_node.run = MagicMock(return_value=[MediaInsight(
            title="汇总", source_name="测试媒体", url="https://example.com/a"
        )])
        self.agent.brief_node.generate = MagicMock(return_value=SimpleNamespace(
            markdown="# 简报", data={"title": "虚构事件"}
        ))

        result = self.agent.complete(state)

        self.assertEqual(self.agent.reader.read.call_count, 2)
        self.assertEqual(result.read_attempted_count, 2)

    def test_weibo_is_filtered_before_persistence_and_comments(self):
        accepted = MediaCandidate(
            "相关微博", "https://weibo.com/1/A", "微博", None,
            snippet="虚构核心事项出现新进展", discovered_by=("weibo",),
            source_group="social_media", guid="weibo:1", metadata={"wid": "1"},
        )
        rejected = MediaCandidate(
            "无关微博", "https://weibo.com/2/B", "微博", None,
            snippet="完全无关内容", discovered_by=("weibo",),
            source_group="social_media", guid="weibo:2", metadata={"wid": "2"},
        )
        discovery = DiscoveryResult(
            candidates=[accepted, rejected], stats={}, raw_candidates=[accepted, rejected],
            provider_candidates={"weibo": [accepted, rejected]},
        )
        self.agent.candidate_filter_node.run = MagicMock(return_value=[
            RelevanceDecision(accepted, "content", True, 80, "正文直接相关"),
            RelevanceDecision(rejected, "content", False, 10, "与事件无关"),
        ])
        provider = SimpleNamespace(
            raw_results=[{"wid": "1"}, {"wid": "2"}],
            fetch_comments_for_candidates=MagicMock(side_effect=lambda items, progress=None: items),
        )

        result = self.agent._filter_weibo_before_persistence(
            discovery,
            provider,
            RunState(
                query="虚构事件", topic="虚构事件",
                newsnow_rss_core=["虚构核心事项"],
                newsnow_rss_support=["虚构背景", "虚构动作"],
            ),
            {"content_filter_max_chars": 3000},
        )

        self.assertEqual([item.guid for item in result.raw_candidates], ["weibo:1"])
        self.assertEqual(provider.raw_results, [{"wid": "1"}])
        provider.fetch_comments_for_candidates.assert_called_once()
        self.assertTrue(result.raw_candidates[0].metadata["prechecked_relevance"])


if __name__ == "__main__":
    unittest.main()
