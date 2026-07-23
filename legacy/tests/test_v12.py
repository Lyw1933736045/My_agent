import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from My_agent import cli
from My_agent.agent import PROJECT_ROOT, resolve_output_dir
from My_agent.legacy.official.discovery import StateCouncilDiscovery
from My_agent.state import EventFact, State
from My_agent.storage import Database
from My_agent.utils.official_sources import SourceVerification


LIST_HTML = """
<!doctype html>
<html><body>
  <div class="news_box"><div class="list"><ul>
    <li><h4>
      <a href="../202601/content_7056522.htm">国务院关于某事项的批复</a>
      <span class="date">2026-01-29</span>
    </h4></li>
    <li><h4>
      <a href="https://www.gov.cn/zhengce/content/202601/content_7056523.htm">
        国务院办公厅关于印发某方案的通知
      </a>
      <span class="date">2026-01-28</span>
    </h4></li>
    <li><a href="https://pbc.gov.cn/other">其他机构链接</a>2026-01-27</li>
  </ul></div></div>
</body></html>
"""


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "data" / "my_agent.db"
        self.database = Database(self.db_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _insert(self, suffix: str = "1") -> int:
        document_id, _ = self.database.upsert_document(
            source_id="state_council",
            title=f"政策文件 {suffix}",
            url=f"https://www.gov.cn/zhengce/content/{suffix}.htm",
            published_at="2026-01-29",
        )
        return document_id

    def test_database_and_tables_are_created(self):
        self.assertTrue(self.db_path.is_file())
        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertIn("documents", tables)
        self.assertIn("event_facts", tables)

    def test_duplicate_url_is_not_inserted(self):
        first_id, first_inserted = self.database.upsert_document(
            "state_council",
            "原始标题",
            "https://www.gov.cn/zhengce/content/same.htm",
            "2026-01-29",
        )
        second_id, second_inserted = self.database.upsert_document(
            "state_council",
            "不应覆盖的标题",
            "https://www.gov.cn/zhengce/content/same.htm",
            "2026-01-30",
        )
        records = self.database.list_documents()

        self.assertTrue(first_inserted)
        self.assertFalse(second_inserted)
        self.assertEqual(first_id, second_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "原始标题")
        self.assertEqual(records[0].published_at, "2026-01-29")

    def test_pending_query(self):
        pending_id = self._insert("pending")
        rejected_id = self._insert("rejected")
        self.database.update_status(rejected_id, "rejected")

        pending = self.database.list_pending()

        self.assertEqual([item.id for item in pending], [pending_id])

    def test_processed_and_failed_status_updates(self):
        processed_id = self._insert("processed")
        failed_id = self._insert("failed")
        self.database.update_status(processed_id, "processed")
        self.database.update_status(failed_id, "failed", "网页读取失败")

        self.assertEqual(
            self.database.get_document(processed_id).status, "processed"
        )
        failed = self.database.get_document(failed_id)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_message, "网页读取失败")

    def test_event_fact_is_saved_as_json(self):
        document_id = self._insert("fact")
        fact_id = self.database.save_event_fact(
            document_id,
            EventFact(
                title="政策标题",
                publisher="国务院",
                published_at="2026-01-29",
                document_number="国发〔2026〕1号",
                core_facts=["事实一", "事实二"],
            ),
        )

        rows = self.database.get_event_facts(document_id)

        self.assertGreater(fact_id, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["core_facts"]), ["事实一", "事实二"])


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "agent.db")
        self.discovery = StateCouncilDiscovery(self.database)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_parses_relative_urls_dates_and_limit(self):
        documents = self.discovery.parse(LIST_HTML, limit=1)

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].source_id, "state_council")
        self.assertEqual(documents[0].title, "国务院关于某事项的批复")
        self.assertEqual(documents[0].published_at, "2026-01-29")
        self.assertEqual(
            documents[0].url,
            "https://www.gov.cn/zhengce/zhengceku/202601/content_7056522.htm",
        )

    def test_discovery_writes_pending_candidates(self):
        with patch.object(self.discovery, "fetch_html", return_value=LIST_HTML):
            found, inserted = self.discovery.discover(limit=20)

        self.assertEqual(found, 2)
        self.assertEqual(inserted, 2)
        self.assertEqual(len(self.database.list_pending()), 2)

    def test_navigation_links_are_not_candidates(self):
        html = """
        <ul>
          <li><a href="https://www.gov.cn/">首页</a></li>
          <li><a href="https://mail.www.gov.cn/nsmail/index.html">邮箱</a></li>
          <li><a href="/zhengce/">政策</a></li>
        </ul>
        """
        self.assertEqual(self.discovery.parse(html), [])


class CliCompatibilityTests(unittest.TestCase):
    def test_relative_output_dir_is_anchored_inside_my_agent(self):
        self.assertEqual(resolve_output_dir("reports"), PROJECT_ROOT / "reports")

    def test_output_dir_outside_my_agent_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "My_agent"):
            resolve_output_dir(str(PROJECT_ROOT.parent / "outside-reports"))

    def test_cli_only_exposes_media_flows(self):
        self.assertEqual(cli.COMMANDS, {"media-search", "topic-brief"})

    def test_media_search_arguments(self):
        args = cli.parse_args(["media-search", "--query", "央行政策"])
        self.assertEqual(args.command, "media-search")
        self.assertEqual(args.query, "央行政策")


if __name__ == "__main__":
    unittest.main()
