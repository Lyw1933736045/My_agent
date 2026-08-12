"""基于实际网页正文的主题相关性复核。"""

from __future__ import annotations

import json

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_CONTENT_RELEVANCE, SYSTEM_PROMPT_METADATA_RELEVANCE
from ..tools.media_models import MediaCandidate, MediaDocument, RelevanceDecision
from ..utils.text_processing import extract_json


def _terms(candidate: MediaCandidate, queries: list[str]) -> tuple[str, ...]:
    text = f"{candidate.title} {candidate.snippet}".casefold()
    return tuple(dict.fromkeys(
        term
        for query in queries
        for term in query.casefold().split()
        if term and term in text
    ))


class CandidateFilterNode(BaseNode):
    """只让高相关候选进入观点提取与简报节点。

    metadata 阶段可以筛选标题/摘要；content 阶段筛选真实正文。
    当前 Agent 的 complete() 重点使用 content 阶段。
    """

    def run(self, input_data: dict) -> list[RelevanceDecision]:
        stage = str(input_data.get("stage", "")).strip()
        topic = str(input_data.get("topic", "")).strip()
        queries = input_data.get("queries")
        if not topic or not isinstance(queries, list) or not queries:
            raise ValueError("CandidateFilterNode 缺少 topic 或 queries")
        # 根据 stage 决定当前是在筛选候选元数据，还是筛选网页正文。
        if stage == "content":
            return self._filter_content(
                input_data.get("documents"),
                topic,
                queries,
                int(input_data.get("max_content_chars", 6_000)),
                int(input_data.get("model_min_score", 60)),
            )
        if stage == "metadata":
            candidates = input_data.get("candidates")
            if not isinstance(candidates, list):
                raise ValueError("候选筛选需要 candidates 列表")
            payload = [{
                "index": index, "title": item.title, "snippet": item.snippet,
            } for index, item in enumerate(candidates) if isinstance(item, MediaCandidate)]
            parsed = self._invoke(SYSTEM_PROMPT_METADATA_RELEVANCE, {
                "topic": topic, "queries": queries, "candidates": payload,
            })
            results = self._result_map(parsed)
            return [self._decision_from_item(
                item, "metadata", results.get(index), _terms(item, queries),
                "模型未返回该候选的判断", int(input_data.get("model_min_score", 50)),
            ) for index, item in enumerate(candidates)]
        raise ValueError("CandidateFilterNode.stage 必须是 metadata 或 content")

    def _filter_content(
        self,
        documents: object,
        topic: str,
        queries: list[str],
        max_content_chars: int,
        model_min_score: int,
    ) -> list[RelevanceDecision]:
        if not isinstance(documents, list):
            raise ValueError("正文筛选需要 documents 列表")
        payload_documents = []
        for index, document in enumerate(documents):
            if not isinstance(document, MediaDocument) or not document.content.strip():
                raise ValueError("documents 必须包含具有正文的 MediaDocument")
            payload_documents.append({
                "index": index,
                "title": document.candidate.title,
                "source_group": document.candidate.source_group,
                "content": document.content[:max(500, max_content_chars)],
            })
        if not payload_documents:
            return []
        parsed = self._invoke(SYSTEM_PROMPT_CONTENT_RELEVANCE, {
            "topic": topic, "queries": queries, "documents": payload_documents,
        })
        model_results = self._result_map(parsed)
        decisions = []
        for index, document in enumerate(documents):
            decisions.append(self._decision_from_item(
                document.candidate,
                "content",
                model_results.get(index),
                _terms(document.candidate, queries),
                "模型未返回该正文的判断",
                model_min_score,
            ))
        return decisions

    def _invoke(self, prompt: str, payload: dict):
        response = self.llm_client.invoke(prompt, json.dumps(payload, ensure_ascii=False))
        parsed = extract_json(response)
        if not isinstance(parsed, list):
            raise ValueError("相关性筛选模型未返回 JSON 数组")
        return parsed

    @staticmethod
    def _result_map(items: list) -> dict[int, dict]:
        return {
            item["index"]: item
            for item in items
            if isinstance(item, dict) and isinstance(item.get("index"), int)
        }

    @staticmethod
    def _decision_from_item(
        candidate: MediaCandidate,
        stage: str,
        item: dict | None,
        matched: tuple[str, ...],
        missing_reason: str,
        min_score: int,
    ) -> RelevanceDecision:
        if item is None:
            return RelevanceDecision(candidate, stage, False, 0, missing_reason, matched)
        raw_score = item.get("score", 0)
        score = max(0, min(100, int(raw_score))) if isinstance(raw_score, (int, float)) else 0
        # 模型必须明确判定 relevant=True，并且分数达到阈值，才算通过。
        relevant = item.get("relevant") is True and score >= min_score
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = "模型未提供判断理由"
        return RelevanceDecision(candidate, stage, relevant, score, reason.strip(), matched)
