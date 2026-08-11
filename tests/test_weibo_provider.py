import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from My_agent.evaluation.snapshot import write_weibo_raw
from My_agent.state import RunState
from My_agent.tools.media_discovery import MediaDiscovery
from My_agent.tools.media_models import MediaCandidate
from My_agent.tools.weibo_provider import WeiboProvider, parse_count


def _card(index: int, *, comments="评论", likes="赞", reposts="转发") -> str:
    wid = str(1000 + index)
    return f"""
    <div action-type="feed_list_item" mid="{wid}">
      <div class="content">
        <a nick-name="作者{index}" usercard="id={2000 + index}">作者{index}</a>
        <p class="txt">第{index}条 微博正文 <img alt="[笑]" /></p>
        <p class="from"><a href="//weibo.com/{2000 + index}/AbC{index}"
             title="2026-08-11 10:0{index}">刚刚</a></p>
        <a action-type="feed_list_forward"><span>转发</span>{reposts}</a>
        <a action-type="feed_list_comment"><span>评论</span>{comments}</a>
        <a action-type="feed_list_like"><span>赞</span>{likes}</a>
      </div>
    </div>
    """


class _Response:
    def __init__(self, text="", status=200, *, headers=None, payload=None):
        self.text = text
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("invalid", "", 0)
        return self._payload


class WeiboProviderTests(unittest.TestCase):
    def _provider(self, directory, responses, **kwargs):
        cookie = Path(directory) / "cookie.txt"
        cookie.write_text("SUB=test; WBPSESS=test", encoding="utf-8")
        session = MagicMock()
        session.get.side_effect = responses
        provider = WeiboProvider(
            cookie_file=str(cookie),
            session=session,
            request_interval_min=0,
            request_interval_max=0,
            comment_interval_min=0,
            comment_interval_max=0,
            **kwargs,
        )
        return provider, session

    def test_parse_chinese_counts(self):
        self.assertEqual(parse_count("赞"), 0)
        self.assertEqual(parse_count("评论"), 0)
        self.assertEqual(parse_count("1.2万"), 12000)
        self.assertEqual(parse_count("3万+"), 30000)
        self.assertEqual(parse_count("2亿"), 200000000)

    def test_parses_post_fields_without_live_network(self):
        with TemporaryDirectory() as directory:
            provider, session = self._provider(
                directory,
                [_Response(_card(1, comments="12", likes="1.2万", reposts="3万+"))],
                comments_enabled=False,
            )
            results = provider.search(["国债 期货"])

        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate.source_group, "social_media")
        self.assertEqual(candidate.snippet, "第1条 微博正文 [笑]")
        self.assertEqual(candidate.url, "https://weibo.com/2001/AbC1")
        self.assertEqual(candidate.published_at, "2026-08-11 10:01")
        self.assertEqual(candidate.metadata["user_name"], "作者1")
        self.assertEqual(candidate.metadata["likes_count"], 12000)
        self.assertEqual(candidate.metadata["comments_count"], 12)
        self.assertEqual(candidate.metadata["reposts_count"], 30000)
        self.assertTrue(candidate.metadata["content_ready"])
        self.assertNotIn("cookie", json.dumps(provider.raw_results).lower())

    @patch("My_agent.tools.weibo_provider.time.sleep")
    def test_stops_after_target_and_keeps_complete_last_page(self, _sleep):
        page1 = _card(1) + _card(2) + '<a href="?q=x&amp;page=2">下一页</a>'
        page2 = _card(3) + _card(4)
        with TemporaryDirectory() as directory:
            provider, session = self._provider(
                directory, [_Response(page1), _Response(page2)],
                target_posts=3, max_search_pages=3, comments_enabled=False,
            )
            results = provider.search(["测试"])

        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(len(results), 4)
        self.assertEqual([item.metadata["platform_rank"] for item in results], [1, 2, 3, 4])

    @patch("My_agent.tools.weibo_provider.time.sleep")
    def test_later_page_failure_keeps_previous_results(self, _sleep):
        page1 = _card(1) + '<a href="?q=x&amp;page=2">下一页</a>'
        with TemporaryDirectory() as directory:
            provider, session = self._provider(
                directory, [_Response(page1), _Response(status=432)],
                target_posts=20, max_search_pages=3, comments_enabled=False,
            )
            results = provider.search(["测试"])

        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(len(results), 1)
        self.assertIn("微博后续页面", provider.diagnostics.failed_sources)

    def test_fetches_comments_once_for_top_two_posts(self):
        search = (
            _card(1, comments="8", likes="1")
            + _card(2, comments="20", likes="2")
            + _card(3, comments="10", likes="3")
        )
        comment = lambda ident: _Response(payload={
            "ok": 1,
            "max_id": 99,
            "data": [{
                "id": ident,
                "created_at": "now",
                "text_raw": "评论正文",
                "like_counts": 2,
                "user": {"id": 9, "screen_name": "评论者"},
            }],
        })
        with TemporaryDirectory() as directory:
            provider, session = self._provider(
                directory, [_Response(search), comment(5002), comment(5003)],
                comments_enabled=True,
            )
            provider.search(["测试"])

        self.assertEqual(session.get.call_count, 3)
        by_wid = {item["wid"]: item for item in provider.raw_results}
        self.assertFalse(by_wid["1001"]["comments_fetch"]["attempted"])
        self.assertEqual(by_wid["1002"]["comments"][0]["text"], "评论正文")
        self.assertTrue(by_wid["1003"]["comments_fetch"]["truncated"])

    def test_missing_cookie_and_access_limit_fail_safely(self):
        provider = WeiboProvider(cookie_file="/definitely/missing", comments_enabled=False)
        self.assertEqual(provider.search(["测试"]), [])
        self.assertIn("微博", provider.diagnostics.failed_sources)

        with TemporaryDirectory() as directory:
            limited, session = self._provider(
                directory, [_Response(status=432)], comments_enabled=False,
            )
            self.assertEqual(limited.search(["测试"]), [])
            self.assertEqual(session.get.call_count, 1)
            self.assertIn("HTTP 432", limited.diagnostics.failed_sources["微博"])

    def test_login_redirect_and_login_html_are_detected(self):
        with TemporaryDirectory() as directory:
            redirected, _ = self._provider(
                directory,
                [_Response(status=302, headers={"Location": "https://passport.weibo.com/login"})],
                comments_enabled=False,
            )
            self.assertEqual(redirected.search(["测试"]), [])
            self.assertIn("Cookie", redirected.diagnostics.failed_sources["微博"])

            login_html, _ = self._provider(
                directory, [_Response("<title>登录 - 微博</title>")],
                comments_enabled=False,
            )
            self.assertEqual(login_html.search(["测试"]), [])

    def test_provider_failure_does_not_stop_other_provider(self):
        with TemporaryDirectory() as directory:
            failed, _ = self._provider(
                directory, [_Response(status=403)], comments_enabled=False,
            )
            other = MagicMock()
            other.search.return_value = [MediaCandidate(
                "测试新闻", "https://example.com/a", "测试媒体", None,
                snippet="测试关键词", discovered_by=("rss",),
            )]
            result = MediaDiscovery({"weibo": failed, "rss": other}).run(
                ["测试"], limit=10
            )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].source_name, "测试媒体")
        self.assertIn("weibo/微博", result.errors)

    def test_writes_raw_json_without_cookie(self):
        state = RunState(query="测试", weibo_raw=[{"wid": "1", "text": "正文"}])
        with TemporaryDirectory() as directory:
            path = write_weibo_raw(state, Path(directory))
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload, state.weibo_raw)


if __name__ == "__main__":
    unittest.main()
