import unittest

from sqlalchemy.dialects.postgresql import JSONB

from My_agent.knowledge.models import Base, Document, Event, EventDocument, KnowledgeChunk
from My_agent.run_repository import RunRepository


class PostgreSQLStorageTests(unittest.TestCase):
    def test_storage_tables_include_knowledge_chunks(self):
        self.assertEqual(
            set(Base.metadata.tables),
            {"events", "documents", "event_documents", "knowledge_chunks"},
        )

    def test_json_fields_are_postgresql_jsonb(self):
        self.assertIsInstance(Event.__table__.c.search_plan.type, JSONB)
        self.assertIsInstance(Document.__table__.c.metadata.type, JSONB)
        self.assertIsInstance(EventDocument.__table__.c.discovery.type, JSONB)

    def test_repository_rejects_non_postgresql_url(self):
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            RunRepository("sqlite:///knowledge.db")


if __name__ == "__main__":
    unittest.main()
