import json
import tempfile
import unittest
from pathlib import Path

import yaml

from My_agent.agent import FinancialResearchAgent
from My_agent.state import SourceDocument, State
from My_agent.utils.official_sources import (
    OfficialSourcesConfigError,
    OfficialSourcesRegistry,
    SourceVerification,
    UnsafeOfficialUrlError,
)


class OfficialSourcesRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = OfficialSourcesRegistry()

    def test_recognizes_pbc_domain(self):
        result = self.registry.match_url(
            "https://pbc.gov.cn/tiaofasi/144941/144957/document.html"
        )
        self.assertEqual(result.source_id, "pbc")
        self.assertEqual(result.source_name, "中国人民银行")
        self.assertTrue(result.domain_verified)
        self.assertTrue(result.path_verified)
        self.assertEqual(result.verification_status, "verified")
        self.assertIn("匹配", result.verification_message)

    def test_recognizes_pbc_subdomain(self):
        result = self.registry.match_url(
            "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/document.html"
        )
        self.assertEqual(result.source_id, "pbc")
        self.assertEqual(result.verification_status, "verified")

    def test_does_not_match_domain_suffix_attack(self):
        result = self.registry.match_url(
            "https://pbc.gov.cn.example.com/tiaofasi/144941/144957/document.html"
        )
        self.assertIsNone(result.source_id)
        self.assertFalse(result.domain_verified)
        self.assertEqual(result.verification_status, "unverified")

    def test_unknown_https_domain_is_unverified(self):
        result = self.registry.match_url("https://news.example.com/document")
        self.assertEqual(result.verification_status, "unverified")
        self.assertIn("未识别为预定义官方来源", result.verification_message)

    def test_known_domain_outside_allowed_path_is_unverified(self):
        result = self.registry.match_url("https://www.pbc.gov.cn/other/document.html")
        self.assertTrue(result.domain_verified)
        self.assertFalse(result.path_verified)
        self.assertEqual(result.verification_status, "unverified")
        self.assertIn("不在 allowed_path_prefixes", result.verification_message)

    def test_rejects_non_https_url(self):
        with self.assertRaisesRegex(UnsafeOfficialUrlError, "HTTPS"):
            self.registry.match_url(
                "http://www.pbc.gov.cn/tiaofasi/144941/144957/document.html"
            )

    def test_global_filter_helpers_are_advisory(self):
        self.assertTrue(self.registry.is_excluded_title("某部门人事任免公告"))
        self.assertFalse(self.registry.is_excluded_title("货币政策执行报告"))
        self.assertTrue(self.registry.has_accepted_document_keyword("关于某事项的通知"))
        self.assertFalse(self.registry.has_accepted_document_keyword("季度执行报告"))

    def test_get_source(self):
        source = self.registry.get_source("pbc")
        self.assertEqual(source.source_type, "central_bank")
        self.assertEqual(source.priority, 1)
        self.assertEqual(source.trust_level, "primary")

    def test_missing_required_yaml_field_has_clear_error(self):
        invalid = {
            "version": 1,
            "defaults": {
                "enabled": True,
                "trust_level": "primary",
                "require_https": True,
            },
            "sources": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "invalid.yaml"
            config_path.write_text(
                yaml.safe_dump(invalid, allow_unicode=True),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                OfficialSourcesConfigError, "global_filters"
            ):
                OfficialSourcesRegistry(config_path)

    def test_missing_source_field_has_clear_error(self):
        invalid = {
            "version": 1,
            "defaults": {
                "enabled": True,
                "trust_level": "primary",
                "require_https": True,
            },
            "sources": [
                {
                    "source_id": "missing_name",
                    "organization": "测试机构",
                    "source_type": "regulator",
                    "priority": 1,
                    "allowed_domain_suffixes": ["example.gov.cn"],
                }
            ],
            "global_filters": {
                "exclude_title_keywords": [],
                "accepted_document_keywords": [],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "invalid.yaml"
            config_path.write_text(
                yaml.safe_dump(invalid, allow_unicode=True),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(OfficialSourcesConfigError, "name"):
                OfficialSourcesRegistry(config_path)


class SourceVerificationStateTests(unittest.TestCase):
    def test_state_json_saves_all_verification_fields(self):
        document = SourceDocument(
            official_url="https://www.pbc.gov.cn/start",
            requested_url="https://www.pbc.gov.cn/start",
            final_url="https://www.pbc.gov.cn/final",
            redirected=True,
            fetched_at="2026-07-20T10:00:00+08:00",
            content_type="text/html",
            content="官方正文",
            source_id="pbc",
            source_name="中国人民银行",
            source_type="central_bank",
            trust_level="primary",
            source_priority=1,
            domain_verified=True,
            path_verified=True,
            verification_status="verified",
            verification_message="请求及最终 URL 均匹配中国人民银行",
        )
        state = State(query=document.requested_url, source_document=document)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state.save_to_file(path)
            payload = json.loads(path.read_text(encoding="utf-8"))["source_document"]

        self.assertEqual(payload["requested_url"], document.requested_url)
        self.assertEqual(payload["final_url"], document.final_url)
        self.assertTrue(payload["redirected"])
        self.assertEqual(payload["source_id"], "pbc")
        self.assertEqual(payload["source_name"], "中国人民银行")
        self.assertEqual(payload["source_type"], "central_bank")
        self.assertEqual(payload["trust_level"], "primary")
        self.assertEqual(payload["source_priority"], 1)
        self.assertTrue(payload["domain_verified"])
        self.assertTrue(payload["path_verified"])
        self.assertEqual(payload["verification_status"], "verified")
        self.assertIn("匹配", payload["verification_message"])

    def test_redirect_to_different_domain_cannot_remain_verified(self):
        requested = SourceVerification(
            url="https://www.pbc.gov.cn/tiaofasi/144941/144957/a.html",
            source_id="pbc",
            source_name="中国人民银行",
            source_type="central_bank",
            trust_level="primary",
            source_priority=1,
            domain_verified=True,
            path_verified=True,
            verification_status="verified",
            verification_message="请求 URL 匹配",
        )
        final = SourceVerification(
            url="https://cache.example.com/a.html",
            verification_status="unverified",
            verification_message="最终域名未知",
        )
        result = FinancialResearchAgent._combine_source_verification(
            requested, final, redirected=True
        )
        self.assertEqual(result.verification_status, "unverified")
        self.assertFalse(result.domain_verified)
        self.assertIn("重定向", result.verification_message)


if __name__ == "__main__":
    unittest.main()
