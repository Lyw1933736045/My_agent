"""旧版搜索规划节点。"""

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_FIRST_SEARCH, SYSTEM_PROMPT_REFLECTION
from ..utils.text_processing import extract_json


class SearchNode(BaseNode):
    prompt = ""

    def run(self, input_data: dict) -> dict:
        response = self.llm_client.invoke(
            self.prompt, json.dumps(input_data, ensure_ascii=False)
        )
        parsed = extract_json(response)
        if not isinstance(parsed, dict) or not parsed.get("search_query"):
            raise ValueError("LLM 未返回有效搜索词")
        depth = parsed.get("search_depth", "basic")
        parsed["search_depth"] = depth if depth in {"basic", "advanced"} else "basic"
        days = parsed.get("days")
        parsed["days"] = days if isinstance(days, int) and days > 0 else None
        return parsed


class FirstSearchNode(SearchNode):
    prompt = SYSTEM_PROMPT_FIRST_SEARCH


class ReflectionNode(SearchNode):
    prompt = SYSTEM_PROMPT_REFLECTION
