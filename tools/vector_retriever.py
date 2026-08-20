"""pgvector cosine retrieval for case and historical scopes."""

from __future__ import annotations

from ..run_repository import RunRepository


class VectorRetriever:
    def __init__(self, repository: RunRepository) -> None:
        self.repository = repository

    def search_case(
        self,
        query_vector: list[float],
        case_id: str,
        *,
        source_types: list[str] | None = None,
        top_k: int = 30,
    ) -> list[dict]:
        return self.repository.search_knowledge_chunks(
            query_vector,
            case_id=case_id,
            source_types=source_types,
            top_k=top_k,
        )

    def search_global(
        self,
        query_vector: list[float],
        exclude_case_id: str,
        *,
        source_types: list[str] | None = None,
        top_k: int = 15,
    ) -> list[dict]:
        return self.repository.search_knowledge_chunks(
            query_vector,
            exclude_case_id=exclude_case_id,
            source_types=source_types,
            top_k=top_k,
        )

    def search_all(
        self,
        query_vector: list[float],
        *,
        source_types: list[str] | None = None,
        top_k: int = 15,
        exclude_case_id: str | None = None,
    ) -> list[dict]:
        """Search the whole knowledge base. Historical news should not be limited to one case."""
        return self.repository.search_knowledge_chunks(
            query_vector,
            exclude_case_id=exclude_case_id,
            source_types=source_types,
            top_k=top_k,
        )
