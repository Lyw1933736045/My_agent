"""基于微博桌面搜索页的保守关键词 Provider。"""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import html
import json
from pathlib import Path
import random
import re
import time
from typing import Any
from urllib.parse import parse_qs

import requests

from .media_models import MediaCandidate, ProviderDiagnostics


_COUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|亿)?")


def parse_count(value: object) -> int:
    """把微博页面上的中文计数转换为非负整数。"""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = " ".join(str(value).split()).replace(",", "")
    match = _COUNT_RE.search(text)
    if not match:
        return 0
    number = float(match.group(1))
    multiplier = {"万": 10_000, "亿": 100_000_000}.get(match.group(2), 1)
    return max(0, int(number * multiplier))


def _attrs(items: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key: value or "" for key, value in items}


class _SearchPageParser(HTMLParser):
    """只解析 action-type=feed_list_item 范围内的公开字段。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[dict[str, Any]] = []
        self._post: dict[str, Any] | None = None
        self._depth = 0
        self._capture: str | None = None
        self._capture_tag: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = _attrs(attrs)
        if tag == "div" and attributes.get("action-type") == "feed_list_item":
            if self._post is not None:
                self._finish_post()
            self._post = {
                "wid": attributes.get("mid", ""),
                "mblogid": "",
                "user_id": "",
                "user_name": "",
                "published_at": None,
                "url": "",
                "text_parts": [],
                "reposts_count": 0,
                "comments_count": 0,
                "likes_count": 0,
                "text_complete": True,
            }
            self._depth = 1
            return
        if self._post is None:
            return
        if tag == "div":
            self._depth += 1
        if tag == "img" and self._capture == "text" and attributes.get("alt"):
            self._parts.append(attributes["alt"])
        if tag != "a" and tag != "p":
            return

        classes = set(attributes.get("class", "").split())
        action = attributes.get("action-type", "")
        if tag == "p" and "txt" in classes:
            self._begin_capture("text")
        elif tag == "a" and attributes.get("nick-name"):
            self._post["user_name"] = attributes["nick-name"]
            usercard = parse_qs(attributes.get("usercard", ""))
            self._post["user_id"] = (usercard.get("id") or [""])[0]
        elif tag == "a" and action in {
            "feed_list_forward", "feed_list_comment", "feed_list_like"
        }:
            self._begin_capture(action)
        elif tag == "a":
            href = attributes.get("href", "")
            match = re.search(r"//weibo\.com/(\d+)/([A-Za-z0-9]+)", href)
            if match and not self._post["mblogid"]:
                self._post["user_id"] = self._post["user_id"] or match.group(1)
                self._post["mblogid"] = match.group(2)
                self._post["url"] = "https:" + href if href.startswith("//") else href
                self._post["published_at"] = attributes.get("title") or None

    def handle_endtag(self, tag: str) -> None:
        if self._post is None:
            return
        if self._capture and tag == self._capture_tag:
            self._finish_capture()
        if tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                self._finish_post()

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        if self._post is not None:
            self._finish_post()

    def _begin_capture(self, name: str) -> None:
        self._capture = name
        self._capture_tag = "p" if name == "text" else "a"
        self._parts = []

    def _finish_capture(self) -> None:
        assert self._post is not None
        value = " ".join("".join(self._parts).split())
        if self._capture == "text":
            if value:
                self._post["text_parts"].append(value)
            if "展开全文" in value:
                self._post["text_complete"] = False
        else:
            field = {
                "feed_list_forward": "reposts_count",
                "feed_list_comment": "comments_count",
                "feed_list_like": "likes_count",
            }[self._capture]
            self._post[field] = parse_count(value)
        self._capture = None
        self._capture_tag = None
        self._parts = []

    def _finish_post(self) -> None:
        if self._post is None:
            return
        if self._capture:
            self._finish_capture()
        self._post["text"] = "\n".join(dict.fromkeys(self._post.pop("text_parts")))
        self.posts.append(self._post)
        self._post = None
        self._depth = 0


class WeiboProvider:
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    def __init__(
        self,
        *,
        cookie_file: str,
        search_url: str = "https://s.weibo.com/weibo",
        comments_url: str = "https://weibo.com/ajax/statuses/buildComments",
        target_posts: int = 20,
        max_search_pages: int = 3,
        timeout: float = 20,
        request_interval_min: float = 4,
        request_interval_max: float = 8,
        trust_env_proxy: bool = False,
        comments_enabled: bool = False,
        max_comment_posts: int = 2,
        comment_interval_min: float = 5,
        comment_interval_max: float = 10,
        session: requests.Session | None = None,
    ) -> None:
        self.cookie_file = Path(cookie_file).expanduser()
        self.search_url = search_url
        self.comments_url = comments_url
        self.target_posts = max(1, int(target_posts))
        self.max_search_pages = max(1, min(3, int(max_search_pages)))
        self.timeout = max(1.0, float(timeout))
        self.request_interval_min = max(0.0, float(request_interval_min))
        self.request_interval_max = max(
            self.request_interval_min, float(request_interval_max)
        )
        self.comments_enabled = bool(comments_enabled)
        self.max_comment_posts = max(0, min(2, int(max_comment_posts)))
        self.comment_interval_min = max(0.0, float(comment_interval_min))
        self.comment_interval_max = max(
            self.comment_interval_min, float(comment_interval_max)
        )
        self.session = session or requests.Session()
        self.session.trust_env = bool(trust_env_proxy)
        self.diagnostics = ProviderDiagnostics()
        self.raw_results: list[dict[str, Any]] = []
        self.request_count = 0

    def search(self, queries: list[str], limit: int = 20, progress=None) -> list[MediaCandidate]:
        del limit  # 网络停止条件由 target_posts/max_search_pages 独立控制。
        self.raw_results = []
        self.request_count = 0
        self.diagnostics = ProviderDiagnostics(status_counts={"requests": 0})
        keyword = next((" ".join(q.split()) for q in queries if q.strip()), "")
        if not keyword:
            return []
        try:
            cookie = self._read_cookie()
            posts = self._fetch_posts(keyword, cookie, progress)
            if self.comments_enabled and posts:
                self._fetch_selected_comments(posts, cookie, progress)
            fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.raw_results = [
                {**post, "fetched_at": fetched_at, "query": keyword}
                for post in posts
            ]
            candidates = [self._to_candidate(post) for post in self.raw_results]
            self.diagnostics.successful_sources["微博"] = (
                f"{len(candidates)} 条，{self.request_count} 次请求"
            )
            return candidates
        except (OSError, ValueError, requests.RequestException) as exc:
            self.diagnostics.failed_sources["微博"] = str(exc)
            if progress:
                progress(f"微博跳过：{exc}")
            return []
        finally:
            self.diagnostics.status_counts["requests"] = self.request_count

    def _read_cookie(self) -> str:
        try:
            cookie = self.cookie_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("微博 Cookie 文件不存在或不可读") from exc
        if cookie.lower().startswith("cookie:"):
            cookie = cookie.split(":", 1)[1].strip()
        if not cookie or "=" not in cookie:
            raise ValueError("微博 Cookie 文件为空或格式无效")
        return cookie

    def _get(self, url: str, *, cookie: str, params: dict[str, object]):
        headers = dict(self.DEFAULT_HEADERS)
        headers["Cookie"] = cookie
        headers["Referer"] = "https://s.weibo.com/"
        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        finally:
            self.request_count += 1
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location", "")
            if "passport" in location.lower() or "login" in location.lower():
                raise ValueError("微博 Cookie 已失效或域名不匹配")
            raise ValueError(f"微博返回重定向 HTTP {response.status_code}")
        if response.status_code in {403, 418, 432}:
            raise ValueError(f"微博触发访问限制 HTTP {response.status_code}")
        if response.status_code != 200:
            raise ValueError(f"微博返回 HTTP {response.status_code}")
        return response

    def _fetch_posts(self, keyword: str, cookie: str, progress=None) -> list[dict]:
        results: list[dict] = []
        seen: set[str] = set()
        for page in range(1, self.max_search_pages + 1):
            if page > 1:
                time.sleep(random.uniform(
                    self.request_interval_min, self.request_interval_max
                ))
            try:
                response = self._get(
                    self.search_url,
                    cookie=cookie,
                    params={
                        "q": keyword,
                        "xsort": "hot",
                        "Refer": "hot_weibo",
                        "page": page,
                    },
                )
            except (ValueError, requests.RequestException) as exc:
                if not results:
                    raise
                self.diagnostics.failed_sources["微博后续页面"] = str(exc)
                if progress:
                    progress(f"微博第 {page} 页停止：{exc}；保留前页结果")
                break
            if self._is_login_html(response.text):
                raise ValueError("微博 Cookie 已失效，返回登录页面")
            parser = _SearchPageParser()
            parser.feed(response.text)
            parser.close()
            new_count = 0
            for parsed in parser.posts:
                wid = str(parsed.get("wid") or "")
                text = str(parsed.get("text") or "").strip()
                if not wid or not text or wid in seen:
                    continue
                seen.add(wid)
                new_count += 1
                parsed.update({
                    "platform": "weibo",
                    "platform_rank": len(results) + 1,
                    "search_sort": "hot",
                    "comments": [],
                    "comments_fetch": {
                        "attempted": False,
                        "success": False,
                        "returned_count": 0,
                        "has_more": False,
                        "truncated": False,
                        "error": None,
                    },
                })
                results.append(parsed)
            if progress:
                progress(f"微博第 {page} 页：新增 {new_count} 条，累计 {len(results)} 条")
            if len(results) >= self.target_posts or new_count == 0:
                break
            if not self._has_next_page(response.text, page):
                break
        return results

    def _fetch_selected_comments(self, posts: list[dict], cookie: str, progress=None) -> None:
        targets = sorted(
            (post for post in posts if post["comments_count"] > 0),
            key=lambda post: (
                post["comments_count"], post["likes_count"], post["reposts_count"]
            ),
            reverse=True,
        )[: self.max_comment_posts]
        for index, post in enumerate(targets):
            if index:
                time.sleep(random.uniform(
                    self.comment_interval_min, self.comment_interval_max
                ))
            fetch = post["comments_fetch"]
            fetch["attempted"] = True
            try:
                response = self._get(
                    self.comments_url,
                    cookie=cookie,
                    params={
                        "id": post["wid"], "is_reload": 1,
                        "is_show_bulletin": 2, "is_mix": 0,
                        "count": 20, "fetch_level": 0, "locale": "zh-CN",
                    },
                )
                payload = response.json()
                if payload.get("ok") != 1 or not isinstance(payload.get("data"), list):
                    raise ValueError("微博评论接口返回异常")
                post["comments"] = [
                    self._parse_comment(item, post["wid"])
                    for item in payload["data"]
                    if isinstance(item, dict) and item.get("id") is not None
                ]
                fetch.update({
                    "success": True,
                    "returned_count": len(post["comments"]),
                    "has_more": bool(payload.get("max_id")),
                    "truncated": bool(payload.get("max_id")),
                })
                if progress:
                    progress(f"微博评论：{post['wid']} 保存 {len(post['comments'])} 条")
            except (ValueError, requests.RequestException, json.JSONDecodeError) as exc:
                fetch["error"] = str(exc)
                if progress:
                    progress(f"微博评论跳过：{post['wid']}（{exc}）")

    @staticmethod
    def _parse_comment(item: dict, post_wid: str) -> dict[str, Any]:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        raw_text = item.get("text_raw") or item.get("text") or ""
        text = re.sub(r"<[^>]+>", "", str(raw_text))
        return {
            "comment_id": str(item.get("idstr") or item.get("id")),
            "post_wid": post_wid,
            "user_id": str(user.get("idstr") or user.get("id") or ""),
            "user_name": str(user.get("screen_name") or ""),
            "created_at": item.get("created_at"),
            "text": html.unescape(text).strip(),
            "likes_count": parse_count(item.get("like_counts")),
        }

    @staticmethod
    def _is_login_html(body: str) -> bool:
        lowered = body.lower()
        return (
            "passport.weibo.com" in lowered
            or "sina visitor system" in lowered
            or ("<title" in lowered and "登录" in body[:5000])
        )

    @staticmethod
    def _has_next_page(body: str, current_page: int) -> bool:
        return bool(re.search(rf"[?&](?:amp;)?page={current_page + 1}(?:\D|$)", body))

    @staticmethod
    def _to_candidate(post: dict[str, Any]) -> MediaCandidate:
        text = str(post["text"])
        metadata = {
            key: value for key, value in post.items()
            if key not in {"text", "query", "published_at", "url"}
        }
        metadata["content_ready"] = True
        return MediaCandidate(
            title=text[:80] or f"微博 {post['wid']}",
            url=str(post.get("url") or f"https://weibo.com/detail/{post['wid']}"),
            source_name="微博",
            published_at=post.get("published_at"),
            snippet=text,
            discovered_by=("weibo",),
            source_group="social_media",
            query=str(post.get("query") or ""),
            guid=f"weibo:{post['wid']}",
            metadata=metadata,
        )
