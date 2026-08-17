import unittest

from My_agent.tools.media_relevance import (
    is_media_candidate_relevant,
    is_weibo_candidate_relevant,
    normalize_match_text,
)
from My_agent.tools.media_discovery import MediaDiscovery
from My_agent.tools.media_models import MediaCandidate


NEWSNOW_RSS_CORE = ["新能源汽车召回"]
NEWSNOW_RSS_SUPPORT = ["电池", "安全隐患", "车企", "监管", "召回公告"]


class MediaCandidateRelevanceTests(unittest.TestCase):
    def test_newsnow_title_hits_core_term(self):
        self.assertTrue(is_media_candidate_relevant(
            "某车企发布新能源汽车召回通知", None,
            NEWSNOW_RSS_CORE, NEWSNOW_RSS_SUPPORT, "newsnow",
        ))

    def test_newsnow_title_hits_two_support_terms(self):
        self.assertTrue(is_media_candidate_relevant(
            "某车企回应电池安全隐患", None,
            NEWSNOW_RSS_CORE, NEWSNOW_RSS_SUPPORT, "newsnow",
        ))

    def test_newsnow_title_hits_only_one_support_term(self):
        self.assertFalse(is_media_candidate_relevant(
            "新能源汽车市场销量继续增长", None,
            NEWSNOW_RSS_CORE, NEWSNOW_RSS_SUPPORT, "newsnow",
        ))

    def test_newsnow_irrelevant_title(self):
        self.assertFalse(is_media_candidate_relevant(
            "虚构城市今日天气晴朗", None,
            NEWSNOW_RSS_CORE, NEWSNOW_RSS_SUPPORT, "newsnow",
        ))

    def test_rss_title_hits_core_term(self):
        self.assertTrue(is_media_candidate_relevant(
            "新能源汽车召回事项有新进展", "",
            NEWSNOW_RSS_CORE, NEWSNOW_RSS_SUPPORT, "rss",
        ))

    def test_rss_snippet_hits_core_term(self):
        self.assertTrue(is_media_candidate_relevant(
            "某企业发布通知", "摘要提到新能源汽车召回事项",
            NEWSNOW_RSS_CORE, NEWSNOW_RSS_SUPPORT, "rss",
        ))

    def test_rss_title_hits_two_support_terms(self):
        self.assertTrue(is_media_candidate_relevant(
            "监管关注车企最新公告", "",
            NEWSNOW_RSS_CORE, NEWSNOW_RSS_SUPPORT, "rss",
        ))

    def test_rss_title_and_snippet_are_irrelevant(self):
        self.assertFalse(is_media_candidate_relevant(
            "虚构展览开幕", "摘要介绍当地文化活动",
            NEWSNOW_RSS_CORE, NEWSNOW_RSS_SUPPORT, "rss",
        ))

    def test_normalization_handles_case_whitespace_and_punctuation(self):
        self.assertEqual(
            normalize_match_text("  Example，  EVENT！ "),
            "example event",
        )

    def test_weibo_local_fallback_is_recall_first(self):
        self.assertTrue(is_weibo_candidate_relevant(
            "某车企回应电池安全隐患",
            NEWSNOW_RSS_CORE,
            NEWSNOW_RSS_SUPPORT,
        ))
        self.assertFalse(is_weibo_candidate_relevant(
            "虚构城市今日天气晴朗",
            NEWSNOW_RSS_CORE,
            NEWSNOW_RSS_SUPPORT,
        ))

    def test_discovery_stage1_excludes_rejected_media_candidates(self):
        class FakeProvider:
            def search(self, queries, limit=20, progress=None):
                return [
                    MediaCandidate(
                        "某车企回应电池安全隐患",
                        "https://example.test/relevant",
                        "虚构媒体",
                        None,
                        discovered_by=("newsnow",),
                    ),
                    MediaCandidate(
                        "虚构城市今日天气晴朗",
                        "https://example.test/irrelevant",
                        "虚构媒体",
                        None,
                        discovered_by=("newsnow",),
                    ),
                ]

        result = MediaDiscovery({"newsnow": FakeProvider()}).run(
            ["虚构检索词"],
            newsnow_rss_core=NEWSNOW_RSS_CORE,
            newsnow_rss_support=NEWSNOW_RSS_SUPPORT,
        )

        self.assertEqual(
            [item.url for item in result.raw_candidates],
            ["https://example.test/relevant"],
        )
        self.assertEqual(result.stats["newsnow_relevance_filtered_count"], 1)


if __name__ == "__main__":
    unittest.main()
