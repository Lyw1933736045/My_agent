"""LLM 输出清理与搜索资料格式化。"""

import json
import re
from typing import Any


def clean_json_tags(text: str) -> str:
    return re.sub(r"```(?:json)?|```", "", text, flags=re.IGNORECASE).strip()


def clean_markdown_tags(text: str) -> str:
    return re.sub(r"```(?:markdown)?|```", "", text, flags=re.IGNORECASE).strip()


def remove_reasoning_from_output(text: str) -> str:
    starts = [position for marker in ("{", "[") if (position := text.find(marker)) >= 0]
    return text[min(starts):].strip() if starts else text.strip()


def extract_json(text: str) -> Any:
    cleaned = clean_json_tags(remove_reasoning_from_output(text))
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(cleaned[index:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("无法从 LLM 响应中解析 JSON")


def format_search_results_for_prompt(
    search_results: list[dict[str, Any]], max_length: int = 20000
) -> list[dict[str, Any]]:
    """保留引用字段，并在总字符预算内截断正文。"""
    if not search_results:
        return []
    per_result = max(500, max_length // len(search_results))
    formatted = []
    for result in search_results:
        formatted.append(
            {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "published_date": result.get("published_date"),
                "source": result.get("source", ""),
                "content": (result.get("content") or "")[:per_result],
            }
        )
    return formatted
