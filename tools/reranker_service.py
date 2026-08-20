"""Qwen rerank client. Always bypasses local HTTP proxies."""

from __future__ import annotations

from typing import Any

import httpx

from ..utils.config import Settings


class RerankerService:
    """Independent rerank API: (question, chunks) -> scored chunks."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("Rerank API Key 不能为空")
        if not model:
            raise ValueError("RERANKER_MODEL 不能为空")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(trust_env=False, timeout=timeout)
        headers = {"Authorization": f"Bearer {api_key}"}
        self.client.headers.update(headers)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "RerankerService":
        settings = settings or Settings()
        api_key = settings.RERANKER_API_KEY or settings.DASHSCOPE_API_KEY
        return cls(
            api_key=api_key,
            model=settings.RERANKER_MODEL,
            base_url=settings.RERANKER_BASE_URL,
        )

    def rerank(self, question: str, chunks: list[dict], top_n: int | None = None) -> list[dict]:
        if not chunks:
            return []
        documents = [
            str(item.get("content") or item.get("preview") or "")[:2000]
            for item in chunks
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "query": str(question or ""),
            "documents": documents,
        }
        if top_n:
            payload["top_n"] = int(top_n)
        response = self.client.post(
            self.base_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        body = response.json()
        results = body.get("results") or (body.get("output") or {}).get("results") or []
        if not isinstance(results, list) or not results:
            raise ValueError("Rerank 没有返回 results")
        ranked = []
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if not isinstance(index, int) or index < 0 or index >= len(chunks) or index in seen:
                continue
            seen.add(index)
            ranked.append({
                **chunks[index],
                "rerank_score": float(item.get("relevance_score") or 0),
            })
        ranked.extend(item for index, item in enumerate(chunks) if index not in seen)
        return ranked
