"""旧版报告格式化节点。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_REPORT_FORMATTING
from ..utils.text_processing import clean_markdown_tags


class ReportFormattingNode(BaseNode):
    def run(self, input_data: dict) -> str:
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_REPORT_FORMATTING,
            json.dumps(input_data, ensure_ascii=False),
        )
        report = clean_markdown_tags(response)
        if not report:
            raise ValueError("LLM 未返回报告")
        return report

    @staticmethod
    def format_manually(input_data: dict) -> str:
        lines = [
            f"# {input_data['report_title']}",
            "",
            f"> 数据截止时间：{input_data['data_cutoff']}",
            "",
        ]
        for paragraph in input_data["paragraphs"]:
            lines.extend(
                [f"## {paragraph['title']}", "", paragraph["content"], ""]
            )
        return "\n".join(lines).strip()
