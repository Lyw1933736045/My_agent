import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from My_agent import api
from My_agent.run_repository import RunRecord


class AdditionalSearchApiTests(unittest.TestCase):
    def _record(self):
        return RunRecord(
            run_id=uuid4().hex,
            query="虚构事件",
            topic="虚构事件",
            tavily_queries=["虚构搜索词"],
            approved_tavily_queries=["虚构搜索词"],
            status="completed",
            progress="研究完成",
            report="# 原报告",
            error=None,
            source_results=[],
            retrieval_reflection={},
            enabled_sources=["tavily"],
            newsnow_rss_core=["虚构核心事项"],
            newsnow_rss_support=["虚构背景"],
        )

    def test_rerun_changes_only_tavily_query(self):
        record = self._record()
        repository = MagicMock()
        repository.get.return_value = record
        repository.begin_additional_search.return_value = True
        executor = MagicMock()
        with patch.object(api, "_repository", repository), patch.object(api, "_executor", executor):
            response = api.rerun_with_tavily_queries(
                record.run_id,
                api.RerunRequest(tavily_queries=["虚构搜索词二"]),
            )
        self.assertEqual(response.run_id, record.run_id)
        repository.begin_additional_search.assert_called_once_with(
            record.run_id, ["虚构搜索词二"]
        )

    def test_rerun_rejects_multiple_tavily_queries(self):
        record = self._record()
        with patch.object(api, "_repository") as repository:
            repository.get.return_value = record
            with self.assertRaises(HTTPException) as raised:
                api.rerun_with_tavily_queries(
                    record.run_id,
                    api.RerunRequest(tavily_queries=["虚构一", "虚构二"]),
                )
        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
