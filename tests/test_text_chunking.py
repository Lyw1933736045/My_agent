import unittest

from My_agent.tools.text_chunking import select_chunks, split_text


class TextChunkingTests(unittest.TestCase):
    def test_split_keeps_long_content_in_multiple_overlapping_chunks(self):
        content = "背景信息。" * 180 + "\n" + "降准影响银行流动性。" * 180

        chunks = split_text(content, chunk_size=500, overlap=80)

        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))

    def test_selects_relevant_chunk_even_when_it_is_near_the_end(self):
        chunks = [
            "无关的国际体育内容。",
            "一般市场回顾。",
            "央行降准将影响银行流动性和债券市场。",
        ]

        selected = select_chunks(
            chunks,
            topic="央行降准影响",
            queries=["央行 降准", "债券 市场"],
            top_k=1,
        )

        self.assertEqual(selected, [chunks[2]])

    def test_uses_document_spread_when_no_keyword_matches(self):
        chunks = ["第一段", "第二段", "第三段", "第四段", "第五段"]

        selected = select_chunks(
            chunks,
            topic="完全不同的主题",
            queries=["不存在的词"],
            top_k=3,
        )

        self.assertEqual(selected, ["第一段", "第三段", "第五段"])


if __name__ == "__main__":
    unittest.main()
