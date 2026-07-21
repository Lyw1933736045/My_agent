import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from My_agent.nodes.fact_node import FactNode
from My_agent.state import EventFact, SourceDocument, State
from My_agent.tools.web_reader import WebReader


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
        final_url: str = "https://official.example/final",
    ) -> None:
        self._body = body
        self._final_url = final_url
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self) -> str:
        return self._final_url

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_system_prompt = ""
        self.last_user_prompt = ""

    def invoke(self, system_prompt: str, user_prompt: str) -> str:
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        return self.response


class WebReaderTests(unittest.TestCase):
    @patch("My_agent.tools.web_reader.urlopen")
    def test_reads_visible_html_and_records_final_url(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeResponse(
            b"""
            <html><head><style>hidden</style><script>ignored()</script></head>
            <body><h1>Official Notice</h1><p>Confirmed fact.</p></body></html>
            """
        )

        result = WebReader().read("https://official.example/start")

        self.assertEqual(result.final_url, "https://official.example/final")
        self.assertEqual(result.content_type, "text/html")
        self.assertIn("Official Notice", result.content)
        self.assertIn("Confirmed fact.", result.content)
        self.assertNotIn("ignored", result.content)
        self.assertTrue(result.fetched_at)

    @patch("My_agent.tools.web_reader.urlopen")
    def test_rejects_unsupported_content_type(self, mocked_urlopen):
        mocked_urlopen.return_value = _FakeResponse(
            b"%PDF", content_type="application/pdf"
        )
        with self.assertRaisesRegex(ValueError, "暂不支持"):
            WebReader().read("https://official.example/document.pdf")

    def test_rejects_non_http_url(self):
        with self.assertRaisesRegex(ValueError, "HTTP"):
            WebReader().read("file:///tmp/document.html")


class FactNodeTests(unittest.TestCase):
    def test_unknown_fields_become_none(self):
        llm = _FakeLLM(
            json.dumps(
                {
                    "title": "  官方公告  ",
                    "publisher": None,
                    "published_at": "",
                    "document_number": 123,
                    "core_facts": [" 事实一 ", "", None],
                },
                ensure_ascii=False,
            )
        )
        source = SourceDocument(
            official_url="https://official.example/start",
            final_url="https://official.example/final",
            fetched_at="2026-07-20T10:00:00+08:00",
            content_type="text/html",
            content="官方公告正文，包含事实一。",
        )

        fact = FactNode(llm).run(source)

        self.assertEqual(fact.title, "官方公告")
        self.assertIsNone(fact.publisher)
        self.assertIsNone(fact.published_at)
        self.assertIsNone(fact.document_number)
        self.assertEqual(fact.core_facts, ["事实一"])
        self.assertIn("官方公告正文", llm.last_user_prompt)

    def test_state_serializes_unknown_fields_as_json_null(self):
        state = State(
            query="https://official.example/document",
            event_fact=EventFact(title="公告"),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "state.json"
            state.save_to_file(output)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertIsNone(payload["event_fact"]["publisher"])
        self.assertIsNone(payload["event_fact"]["published_at"])
        self.assertIsNone(payload["event_fact"]["document_number"])
        self.assertIsNone(payload["event_fact"]["core_facts"])


if __name__ == "__main__":
    unittest.main()
