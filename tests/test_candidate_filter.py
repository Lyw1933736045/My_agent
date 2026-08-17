import json
import unittest

from My_agent.nodes.candidate_filter_node import CandidateFilterNode
from My_agent.tools.media_models import MediaCandidate, MediaDocument


class _FakeLLM:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def invoke(self, system_prompt, user_prompt):
        self.calls.append(json.loads(user_prompt))
        return self.responses.pop(0)


def _candidate(title="央行宣布降准", snippet=""):
    return MediaCandidate(
        title, "https://example.com/a", "测试媒体", None,
        snippet=snippet, discovered_by=("rss",),
    )


class CandidateContentFilterTests(unittest.TestCase):
    def test_content_decisions_keep_input_order(self):
        candidates = [_candidate(), _candidate("债券市场观察")]
        documents = [
            MediaDocument(item, item.url, "2026-07-22T12:00:00+08:00", "text/html", content)
            for item, content in zip(candidates, [
                "央行宣布降低存款准备金率。",
                "这是一篇与主题无关的债券历史回顾。",
            ])
        ]
        llm = _FakeLLM([json.dumps([
            {"index": 1, "relevant": False, "score": 10, "reason": "没有讨论本次降准"},
            {"index": 0, "relevant": True, "score": 95, "reason": "正文直接讨论降准"},
        ], ensure_ascii=False)])
        decisions = CandidateFilterNode(llm).run({
            "stage": "content",
            "topic": "央行降准",
            "queries": ["央行 降准"],
            "documents": documents,
        })
        self.assertEqual([item.candidate for item in decisions], candidates)
        self.assertEqual([item.relevant for item in decisions], [True, False])

    def test_content_filter_rejects_empty_documents(self):
        candidate = _candidate()
        document = MediaDocument(candidate, candidate.url, "now", "text/html", "")
        with self.assertRaisesRegex(ValueError, "具有正文"):
            CandidateFilterNode(_FakeLLM()).run({
                "stage": "content",
                "topic": "央行降准",
                "queries": ["央行 降准"],
                "documents": [document],
            })

    def test_content_filter_accepts_separate_core_and_support_terms(self):
        candidate = _candidate()
        document = MediaDocument(candidate, candidate.url, "now", "text/html", "正文片段")
        llm = _FakeLLM([json.dumps([
            {"index": 0, "relevant": True, "score": 90, "reason": "正文实质相关"}
        ], ensure_ascii=False)])
        decisions = CandidateFilterNode(llm).run({
            "stage": "content",
            "topic": "虚构事件",
            "newsnow_rss_core": ["核心事项"],
            "newsnow_rss_support": ["监管"],
            "documents": [document],
        })
        self.assertTrue(decisions[0].relevant)
        self.assertEqual(llm.calls[0]["newsnow_rss_core"], ["核心事项"])
        self.assertEqual(llm.calls[0]["newsnow_rss_support"], ["监管"])


if __name__ == "__main__":
    unittest.main()
