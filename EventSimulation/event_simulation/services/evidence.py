"""Stable source identifiers and evidence traces."""

from __future__ import annotations

import hashlib
import re
from typing import Any


def stable_source_id(document_id: object, canonical_url: object, url: object) -> str:
    if str(document_id or "").strip():
        return f"doc:{str(document_id).strip()}"
    value = str(canonical_url or url or "").strip()
    if not value:
        return ""
    return "url:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


class TraceIndex:
    def __init__(self, source_catalog: list[dict], documents: list[dict]) -> None:
        self.brief_sources = {str(item.get("id")): item for item in source_catalog}
        self.brief_id_by_url = {
            str(item.get("url") or "").strip(): str(item.get("id"))
            for item in source_catalog
            if str(item.get("url") or "").strip()
        }
        self.documents_by_url: dict[str, dict] = {}
        self.documents_by_id: dict[str, dict] = {}
        for document in documents:
            document_id = str(document.get("document_id") or "").strip()
            if document_id:
                self.documents_by_id[document_id] = document
            for key in ("canonical_url", "url", "final_url"):
                value = str(document.get(key) or "").strip()
                if value:
                    self.documents_by_url[value] = document

    def source(self, brief_source_id: str) -> dict[str, Any]:
        brief = self.brief_sources.get(str(brief_source_id), {})
        url = str(brief.get("url") or "").strip()
        document = self.documents_by_url.get(url, {})
        source_id = stable_source_id(
            document.get("document_id"),
            document.get("canonical_url"),
            url,
        )
        return {
            "source_id": source_id,
            "brief_source_id": str(brief_source_id),
            "document_id": document.get("document_id"),
            "url": url or document.get("url"),
            "canonical_url": document.get("canonical_url") or url,
            "title": brief.get("title") or document.get("title"),
            "publisher": brief.get("source_name") or document.get("source"),
            "source_type": brief.get("source_type") or document.get("source_group"),
            "published_at": brief.get("published_at") or document.get("published_at"),
            "run_ids": list(document.get("case_run_ids") or ([document.get("run_id")] if document.get("run_id") else [])),
        }

    def trace(self, brief_path: str, source_ids: list[str]) -> list[dict[str, Any]]:
        traces = []
        for brief_source_id in dict.fromkeys(str(item) for item in source_ids if item):
            source = self.source(brief_source_id)
            traces.append({"brief_path": brief_path, **source})
        if not traces:
            traces.append({
                "brief_path": brief_path,
                "source_id": "",
                "brief_source_id": None,
                "document_id": None,
                "url": None,
                "canonical_url": None,
                "title": None,
                "publisher": None,
                "source_type": None,
                "published_at": None,
                "run_ids": [],
            })
        return traces

    def trace_for_insight(self, path: str, insight: dict) -> list[dict[str, Any]]:
        brief_ids = list(insight.get("source_ids") or [])
        if insight.get("source_id"):
            brief_ids.append(str(insight["source_id"]))
        url = str(insight.get("url") or "").strip()
        if url and self.brief_id_by_url.get(url):
            brief_ids.append(self.brief_id_by_url[url])
        return self.trace(path, brief_ids)

    def catalog(self) -> list[dict[str, Any]]:
        result = []
        seen: set[str] = set()
        for brief_id in self.brief_sources:
            item = self.source(brief_id)
            key = item["source_id"] or f"brief:{brief_id}"
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def grounded(self, traces: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
        """Keep only traces whose accepted document body supports the text."""
        result = []
        for trace in traces:
            document = self.documents_by_id.get(str(trace.get("document_id") or ""))
            if document is None:
                document = self.documents_by_url.get(str(trace.get("canonical_url") or ""))
            if document is None:
                document = self.documents_by_url.get(str(trace.get("url") or ""))
            content = str((document or {}).get("content") or "")
            score, numbers_verified = self._grounding_score(text, content)
            if score >= 0.80 and numbers_verified:
                result.append({
                    **trace,
                    "content_grounding": {
                        "method": "normalized_character_bigram_coverage",
                        "score": round(score, 4),
                        "numbers_verified": True,
                    },
                })
        return result

    @staticmethod
    def _grounding_score(candidate: str, content: str) -> tuple[float, bool]:
        normalize = lambda value: re.sub(r"[^\w\u4e00-\u9fff]+", "", value).casefold()
        candidate_text = normalize(candidate)
        content_text = normalize(content)
        if not candidate_text or not content_text:
            return 0.0, False
        candidate_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", candidate))
        content_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", content))
        numbers_verified = candidate_numbers <= content_numbers
        if candidate_text in content_text:
            return 1.0, numbers_verified
        pairs = {
            candidate_text[index:index + 2]
            for index in range(max(0, len(candidate_text) - 1))
        }
        if not pairs:
            return 0.0, numbers_verified
        content_pairs = {
            content_text[index:index + 2]
            for index in range(max(0, len(content_text) - 1))
        }
        return len(pairs & content_pairs) / len(pairs), numbers_verified
