"""Qwen embedding client. Always bypasses local HTTP proxies."""

from __future__ import annotations

import httpx
from openai import OpenAI

from ..utils.config import Settings


class EmbeddingService:
    """Batch document embeddings and one-off query embeddings."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        dimension: int,
        timeout: float = 60.0,
        batch_size: int = 20,
    ) -> None:
        if not api_key:
            raise ValueError("Embedding API Key 不能为空")
        if not model:
            raise ValueError("EMBEDDING_MODEL 不能为空")
        self.model = model
        self.dimension = int(dimension)
        self.batch_size = max(1, int(batch_size))
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            max_retries=0,
            http_client=httpx.Client(trust_env=False, timeout=timeout),
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "EmbeddingService":
        settings = settings or Settings()
        api_key = settings.EMBEDDING_API_KEY or settings.DASHSCOPE_API_KEY
        return cls(
            api_key=api_key,
            model=settings.EMBEDDING_MODEL,
            base_url=settings.EMBEDDING_BASE_URL,
            dimension=settings.EMBEDDING_DIMENSION,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        values = [str(item or "") for item in texts]
        if not values:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(values), self.batch_size):
            vectors.extend(self._embed(values[start:start + self.batch_size]))
        return vectors

    def embed_query(self, question: str) -> list[float]:
        vectors = self._embed([str(question or "")])
        if not vectors:
            raise ValueError("Embedding 没有返回 query 向量")
        return vectors[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimension,
        )
        by_index = {
            int(item.index): list(item.embedding)
            for item in response.data
        }
        vectors = [by_index[index] for index in range(len(texts))]
        for vector in vectors:
            if len(vector) != self.dimension:
                raise ValueError(
                    f"Embedding 维度为 {len(vector)}，与配置 {self.dimension} 不一致"
                )
        return vectors
