"""Backfill knowledge_chunks for existing cases. Does not run inside Alembic.

Run from the My_agent directory with the My_agent virtualenv:

    pip install pgvector
    python scripts/backfill_knowledge_chunks.py
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from My_agent.run_repository import RunRepository
from My_agent.tools.embedding_service import EmbeddingService
from My_agent.tools.knowledge_indexer import KnowledgeIndexer
from My_agent.utils.config import Settings


def main() -> None:
    settings = Settings()
    repository = RunRepository(settings.DATABASE_URL)
    indexer = KnowledgeIndexer(repository, EmbeddingService.from_settings(settings))
    case_ids = repository.list_case_ids()
    print(f"backfill cases={len(case_ids)}")
    for case_id in case_ids:
        try:
            stats = indexer.index_case(case_id)
            print(
                f"ok {case_id} total={stats['total']} "
                f"embedded={stats['embedded']} reused={stats['reused']}"
            )
        except Exception as exc:
            print(f"fail {case_id}: {exc}")


if __name__ == "__main__":
    main()
