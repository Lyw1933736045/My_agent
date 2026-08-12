"""FastAPI 研究任务的 SQLite 持久化仓库。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    query: str
    topic: str
    proposed_queries: list[str]
    provider_queries: dict[str, str]
    approved_queries: list[str]
    status: str
    progress: str
    report: str | None
    error: str | None
    source_results: list[dict]
    retrieval_reflection: dict


class RunRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        typedef: str,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")

    @staticmethod
    def _loads_sources(raw: str | None) -> list[dict]:
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return payload if isinstance(payload, list) else []

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress TEXT NOT NULL,
                    report TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS query_plans (
                    run_id TEXT PRIMARY KEY,
                    proposed_queries TEXT NOT NULL,
                    approved_queries TEXT,
                    review_status TEXT NOT NULL,
                    reviewed_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS media_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    snippet TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    source_group TEXT NOT NULL DEFAULT 'news_media',
                    query TEXT,
                    published_at TEXT,
                    content TEXT,
                    fetch_status TEXT NOT NULL DEFAULT 'pending',
                    fetch_error TEXT,
                    final_url TEXT,
                    content_type TEXT,
                    fetched_at TEXT,
                    analysis_status TEXT NOT NULL DEFAULT 'pending',
                    analysis_reason TEXT,
                    duplicate_of_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, url),
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                """
            )
            self._ensure_column(connection, "runs", "source_results", "TEXT")
            self._ensure_column(connection, "runs", "retrieval_reflection", "TEXT")
            self._ensure_column(connection, "query_plans", "provider_queries", "TEXT")
            self._ensure_column(connection, "media_candidates", "source_group", "TEXT NOT NULL DEFAULT 'news_media'")
            self._ensure_column(connection, "media_candidates", "analysis_status", "TEXT NOT NULL DEFAULT 'pending'")
            self._ensure_column(connection, "media_candidates", "analysis_reason", "TEXT")
            self._ensure_column(connection, "media_candidates", "duplicate_of_id", "INTEGER")
            now = self._now()
            connection.execute(
                """
                UPDATE runs
                SET status = 'failed',
                    progress = '服务重启，原后台任务已中断',
                    error_message = 'API服务重启导致后台任务中断，请重新创建任务',
                    updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )

    def save_candidates(self, run_id: str, candidates: list[dict]) -> None:
        now = self._now()
        with self._connection() as connection:
            for item in candidates:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO media_candidates
                    (run_id, title, url, snippet, source, provider, source_group,
                     query, published_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, item["title"], item["url"], item.get("snippet", ""),
                     item.get("source", ""), item.get("provider", ""),
                     item.get("source_group", "news_media"), item.get("query"),
                     item.get("published_at"), now, now),
                )

    def update_candidate_analysis(
        self,
        candidate_id: int,
        status: str,
        reason: str | None = None,
        duplicate_of_id: int | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE media_candidates
                SET analysis_status = ?, analysis_reason = ?, duplicate_of_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, reason, duplicate_of_id, self._now(), candidate_id),
            )

    def update_candidate_fetch(self, run_id: str, url: str, **values) -> None:
        allowed = {"content", "fetch_status", "fetch_error", "final_url",
                   "content_type", "fetched_at"}
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = self._now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connection() as connection:
            connection.execute(
                f"UPDATE media_candidates SET {assignments} WHERE run_id = ? AND url = ?",
                (*values.values(), run_id, url),
            )

    def list_candidates(self, run_id: str) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM media_candidates WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]
    def create(
        self,
        *,
        run_id: str,
        query: str,
        topic: str,
        proposed_queries: list[str],
        provider_queries: dict[str, str] | None = None,
    ) -> None:
        now = self._now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    id, query, topic, status, progress, created_at, updated_at
                ) VALUES (?, ?, ?, 'waiting_for_review', ?, ?, ?)
                """,
                (run_id, query, topic, "等待人工审核检索词", now, now),
            )
            connection.execute(
                """
                INSERT INTO query_plans (
                    run_id, proposed_queries, provider_queries, review_status
                ) VALUES (?, ?, ?, 'pending')
                """,
                (
                    run_id,
                    json.dumps(proposed_queries, ensure_ascii=False),
                    json.dumps(provider_queries or {}, ensure_ascii=False),
                ),
            )
            self._insert_event(
                connection, run_id, "info", "检索计划已生成，等待人工审核", now
            )

    def get(self, run_id: str) -> RunRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT r.*, q.proposed_queries, q.provider_queries, q.approved_queries
                FROM runs AS r
                JOIN query_plans AS q ON q.run_id = r.id
                WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=row["id"],
            query=row["query"],
            topic=row["topic"],
            proposed_queries=json.loads(row["proposed_queries"]),
            provider_queries=(
                json.loads(row["provider_queries"])
                if row["provider_queries"] else {}
            ),
            approved_queries=(
                json.loads(row["approved_queries"])
                if row["approved_queries"]
                else []
            ),
            status=row["status"],
            progress=row["progress"],
            report=row["report"],
            error=row["error_message"],
            source_results=self._loads_sources(row["source_results"]),
            retrieval_reflection=(
                json.loads(row["retrieval_reflection"])
                if row["retrieval_reflection"] else {}
            ),
        )

    def approve(self, run_id: str, approved_queries: list[str]) -> bool:
        now = self._now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = 'running', progress = ?, updated_at = ?
                WHERE id = ? AND status = 'waiting_for_review'
                """,
                ("已批准，等待后台执行", now, run_id),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                """
                UPDATE query_plans
                SET approved_queries = ?, review_status = 'approved', reviewed_at = ?
                WHERE run_id = ?
                """,
                (
                    json.dumps(approved_queries, ensure_ascii=False),
                    now,
                    run_id,
                ),
            )
            self._insert_event(connection, run_id, "info", "检索计划已批准", now)
        return True

    def update_progress(self, run_id: str, message: str) -> None:
        now = self._now()
        with self._connection() as connection:
            connection.execute(
                "UPDATE runs SET progress = ?, updated_at = ? WHERE id = ?",
                (message, now, run_id),
            )
            self._insert_event(connection, run_id, "info", message, now)

    def save_source_results(self, run_id: str, source_results: list[dict]) -> None:
        now = self._now()
        payload = json.dumps(source_results, ensure_ascii=False)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE runs
                SET source_results = ?, updated_at = ?
                WHERE id = ?
                """,
                (payload, now, run_id),
            )

    def save_retrieval_reflection(self, run_id: str, trace: dict) -> None:
        now = self._now()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE runs
                SET retrieval_reflection = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(trace, ensure_ascii=False), now, run_id),
            )

    def complete(
        self,
        run_id: str,
        report: str,
        *,
        source_results: list[dict] | None = None,
    ) -> None:
        now = self._now()
        with self._connection() as connection:
            if source_results is None:
                connection.execute(
                    """
                    UPDATE runs
                    SET status = 'completed', progress = '研究完成',
                        report = ?, error_message = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (report, now, run_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE runs
                    SET status = 'completed', progress = '研究完成',
                        report = ?, error_message = NULL,
                        source_results = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        report,
                        json.dumps(source_results, ensure_ascii=False),
                        now,
                        run_id,
                    ),
                )
            self._insert_event(connection, run_id, "info", "研究完成", now)

    def fail(
        self,
        run_id: str,
        error: str,
        *,
        source_results: list[dict] | None = None,
    ) -> None:
        now = self._now()
        with self._connection() as connection:
            if source_results is None:
                connection.execute(
                    """
                    UPDATE runs
                    SET status = 'failed', progress = '研究失败',
                        error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (error, now, run_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE runs
                    SET status = 'failed', progress = '研究失败',
                        error_message = ?, source_results = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        error,
                        json.dumps(source_results, ensure_ascii=False),
                        now,
                        run_id,
                    ),
                )
            self._insert_event(connection, run_id, "error", error, now)

    def cancel(self, run_id: str) -> bool:
        now = self._now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = 'canceled', progress = '任务已终止',
                    error_message = '用户终止任务', updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (now, run_id),
            )
            if cursor.rowcount:
                self._insert_event(connection, run_id, "info", "用户终止任务", now)
                return True
        return False

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        run_id: str,
        level: str,
        message: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO run_events (run_id, level, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, level, message, created_at),
        )
