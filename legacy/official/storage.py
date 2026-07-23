"""旧版官方候选与事实结果存储。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..deep_search.state import EventFact


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "my_agent.db"
DOCUMENT_STATUSES = ("pending", "processed", "failed", "rejected")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class DocumentRecord:
    id: int
    source_id: str
    title: str
    url: str
    published_at: Optional[str]
    status: str
    discovered_at: str
    last_seen_at: str
    error_message: Optional[str]


class Database:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    published_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'processed', 'failed', 'rejected')),
                    discovered_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS event_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    title TEXT,
                    publisher TEXT,
                    published_at TEXT,
                    document_number TEXT,
                    core_facts TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );
                """
            )

    def upsert_document(
        self,
        source_id: str,
        title: str,
        url: str,
        published_at: Optional[str],
    ) -> tuple[int, bool]:
        timestamp = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO documents (
                    source_id, title, url, published_at, status,
                    discovered_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (source_id, title, url, published_at, timestamp, timestamp),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                connection.execute(
                    "UPDATE documents SET last_seen_at = ? WHERE url = ?",
                    (timestamp, url),
                )
            row = connection.execute(
                "SELECT id FROM documents WHERE url = ?", (url,)
            ).fetchone()
        return int(row["id"]), inserted

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(**dict(row))

    def get_document(self, document_id: int) -> Optional[DocumentRecord]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        return self._row_to_document(row) if row else None

    def get_documents(self, document_ids: list[int]) -> list[DocumentRecord]:
        """按传入顺序读取多个候选，不静默忽略缺失 ID。"""
        documents = []
        for document_id in document_ids:
            document = self.get_document(document_id)
            if document is None:
                raise KeyError(f"未找到候选文件 ID：{document_id}")
            documents.append(document)
        return documents

    def list_documents(self, status: Optional[str] = None) -> list[DocumentRecord]:
        if status is not None and status not in DOCUMENT_STATUSES:
            raise ValueError(f"无效 documents.status：{status}")
        sql = "SELECT * FROM documents"
        params: tuple[str, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY COALESCE(published_at, '') DESC, id DESC"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._row_to_document(row) for row in rows]

    def list_pending(self) -> list[DocumentRecord]:
        return self.list_documents("pending")

    def update_status(
        self,
        document_id: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        if status not in DOCUMENT_STATUSES:
            raise ValueError(f"无效 documents.status：{status}")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE documents SET status = ?, error_message = ? WHERE id = ?",
                (status, error_message, document_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"未找到候选文件 ID：{document_id}")

    def save_event_fact(self, document_id: int, fact: EventFact) -> int:
        core_facts = (
            json.dumps(fact.core_facts, ensure_ascii=False)
            if fact.core_facts is not None
            else None
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO event_facts (
                    document_id, title, publisher, published_at,
                    document_number, core_facts, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    fact.title,
                    fact.publisher,
                    fact.published_at,
                    fact.document_number,
                    core_facts,
                    now_iso(),
                ),
            )
            return int(cursor.lastrowid)

    def get_event_facts(self, document_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM event_facts WHERE document_id = ? ORDER BY id",
                (document_id,),
            ).fetchall()
