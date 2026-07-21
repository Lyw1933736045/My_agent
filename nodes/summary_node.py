"""搜索资料总结节点，不读取任何外部协作模块。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_FIRST_SUMMARY, SYSTEM_PROMPT_REFLECTION_SUMMARY
from ..utils.text_processing import extract_json


class SummaryNode(BaseNode):
    prompt = ""
    output_key = ""

    def run(self, input_data: dict) -> str:
        response = self.llm_client.invoke(
            self.prompt, json.dumps(input_data, ensure_ascii=False)
        )
        parsed = extract_json(response)
        if not isinstance(parsed, dict) or not parsed.get(self.output_key):
            raise ValueError("LLM 未返回有效章节总结")
        return str(parsed[self.output_key]).strip()


class FirstSummaryNode(SummaryNode):
    prompt = SYSTEM_PROMPT_FIRST_SUMMARY
    output_key = "paragraph_latest_state"


class ReflectionSummaryNode(SummaryNode):
    prompt = SYSTEM_PROMPT_REFLECTION_SUMMARY
    output_key = "updated_paragraph_latest_state"
