import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from My_agent import api
from My_agent.run_repository import RunRepository
from My_agent.state import RunState


class _ImmediateExecutor:
    def submit(self, function, *args):
        function(*args)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.repository = RunRepository(
            Path(self.temporary_directory.name) / "my_agent.db"
        )
        self.repository_patch = patch.object(api, "_repository", self.repository)
        self.repository_patch.start()

    def tearDown(self):
        self.repository_patch.stop()
        self.temporary_directory.cleanup()

    @patch("My_agent.api._new_agent")
    def test_plan_approve_and_report_flow(self, new_agent):
        planner = MagicMock()
        planner.create_plan.return_value = RunState(
            query="央行降准",
            topic="央行降准影响",
            media_queries=["央行 降准"],
            provider_queries={"weibo": "央行 降准"},
        )
        runner = MagicMock()

        def discover(state, limit=None):
            self.assertEqual(state.provider_queries, {"weibo": "央行 降准"})
            return state

        def finish(state):
            state.brief = "# 研究报告"
            return state

        runner.discover_from_plan.side_effect = discover
        runner.complete.side_effect = finish
        new_agent.side_effect = [planner, runner]

        with patch.object(api, "_executor", _ImmediateExecutor()), patch(
            "My_agent.api.FinancialMediaAgent.save_brief"
        ) as save_brief:
            save_brief.return_value = Path("/tmp/topic_brief.md")
            plan = api.create_plan(api.CreatePlanRequest(query="央行降准"))
            approved = api.approve_plan(
                plan.run_id,
                api.ApprovePlanRequest(
                    approved_queries=["  央行   降准  ", "债券 市场"]
                ),
            )
            save_brief.assert_called_once_with("# 研究报告")

        self.assertEqual(approved.status, "completed")
        self.assertEqual(approved.approved_queries, ["央行 降准", "债券 市场"])
        report = api.get_report(plan.run_id)
        self.assertEqual(report["report"], "# 研究报告")
        markdown = api.download_report_markdown(plan.run_id)
        self.assertIn("attachment", markdown.headers["content-disposition"])
        self.assertIn("# 研究报告", markdown.body.decode("utf-8"))
        pdf_page = api.export_report_pdf(plan.run_id)
        self.assertIn("window.print()", pdf_page.body.decode("utf-8"))

    @patch("My_agent.api._new_agent")
    def test_failed_run_keeps_source_diagnostics(self, new_agent):
        planner = MagicMock()
        planner.create_plan.return_value = RunState(
            query="央行降准",
            topic="央行降准影响",
            media_queries=["央行 降准"],
        )

        class _Agent:
            def discover_from_plan(self, state, limit=None):
                from My_agent.tools.media_models import (
                    DiscoveryResult,
                    SourceFetchResult,
                )

                state.discovery = DiscoveryResult(
                    candidates=[],
                    stats={},
                    sources=(
                        SourceFetchResult("rss", "财联社", True, "3 条"),
                        SourceFetchResult("rss", "失效源", False, "请求超时"),
                    ),
                )
                return state

            def complete(self, state):
                raise RuntimeError("没有成功读取的正文")

        new_agent.side_effect = [planner, _Agent()]
        with patch.object(api, "_executor", _ImmediateExecutor()):
            plan = api.create_plan(api.CreatePlanRequest(query="央行降准"))
            result = api.approve_plan(
                plan.run_id,
                api.ApprovePlanRequest(approved_queries=["央行 降准"]),
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.source_summary["success"], 1)
        self.assertEqual(result.source_summary["failed"], 1)
        self.assertEqual(result.sources[0].name, "财联社")
        self.assertFalse(result.sources[1].ok)

    @patch("My_agent.api._new_agent")
    def test_plan_cannot_be_approved_twice(self, new_agent):
        planner = MagicMock()
        planner.create_plan.return_value = RunState(
            query="央行降准",
            topic="央行降准影响",
            media_queries=["央行 降准"],
        )
        runner = MagicMock()
        runner.discover_from_plan.side_effect = RuntimeError("测试停止")
        new_agent.side_effect = [planner, runner]

        with patch.object(api, "_executor", _ImmediateExecutor()):
            plan = api.create_plan(api.CreatePlanRequest(query="央行降准"))
            api.approve_plan(
                plan.run_id,
                api.ApprovePlanRequest(approved_queries=["央行 降准"]),
            )
            with self.assertRaises(HTTPException) as raised:
                api.approve_plan(
                    plan.run_id,
                    api.ApprovePlanRequest(approved_queries=["央行 降准"]),
                )

        self.assertEqual(raised.exception.status_code, 409)

    def test_repository_survives_new_instance(self):
        self.repository.create(
            run_id="persisted-run",
            query="央行降准",
            topic="降准影响",
            proposed_queries=["央行 降准"],
        )

        reopened = RunRepository(self.repository.database_path)
        record = reopened.get("persisted-run")

        self.assertIsNotNone(record)
        self.assertEqual(record.status, "waiting_for_review")
        self.assertEqual(record.proposed_queries, ["央行 降准"])

    def test_html_report_view_is_rendered_safely(self):
        self.repository.create(
            run_id="html-report",
            query="央行降准",
            topic="降准影响",
            proposed_queries=["央行 降准"],
        )
        self.repository.complete(
            "html-report",
            "# 简报\n\n[来源](https://example.com)\n\n<script>alert(1)</script>",
        )

        response = api.view_report("html-report")
        body = response.body.decode("utf-8")

        self.assertIn("<h1>简报</h1>", body)
        self.assertIn('rel="noopener noreferrer"', body)
        self.assertNotIn("<script>alert(1)</script>", body)


if __name__ == "__main__":
    unittest.main()
