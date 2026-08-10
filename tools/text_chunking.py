"""长正文的轻量切分与关键词片段选择。"""

from __future__ import annotations

import re


_BOUNDARY = re.compile(r"[\n。！？；.!?;]")


def split_text(
    content: str,
    *,
    chunk_size: int = 1500,
    overlap: int = 200,
) -> list[str]:
    """按接近自然边界的位置切分正文，并保留少量上下文重叠。"""
    text = content.strip()
    if not text:
        return []
    if chunk_size < 200:
        raise ValueError("chunk_size 不能小于 200")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于0且小于 chunk_size")

    chunks = []
    start = 0
    while start < len(text):
        target_end = min(start + chunk_size, len(text))
        end = target_end
        if target_end < len(text):
            minimum_end = start + int(chunk_size * 0.6)
            boundaries = [
                match.end()
                for match in _BOUNDARY.finditer(text, minimum_end, target_end)
            ]
            if boundaries:
                end = boundaries[-1]
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def select_chunks(
    chunks: list[str],
    *,
    topic: str,
    queries: list[str],
    top_k: int = 3,
) -> list[str]:
    """用主题和检索词做轻量评分，返回最相关片段并保持原文顺序。"""
    if top_k < 1:
        raise ValueError("top_k 必须是正整数")
    if not chunks:
        return []

    normalized_topic = " ".join(topic.casefold().split())
    normalized_queries = [
        " ".join(query.casefold().split())
        for query in queries
        if isinstance(query, str) and query.strip()
    ]
    terms = list(dict.fromkeys(
        term
        for query in normalized_queries
        for term in query.split()
        if term
    ))

    scored = []
    for index, chunk in enumerate(chunks):
        text = chunk.casefold()
        score = 0
        if normalized_topic and normalized_topic in text:
            score += 10
        score += sum(10 for query in normalized_queries if query in text)
        score += sum(3 for term in terms if term in text)
        scored.append((score, index))

    matched = [item for item in scored if item[0] > 0]
    if matched:
        selected_indexes = [
            index
            for _, index in sorted(matched, key=lambda item: (-item[0], item[1]))[
                :top_k
            ]
        ]
    else:
        fallback = [0, len(chunks) // 2, len(chunks) - 1]
        selected_indexes = list(dict.fromkeys(fallback))[:top_k]

    return [chunks[index] for index in sorted(selected_indexes)]
