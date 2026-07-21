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
    """按 TrendRadar 的顺序提取、解析并尝试本地修复 JSON。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("LLM 返回空响应")

    response = text.strip()
    if "```json" in response.lower():
        start = response.lower().find("```json") + len("```json")
        end = response.find("```", start)
        cleaned = response[start:end if end >= 0 else None].strip()
    elif "```" in response:
        parts = response.split("```", 2)
        cleaned = parts[1].strip() if len(parts) >= 2 else response
    else:
        cleaned = response

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as parse_error:
        try:
            from json_repair import repair_json

            repaired = repair_json(cleaned, return_objects=True)
            if isinstance(repaired, (dict, list)):
                return repaired
        except (ImportError, ValueError, TypeError):
            pass

        # 兼容没有代码围栏、JSON 前后夹带解释文字的模型。
        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(cleaned[index:])
                return value
            except json.JSONDecodeError:
                continue
        context = cleaned[max(0, parse_error.pos - 30):parse_error.pos + 30]
        raise ValueError(
            f"无法从 LLM 响应中解析 JSON：{parse_error.msg}；上下文：{context}"
        ) from parse_error


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
