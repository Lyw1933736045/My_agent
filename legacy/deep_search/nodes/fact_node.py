"""旧版官方事实提取节点。"""

import json
from typing import Any, Optional

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_FACT_EXTRACTION
from ..state import EventFact, SourceDocument
from ..utils.text_processing import extract_json


def _nullable_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


class FactNode(BaseNode):
    """严格依据正文抽取事实；未知字段统一归一化为 None。"""

    def run(self, input_data: SourceDocument) -> EventFact:
        if not isinstance(input_data, SourceDocument) or not input_data.content.strip():
            raise ValueError("FactNode 需要包含真实正文的 SourceDocument")

        payload = {
            "official_url": input_data.official_url,
            "final_url": input_data.final_url,
            "content_type": input_data.content_type,
            "document_content": input_data.content,
        }
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_FACT_EXTRACTION,
            json.dumps(payload, ensure_ascii=False),
        )
        parsed = extract_json(response)
        if not isinstance(parsed, dict):
            raise ValueError("LLM 未返回有效的事实对象")

        raw_core_facts = parsed.get("core_facts")
        core_facts = None
        if isinstance(raw_core_facts, list):
            normalized = [
                item.strip() for item in raw_core_facts
                if isinstance(item, str) and item.strip()
            ]
            core_facts = normalized or None

        return EventFact(
            title=_nullable_text(parsed.get("title")),
            publisher=_nullable_text(parsed.get("publisher")),
            published_at=_nullable_text(parsed.get("published_at")),
            document_number=_nullable_text(parsed.get("document_number")),
            core_facts=core_facts,
        )
