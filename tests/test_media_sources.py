import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from My_agent.nodes.media_node import MediaNode
from My_agent.state import MediaDocument
from My_agent.tools.media_models import MediaCandidate
from My_agent.tools.newsnow_provider import NewsNowProvider, filter_media_candidates
from My_agent.tools.rss_provider import RSSProvider
from My_agent.utils.media_sources import (
    MediaSourcesConfigError,
    load_media_sources,
    resolve_feed_url,
)


class MediaSourceConfigTests(unittest.TestCase):
    def test_loads_newsnow_rss_and_selection(self):
        config = load_media_sources()
        self.assertTrue(config["newsnow"]["sources"])
        social_ids = [
            item["id"] for item in config["newsnow"]["sources"]
            if item["source_group"] == "social_media"
        ]
        self.assertEqual(social_ids, ["weibo", "zhihu", "bilibili-hot-search"])
        self.assertFalse(config["official"]["enabled"])
        self.assertTrue(config["rss"]["feeds"])
        self.assertTrue(all(
            item["layer"] == "media"
            and item["source_group"] in {"news_media", "social_media"}
            for item in config["rss"]["feeds"]
        ))
        self.assertEqual(len(config["rss"]["official_feeds"]), 8)
        self.assertEqual(
            len([item for item in config["rss"]["official_feeds"] if item.get("enabled", True)]),
            6,
        )
        self.assertGreater(config["selection"]["candidate_limit"], 0)

    @patch.dict("os.environ", {"RSSHUB_BASE": "https://rsshub.example/"})
    def test_expands_rsshub_base(self):
        self.assertEqual(
            resolve_feed_url("${RSSHUB_BASE}/gov/cn/news/zhengce"),
            "https://rsshub.example/gov/cn/news/zhengce",
        )

    @patch.dict("os.environ", {}, clear=True)
    def test_rejects_missing_rsshub_base(self):
        with self.assertRaisesRegex(MediaSourcesConfigError, "RSSHUB_BASE"):
            resolve_feed_url("${RSSHUB_BASE}/gov/cn/news/zhengce")


class NewsNowProviderTests(unittest.TestCase):
    @patch("My_agent.tools.newsnow_provider.urlopen", side_effect=TimeoutError)
    def test_timeout_only_skips_failed_platform(self, mocked_urlopen):
        provider = NewsNowProvider(
            "https://newsnow.example/api/s",
            [{"id": "cls-hot", "name": "财联社", "expected_domain": "cls.cn"}],
        )
        self.assertEqual(provider.search(["央行 降准"]), [])

    def test_parses_filters_and_rejects_wrong_domain(self):
        provider = NewsNowProvider(
            "https://newsnow.example/api/s",
            [{"id": "cls-hot", "name": "财联社", "expected_domain": "cls.cn"}],
        )
        provider.fetch_json = lambda source_id: {
            "status": "success",
            "items": [
                {"title": "新能源汽车购置税政策延续", "url": "https://www.cls.cn/a/1"},
                {"title": "新能源汽车购置税假链接", "url": "https://evil.example/a"},
                {"title": "无关标题", "url": "https://www.cls.cn/a/2"},
            ],
        }
        results = provider.search(["新能源汽车 购置税"], limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source_name, "财联社")
        self.assertEqual(results[0].discovered_by, "newsnow")

    def test_marks_configured_source_group(self):
        provider = NewsNowProvider(
            "https://newsnow.example/api/s",
            [{
                "id": "weibo",
                "name": "微博",
                "expected_domain": "weibo.com",
                "source_group": "social_media",
            }],
        )
        provider.fetch_json = lambda source_id: {
            "status": "success",
            "items": [{"title": "央行政策热议", "url": "https://s.weibo.com/topic"}],
        }
        results = provider.search(["央行 政策"])
        self.assertEqual(results[0].source_group, "social_media")

    def test_deduplicates_and_limits_each_source(self):
        candidates = [
            MediaCandidate(f"政策影响分析{i}", f"https://a.example/{i}", "媒体A", None)
            for i in range(5)
        ]
        candidates.append(
            MediaCandidate("政策影响分析0", "https://b.example/1", "媒体B", None)
        )
        results = filter_media_candidates(
            candidates, ["政策 影响"], limit=10, max_per_source=2
        )
        self.assertEqual(len(results), 2)


class RSSProviderTests(unittest.TestCase):
    @patch("My_agent.tools.rss_provider.urlopen", side_effect=TimeoutError)
    def test_timeout_only_skips_failed_feed(self, mocked_urlopen):
        provider = RSSProvider(
            [{"id": "test", "name": "测试媒体", "url": "https://media.example/rss"}]
        )
        self.assertEqual(provider.search(["央行 降准"]), [])

    def test_parses_rss_candidate(self):
        now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        raw = f"""<?xml version='1.0'?>
        <rss><channel><item><title>新能源汽车政策影响</title>
        <link>https://media.example/article</link><pubDate>{now}</pubDate>
        <description><![CDATA[车企与消费者受到影响。]]></description>
        </item></channel></rss>""".encode()
        provider = RSSProvider([], max_age_days=3)
        results = provider.parse(raw, {
            "id": "test",
            "name": "测试媒体",
            "source_group": "social_media",
        })
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].discovered_by, "rss")
        self.assertEqual(results[0].source_group, "social_media")
        self.assertIn("消费者", results[0].snippet)


class _FakeLLM:
    def invoke(self, system_prompt, user_prompt):
        payload = json.loads(user_prompt)["documents"][0]
        return json.dumps(
            [{
                "title": payload["title"],
                "source_name": payload["source_name"],
                "url": payload["url"],
                "published_at": None,
                "reported_facts": ["报道事实"],
                "interpretations": ["媒体解释"],
                "affected_parties": ["消费者"],
                "risks_or_disagreements": [],
            }],
            ensure_ascii=False,
        )


class MediaNodeTests(unittest.TestCase):
    def test_extracts_structured_media_insight(self):
        candidate = MediaCandidate(
            "政策影响", "https://media.example/start", "测试媒体", None
        )
        document = MediaDocument(
            candidate=candidate,
            final_url="https://media.example/final",
            fetched_at="2026-07-21T12:00:00+08:00",
            content_type="text/html",
            content="媒体正文明确讨论消费者受到的影响。",
        )
        insights = MediaNode(_FakeLLM()).run([document])
        self.assertEqual(insights[0].interpretations, ["媒体解释"])
        self.assertEqual(insights[0].url, document.final_url)
        self.assertEqual(insights[0].source_group, "news_media")


if __name__ == "__main__":
    unittest.main()
