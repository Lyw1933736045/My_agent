import json
import unittest

from My_agent.nodes.brief_node import BriefNode


def _brief_payload():
    return {
        "title": "模型标题会被覆盖",
        "executive_summary": [{"text": "核心结论", "source_ids": ["S01"]}],
        "official": {"overview": "", "topics": []},
        "media": {
            "overview": "媒体整体关注事件进展。",
            "domestic": {
                "overview": "境内媒体概述。",
                "topics": [{
                    "title": "动态主题",
                    "summary": "多家材料关注同一进展。",
                    "supporting_views": [],
                    "social_views": [],
                    "source_ids": ["S01", "S99"],
                }],
            },
            "overseas": {"overview": "", "topics": []},
        },
        "public_opinion": {"overview": "", "topics": []},
        "timeline": [{"date": "某日", "event": "事件发生", "source_ids": ["S01"]}],
        "key_metrics": [],
        "synthesis": {
            "consensus": [{"text": "材料形成一项共识。", "source_ids": ["S01"]}],
            "differences": [], "risks": [], "watch_points": [],
        },
        "sources": [{"id": "S99", "url": "https://invalid.example"}],
    }


class _FakeLLM:
    def __init__(self, payload=None):
        self.payload = payload or _brief_payload()
        self.user_prompt = ""

    def invoke(self, system_prompt, user_prompt):
        self.user_prompt = user_prompt
        return json.dumps(self.payload, ensure_ascii=False)


class BriefNodeTests(unittest.TestCase):
    def test_generates_trusted_brief_data_and_fixed_markdown(self):
        llm = _FakeLLM()
        result = BriefNode(llm).generate({
            "query": "虚构事件研究",
            "media_insights": [{
                "title": "虚构报道",
                "source_name": "示例媒体",
                "url": "https://media.example/a",
                "source_group": "news_media",
                "reported_facts": ["事件发生"],
            }],
            "social_insights": [],
        })

        self.assertEqual(result.data["title"], "虚构事件研究")
        self.assertEqual(result.data["sources"][0]["id"], "S01")
        self.assertEqual(
            result.data["media"]["domestic"]["topics"][0]["source_ids"],
            ["S01"],
        )
        self.assertIn("## 核心摘要", result.markdown)
        self.assertIn("## 二、媒体层面", result.markdown)
        self.assertIn("https://media.example/a", result.markdown)
        prompt = json.loads(llm.user_prompt)
        self.assertEqual(prompt["media_insights"][0]["source_id"], "S01")

    def test_program_limits_dynamic_topics(self):
        payload = _brief_payload()
        topic = payload["media"]["domestic"]["topics"][0]
        payload["media"]["domestic"]["topics"] = [dict(topic, title=f"主题{i}") for i in range(9)]
        result = BriefNode(_FakeLLM(payload)).generate({
            "query": "虚构事件",
            "media_insights": [{
                "title": "虚构报道", "source_name": "示例媒体",
                "url": "https://media.example/a", "source_group": "news_media",
            }],
            "social_insights": [],
        })
        self.assertEqual(len(result.data["media"]["domestic"]["topics"]), 6)

    def test_rejects_raw_content_or_search_summary(self):
        for forbidden in ("content", "search_summary"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "不接收"):
                    BriefNode(_FakeLLM()).run({
                        "media_insights": [{
                            "title": "虚构报道", "source_name": "示例媒体",
                            "url": "https://media.example/a", forbidden: "禁止输入",
                        }],
                        "social_insights": [], "topic": "虚构事件",
                    })


if __name__ == "__main__":
    unittest.main()
