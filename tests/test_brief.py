import json
import unittest

from My_agent.nodes.brief_node import BriefNode


class _FakeLLM:
    def __init__(self):
        self.user_prompt = ""

    def invoke(self, system_prompt, user_prompt):
        self.user_prompt = user_prompt
        return (
            "# 政策简报\n\n## 主题概述\n概述。\n\n"
            "## 官方来源\n[政策文件](https://www.gov.cn/policy.htm)"
        )


class BriefNodeTests(unittest.TestCase):
    def test_accepts_only_facts_and_official_links(self):
        llm = _FakeLLM()
        brief = BriefNode(llm).run(
            {
                "topic": "新能源汽车政策",
                "documents": [
                    {
                        "document_id": 1,
                        "official_url": "https://www.gov.cn/policy.htm",
                        "event_fact": {
                            "title": "政策文件",
                            "publisher": "国务院",
                            "published_at": None,
                            "document_number": None,
                            "core_facts": ["事实一"],
                        },
                    }
                ],
            }
        )
        payload = json.loads(llm.user_prompt)
        self.assertNotIn("content", payload["documents"][0])
        self.assertNotIn("search_summary", payload["documents"][0])
        self.assertIn("https://www.gov.cn/policy.htm", brief)

    def test_rejects_web_content_or_search_summary(self):
        for forbidden in ("content", "search_summary"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "不接收"):
                    BriefNode(_FakeLLM()).run(
                        {
                            "documents": [
                                {
                                    "official_url": "https://www.gov.cn/a.htm",
                                    "event_fact": {"title": "文件"},
                                    forbidden: "禁止输入",
                                }
                            ]
                        }
                    )
