import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests
from http.client import RemoteDisconnected

from My_agent.nodes.media_node import MediaNode
from My_agent.tools.media_discovery import MediaDiscovery
from My_agent.tools.media_models import (
    MediaCandidate,
    MediaDocument,
    ProviderDiagnostics,
)
from My_agent.tools.newsnow_provider import NewsNowProvider
from My_agent.tools.rss_provider import RSSProvider
from My_agent.utils.dedup import canonical_url, select_candidates
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
        self.assertNotIn("official", config)
        self.assertEqual(len(config["rss"]["feeds"]), 15)
        enabled = [item for item in config["rss"]["feeds"] if item.get("enabled", True)]
        self.assertEqual(len(enabled), 13)
        self.assertEqual(
            {item["source_group"] for item in config["rss"]["feeds"]},
            {"official_media", "news_media", "social_media"},
        )
        self.assertEqual(
            {item["id"] for item in enabled},
            {
                "cctv-xwlb",
                "people-finance",
                "inewsweek-finance",
                "chinanews-latest",
                "wallstreetcn-live",
                "wallstreetcn-hot",
                "wallstreetcn-news",
                "gelonghui-live",
                "gelonghui-home",
                "gelonghui-hot",
                "yicai-latest",
                "yicai-headline",
                "zhihu-hot",
            },
        )
        self.assertTrue(config["tavily"]["enabled"])
        self.assertGreater(config["selection"]["candidate_limit"], 0)
        self.assertEqual(config["newsnow"]["timeout_seconds"], 30)
        self.assertEqual(config["newsnow"]["max_retries"], 1)
        self.assertEqual(config["rss"]["request_interval_min_seconds"], 0.5)
        self.assertEqual(config["rss"]["max_retries"], 1)

    @patch.dict("os.environ", {"RSSHUB_BASE": "https://rsshub.example/"})
    def test_expands_rsshub_base(self):
        self.assertEqual(
            resolve_feed_url("${RSSHUB_BASE}/gov/cn/news/zhengce"),
            "https://rsshub.example/gov/cn/news/zhengce",
        )

    @patch("My_agent.utils.media_sources.load_dotenv")
    @patch.dict("os.environ", {}, clear=True)
    def test_rejects_missing_rsshub_base(self, _load_dotenv):
        with self.assertRaisesRegex(MediaSourcesConfigError, "RSSHUB_BASE"):
            resolve_feed_url("${RSSHUB_BASE}/gov/cn/news/zhengce")


class NewsNowProviderTests(unittest.TestCase):
    @patch("My_agent.tools.newsnow_provider.time.sleep")
    @patch("My_agent.tools.newsnow_provider.urlopen")
    def test_remote_disconnect_retries_then_continues_to_next_platform(
        self, mocked_urlopen, _mocked_sleep
    ):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers.get_content_charset.return_value = "utf-8"
        response.read.return_value = b'{"status":"success","items":[]}'
        mocked_urlopen.side_effect = [
            RemoteDisconnected("closed"),
            RemoteDisconnected("closed"),
            response,
        ]
        provider = NewsNowProvider(
            "https://newsnow.example/api/s",
            [
                {"id": "a", "name": "媒体A"},
                {"id": "b", "name": "媒体B"},
            ],
            retry_wait_min=0,
            retry_wait_max=0,
            request_interval=0,
        )

        self.assertEqual(provider.search(["央行"]), [])
        self.assertEqual(mocked_urlopen.call_count, 3)
        self.assertIn("媒体A", provider.diagnostics.failed_sources)
        self.assertEqual(provider.diagnostics.status_counts["success"], 1)

    @patch("My_agent.tools.newsnow_provider.time.sleep")
    @patch("My_agent.tools.newsnow_provider.urlopen", side_effect=TimeoutError)
    def test_timeout_retries_once_then_records_failed_platform(
        self, mocked_urlopen, mocked_sleep
    ):
        provider = NewsNowProvider(
            "https://newsnow.example/api/s",
            [{"id": "cls-hot", "name": "财联社", "expected_domain": "cls.cn"}],
            retry_wait_min=0,
            retry_wait_max=0,
            request_interval=0,
        )
        self.assertEqual(provider.search(["央行 降准"]), [])
        self.assertEqual(mocked_urlopen.call_count, 2)
        self.assertEqual(provider.diagnostics.failed_sources.keys(), {"财联社"})
        mocked_sleep.assert_called_once_with(0.0)

    def test_records_success_and_cache_responses(self):
        provider = NewsNowProvider(
            "https://newsnow.example/api/s",
            [
                {"id": "a", "name": "媒体A", "expected_domain": "a.example"},
                {"id": "b", "name": "媒体B", "expected_domain": "b.example"},
            ],
            request_interval=0,
        )
        provider.fetch_json = lambda source_id: {
            "status": "success" if source_id == "a" else "cache",
            "items": [],
        }
        provider.search(["央行 降准"])
        self.assertEqual(
            provider.diagnostics.status_counts, {"success": 1, "cache": 1}
        )
        self.assertEqual(
            provider.diagnostics.successful_sources,
            {"媒体A": "success，0 条", "媒体B": "cache，0 条"},
        )

    def test_parses_and_rejects_wrong_domain_without_relevance_filtering(self):
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
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].source_name, "财联社")
        self.assertEqual(results[0].discovered_by, ("newsnow",))

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
        results, _ = select_candidates(
            candidates, ["政策 影响"], limit=10, max_per_source=2
        )
        self.assertEqual(len(results), 2)


class RSSProviderTests(unittest.TestCase):
    def test_timeout_only_skips_and_records_failed_feed(self):
        session = MagicMock()
        session.headers = {}
        session.get.side_effect = requests.Timeout("timeout")
        provider = RSSProvider(
            [{"id": "test", "name": "测试媒体", "url": "https://media.example/rss"}],
            request_interval_min=0,
            request_interval_max=0,
            retry_wait_min=0,
            retry_wait_max=0,
            session=session,
        )
        with patch("My_agent.tools.rss_provider.time.sleep"):
            self.assertEqual(provider.search(["央行 降准"]), [])
        self.assertEqual(session.get.call_count, 2)
        self.assertIn("测试媒体", provider.diagnostics.failed_sources)

    def test_parses_rss_candidate(self):
        now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        raw = f"""<?xml version='1.0'?>
        <rss><channel><item><title>新能源汽车政策影响</title>
        <link>https://media.example/article</link><pubDate>{now}</pubDate>
        <guid>article-123</guid>
        <description><![CDATA[车企与消费者受到影响。]]></description>
        </item></channel></rss>""".encode()
        provider = RSSProvider([], max_age_days=3)
        results = provider.parse(raw, {
            "id": "test",
            "name": "测试媒体",
            "source_group": "social_media",
        })
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].discovered_by, ("rss",))
        self.assertEqual(results[0].source_group, "social_media")
        self.assertEqual(results[0].guid, "article-123")
        self.assertIn("消费者", results[0].snippet)

    def test_per_feed_max_items_and_max_age_override(self):
        old = (datetime.now(timezone.utc) - timedelta(days=60)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        raw = f"""<rss><channel>
        <item><title>旧文章一</title><link>https://media.example/1</link>
        <pubDate>{old}</pubDate></item>
        <item><title>旧文章二</title><link>https://media.example/2</link>
        <pubDate>{old}</pubDate></item>
        </channel></rss>""".encode()
        provider = RSSProvider([], max_age_days=3)
        results = provider.parse(raw, {
            "id": "test",
            "name": "测试媒体",
            "source_group": "news_media",
            "max_age_days": 0,
            "max_items": 1,
        })
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].max_age_days, 0)


class TavilyMediaProviderTests(unittest.TestCase):
    def test_converts_search_hits_to_media_candidates(self):
        from My_agent.tools.search import SearchResponse, SearchResult
        from My_agent.tools.tavily_provider import TavilyMediaProvider

        provider = TavilyMediaProvider("tvly-test", max_results_per_query=3)
        provider.agency.search = lambda **kwargs: SearchResponse(
            query=kwargs["query"],
            results=[
                SearchResult(
                    title="股指期货政策影响分析",
                    url="https://wallstreetcn.com/articles/1",
                    published_date="2026-07-21",
                    source="wallstreetcn.com",
                    content="机构观点与股指期货政策",
                ),
                SearchResult(
                    title="知乎讨论股指期货",
                    url="https://www.zhihu.com/question/1",
                    published_date=None,
                    source="zhihu.com",
                    content="社交讨论",
                ),
            ],
        )
        results = provider.search(["股指期货 政策"], limit=10)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].discovered_by, ("tavily",))
        self.assertEqual(results[0].query, "股指期货 政策")
        by_url = {item.url: item for item in results}
        self.assertEqual(
            by_url["https://wallstreetcn.com/articles/1"].source_group,
            "news_media",
        )
        self.assertEqual(
            by_url["https://www.zhihu.com/question/1"].source_group,
            "social_media",
        )


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


class _FakeProvider:
    def __init__(self, candidates=None, error=None, name=None, calls=None):
        self.candidates = candidates or []
        self.error = error
        self.name = name
        self.calls = calls

    def search(self, queries, limit=20, progress=None):
        if self.calls is not None:
            self.calls.append(self.name)
        if self.error:
            raise ValueError(self.error)
        return self.candidates


class MediaDiscoveryTests(unittest.TestCase):
    def test_collects_provider_diagnostics(self):
        provider = _FakeProvider()
        provider.diagnostics = ProviderDiagnostics(
            failed_sources={"媒体A": "请求超时"},
            status_counts={"success": 2, "cache": 1},
        )
        result = MediaDiscovery({"newsnow": provider}).run(
            ["央行 政策"], limit=10
        )
        self.assertEqual(result.errors["newsnow/媒体A"], "请求超时")
        self.assertEqual(result.stats["newsnow_failed_sources"], 1)
        self.assertEqual(result.stats["newsnow_successful_sources"], 0)
        self.assertEqual(result.stats["newsnow_success_responses"], 2)
        self.assertEqual(result.stats["newsnow_cache_responses"], 1)
        self.assertEqual(
            [(item.provider, item.name, item.ok) for item in result.sources],
            [("newsnow", "媒体A", False)],
        )

    def test_runs_providers_in_configured_order(self):
        calls = []
        result = MediaDiscovery({
            "newsnow": _FakeProvider(name="newsnow", calls=calls),
            "rss": _FakeProvider(name="rss", calls=calls),
            "tavily": _FakeProvider(name="tavily", calls=calls),
        }).run(["央行 政策"], limit=10)
        self.assertEqual(calls, ["newsnow", "rss", "tavily"])
        self.assertEqual(result.stats["fetched_count"], 0)

    def test_routes_provider_specific_queries(self):
        shared = _FakeProvider()
        special = _FakeProvider()
        shared.search = MagicMock(return_value=[])
        special.search = MagicMock(return_value=[])

        MediaDiscovery({"rss": shared, "weibo": special}).run(
            ["媒体 查询"],
            limit=10,
            provider_queries={"weibo": ["宽松 微博查询"]},
        )

        self.assertEqual(shared.search.call_args.args[0], ["媒体 查询"])
        self.assertEqual(special.search.call_args.args[0], ["宽松 微博查询"])

    def test_merges_provider_duplicates(self):
        rss = MediaCandidate(
            "央行政策热议", "https://example.com/a?utm_source=rss", "媒体A", None,
            discovered_by=("rss",), source_group="social_media",
        )
        tavily = MediaCandidate(
            "央行政策热议", "https://example.com/a", "媒体A", None,
            discovered_by=("tavily",), source_group="social_media", query="央行 政策",
        )
        result = MediaDiscovery({
            "rss": _FakeProvider([rss]), "tavily": _FakeProvider([tavily]),
        }).run(["央行 政策"], limit=10)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].source_group, "social_media")
        self.assertEqual(result.candidates[0].discovered_by, ("rss", "tavily"))
        self.assertEqual(result.stats["url_duplicates"], 1)

    def test_one_provider_failure_does_not_stop_others(self):
        candidate = MediaCandidate(
            "金融政策", "https://example.com/a", "媒体A", None,
            discovered_by=("rss",),
        )
        result = MediaDiscovery({
            "newsnow": _FakeProvider(error="暂时不可用"),
            "rss": _FakeProvider([candidate]),
        }).run(["金融 政策"], limit=10)
        self.assertEqual(len(result.candidates), 1)
        self.assertIn("newsnow", result.errors)
        self.assertEqual(result.stats["rss_count"], 1)

    def test_canonical_url_removes_tracking_and_fragment(self):
        self.assertEqual(
            canonical_url("https://EXAMPLE.com/a/?utm_source=x&keep=1#top"),
            "https://example.com/a?keep=1",
        )

    def test_filters_old_candidates_and_reports_count(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        old = MediaCandidate(
            "央行金融政策", "https://example.com/old", "媒体A", old_date,
            discovered_by=("rss",),
        )
        result = MediaDiscovery({"rss": _FakeProvider([old])}).run(
            ["央行 政策"], limit=10, max_age_days=30
        )
        self.assertEqual(result.candidates, [])
        self.assertEqual(result.stats["time_filtered_count"], 1)


if __name__ == "__main__":
    unittest.main()
