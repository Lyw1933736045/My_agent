"""旧版报告结构规划节点。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_REPORT_STRUCTURE
from ..utils.text_processing import extract_json


class ReportStructureNode(BaseNode):
    def run(self, input_data: dict) -> dict:
        query = input_data["query"].strip()
        max_paragraphs = input_data["max_paragraphs"]
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_REPORT_STRUCTURE,
            json.dumps(
                {"query": query, "max_paragraphs": max_paragraphs},
                ensure_ascii=False,
            ),
        )
        parsed = extract_json(response)
        if not isinstance(parsed, dict):
            raise ValueError("报告规划必须是 JSON 对象")
        paragraphs = parsed.get("paragraphs", [])
        valid = [
            {"title": item["title"].strip(), "content": item["content"].strip()}
            for item in paragraphs[:max_paragraphs]
            if isinstance(item, dict) and item.get("title") and item.get("content")
        ]
        if not valid:
            valid = [{"title": "事件分析", "content": query}]
        return {
            "report_title": parsed.get("report_title") or f"{query}研究报告",
            "paragraphs": valid,
        }
