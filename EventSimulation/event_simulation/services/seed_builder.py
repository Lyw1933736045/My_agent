"""Gate 2: build a deterministic, time-bounded and traceable seed."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import re
from typing import Any, Iterable

from ..policies.cutoff import parse_datetime
from ..models.case import CaseBundle, ScenarioConfig
from .evidence import TraceIndex
from .ontology_service import strip_report_links


_OFFICIAL_SUBJECTS = (
    "中国证监会", "证监会", "中国人民银行", "人民银行", "央行",
    "中金所", "中国金融期货交易所", "国务院", "监管部门", "吴清",
)
_OFFICIAL_SPEECH = (
    "表示", "指出", "宣布", "提出", "发布", "披露", "回应", "称",
    "将", "拟", "计划", "要求", "批准", "启动", "试点",
)
_KNOWN_ENTITIES = {
    "中国证监会": "regulator",
    "中国人民银行": "central_bank",
    "中国金融期货交易所": "exchange",
    "中金所": "exchange",
    "吴清": "person",
    "人民币": "currency",
    "人民币外汇期货": "financial_instrument",
}
_ENTITY_ALIASES = {
    "证监会": "中国证监会",
    "中国证监会主席": "中国证监会",
    "证监会主席": "中国证监会",
    "人民银行": "中国人民银行",
    "央行": "中国人民银行",
    "中金所": "中国金融期货交易所",
}
_MAX_FACTS = 25
_MAX_CLAIMS = 30


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _signature(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text).casefold()


def _identifier(prefix: str, text: str) -> str:
    digest = hashlib.sha256(_signature(text).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _source_ids(trace: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(
        str(item.get("source_id")) for item in trace if item.get("source_id")
    ))


def _merge_traces(left: list[dict], right: list[dict]) -> list[dict]:
    result = list(left)
    keys = {
        (item.get("brief_path"), item.get("brief_source_id"), item.get("source_id"))
        for item in result
    }
    for item in right:
        key = (item.get("brief_path"), item.get("brief_source_id"), item.get("source_id"))
        if key not in keys:
            result.append(item)
            keys.add(key)
    return result


def _text_similarity(left: str, right: str) -> float:
    """Character-bigram similarity works predictably for short Chinese facts."""
    left_value, right_value = _signature(left), _signature(right)
    if left_value == right_value:
        return 1.0
    shorter, longer = sorted((left_value, right_value), key=len)
    if len(shorter) >= 16 and shorter in longer:
        return len(shorter) / len(longer)
    if len(shorter) < 4:
        return 0.0
    left_pairs = {left_value[index:index + 2] for index in range(len(left_value) - 1)}
    right_pairs = {right_value[index:index + 2] for index in range(len(right_value) - 1)}
    return len(left_pairs & right_pairs) / max(1, len(left_pairs | right_pairs))


class SeedBuilder:
    """Pure in-memory transformer; it performs no I/O and invokes no model."""

    def __init__(self, scenario: ScenarioConfig) -> None:
        self.scenario = scenario
        self._excluded: list[dict[str, Any]] = []
        self._quarantine: list[dict[str, Any]] = []
        self._deduplicated: list[dict[str, Any]] = []
        self._not_selected: list[dict[str, Any]] = []
        self._accepted_undated_social: list[dict[str, Any]] = []
        self._trace_index: TraceIndex | None = None

    def build(self, bundle: CaseBundle) -> dict[str, Any]:
        self._excluded = []
        self._quarantine = []
        self._deduplicated = []
        self._not_selected = []
        self._accepted_undated_social = []
        trace_index = TraceIndex(bundle.source_catalog, bundle.accepted_documents)
        self._trace_index = trace_index
        facts = self._facts(bundle, trace_index)
        claims = self._claims(bundle, trace_index)
        timeline = self._timeline(bundle, trace_index)
        facts = self._deduplicate(facts, "fact")
        claims = self._deduplicate(claims, "claim")
        timeline = self._deduplicate(timeline, "timeline")
        facts = self._limit(facts, _MAX_FACTS, "fact")
        claims = self._limit(claims, _MAX_CLAIMS, "claim")
        entities = self._entities([*facts, *claims, *timeline])
        referenced = set()
        for item in [*facts, *claims, *timeline, *entities]:
            referenced.update(item.get("source_ids") or [])
        sources = [item for item in trace_index.catalog() if item.get("source_id") in referenced]
        # Derive the summary only from evidence that survived the cutoff gate.
        event_summary = "；".join(item["text"] for item in facts[:5])
        source_report = strip_report_links(str(bundle.case.get("report") or ""))
        return {
            "seed_version": "1.1",
            "case_id": bundle.case_id,
            "scenario": self.scenario.as_dict(),
            "event_summary": event_summary,
            "source_report": source_report,
            "facts": facts,
            "claims": claims,
            "entities": entities,
            "timeline": timeline,
            "sources": sources,
            "quality": {
                "excluded_after_cutoff": self._excluded,
                "quarantine": self._quarantine,
                "undated_quarantine": [
                    item for item in self._quarantine
                    if item.get("reason") in {
                        "source_date_missing",
                        "no_pre_cutoff_evidence",
                        "event_date_missing_or_ambiguous",
                    }
                ],
                "content_grounding_quarantine": [
                    item for item in self._quarantine
                    if item.get("reason") == "accepted_document_content_does_not_support_text"
                ],
                "deduplicated_items": self._deduplicated,
                "not_selected_due_to_limit": self._not_selected,
                "accepted_undated_social": self._accepted_undated_social,
                "loader_warnings": deepcopy(bundle.quality_warnings),
            },
        }

    def _eligible_trace(
        self,
        *,
        kind: str,
        text: str,
        path: str,
        trace: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        dated_before: list[dict] = []
        dated_after: list[dict] = []
        undated: list[dict] = []
        for item in trace:
            published = parse_datetime(item.get("published_at"), end_of_day=True)
            if published is None:
                undated.append(item)
            elif published <= self.scenario.as_of:
                dated_before.append(item)
            else:
                dated_after.append(item)
        if dated_after:
            self._excluded.append({
                "kind": kind,
                "text": text,
                "path": path,
                "reason": "source_published_after_cutoff",
                "trace": dated_after,
            })
        if dated_before:
            return dated_before
        if (
            self.scenario.allow_undated_social
            and kind == "claim"
            and undated
            and not dated_after
            and all(self._is_social_trace(item) for item in undated)
        ):
            self._accepted_undated_social.append({
                "kind": kind,
                "text": text,
                "path": path,
                "reason": "undated_social_temporal_gate_disabled",
                "trace": undated,
            })
            return undated
        reason = "source_date_missing" if undated else "no_pre_cutoff_evidence"
        self._quarantine.append({
            "kind": kind,
            "text": text,
            "path": path,
            "reason": reason,
            "trace": undated or trace,
        })
        return []

    @staticmethod
    def _is_social_trace(item: dict[str, Any]) -> bool:
        source_type = str(item.get("source_type") or "").lower()
        source_group = str(item.get("source_group") or "").lower()
        publisher = str(item.get("publisher") or item.get("source_name") or "").lower()
        url = str(item.get("canonical_url") or item.get("url") or "").lower()
        return (
            source_type in {"social", "social_media"}
            or source_group == "social_media"
            or "微博" in publisher
            or "weibo.com" in url
        )

    def _fact_item(
        self,
        *,
        text: str,
        path: str,
        trace: list[dict],
        assertion_type: str,
        event_time: object = None,
    ) -> dict[str, Any] | None:
        text = _clean_text(text)
        if not text:
            return None
        eligible = self._eligible_trace(kind="fact", text=text, path=path, trace=trace)
        if not eligible:
            return None
        grounded = self._trace_index.grounded(eligible, text) if self._trace_index else []
        if not grounded:
            self._quarantine.append({
                "kind": "fact", "text": text, "path": path,
                "reason": "accepted_document_content_does_not_support_text",
                "trace": eligible,
            })
            return None
        eligible = grounded
        parsed_event_time = parse_datetime(event_time, end_of_day=True)
        if parsed_event_time and parsed_event_time > self.scenario.as_of:
            self._excluded.append({
                "kind": "fact", "text": text, "path": path,
                "reason": "event_after_cutoff", "event_time": parsed_event_time.isoformat(),
                "trace": eligible,
            })
            return None
        ids = _source_ids(eligible)
        source_types = {str(item.get("source_type") or "") for item in eligible}
        evidence_level = (
            "primary" if "official" in source_types
            else "multi_source" if len(ids) > 1
            else "single_source"
        )
        return {
            "fact_id": _identifier("fact", text),
            "text": text,
            "event_time": parsed_event_time.isoformat() if parsed_event_time else None,
            "assertion_type": assertion_type,
            "evidence_level": evidence_level,
            "source_ids": ids,
            "origin": "real",
            "trace": eligible,
        }

    def _facts(self, bundle: CaseBundle, index: TraceIndex) -> list[dict[str, Any]]:
        brief = bundle.brief_data
        result: list[dict[str, Any]] = []
        # Executive summaries and topic summaries are synthesis. They may guide
        # selection, but are never promoted to atomic facts in strict mode.
        for position, item in enumerate(brief.get("key_metrics") or []):
            path = f"brief_data.key_metrics[{position}]"
            text = "：".join(filter(None, [
                _clean_text(item.get("label")), _clean_text(item.get("value")),
                _clean_text(item.get("context")),
            ]))
            fact = self._fact_item(
                text=text, path=path,
                trace=index.trace(path, item.get("source_ids") or []),
                assertion_type=self._assertion_type(text),
            )
            if fact:
                result.append(fact)
        for group_name, insights in (
            ("media_insights", bundle.prepared_analysis.get("media_insights") or []),
            ("social_insights", bundle.prepared_analysis.get("social_insights") or []),
        ):
            for insight_position, insight in enumerate(insights):
                # Social posts are claims only, never facts.
                if group_name == "social_insights":
                    continue
                for fact_position, text in enumerate(insight.get("reported_facts") or []):
                    path = f"prepared_analysis.{group_name}[{insight_position}].reported_facts[{fact_position}]"
                    fact = self._fact_item(
                        text=text, path=path,
                        trace=index.trace_for_insight(path, insight),
                        assertion_type=self._assertion_type(text),
                        event_time=insight.get("published_at"),
                    )
                    if fact:
                        result.append(fact)
        return result

    @staticmethod
    def _assertion_type(text: object) -> str:
        value = _clean_text(text)
        if any(subject in value for subject in _OFFICIAL_SUBJECTS) and any(
            verb in value for verb in _OFFICIAL_SPEECH
        ):
            return "official_fact"
        return "reported_fact"

    def _claim_item(
        self,
        *,
        text: object,
        path: str,
        trace: list[dict],
        speaker: object = "",
        organization: object = "",
        claim_type: str,
    ) -> dict[str, Any] | None:
        text = _clean_text(text)
        if not text:
            return None
        eligible = self._eligible_trace(kind="claim", text=text, path=path, trace=trace)
        if not eligible:
            return None
        grounded = self._trace_index.grounded(eligible, text) if self._trace_index else []
        if not grounded:
            self._quarantine.append({
                "kind": "claim", "text": text, "path": path,
                "reason": "accepted_document_content_does_not_support_text",
                "trace": eligible,
            })
            return None
        eligible = grounded
        return {
            "claim_id": _identifier("claim", "|".join([_clean_text(speaker), _clean_text(organization), text])),
            "speaker": _clean_text(speaker),
            "organization": _clean_text(organization),
            "text": text,
            "claim_type": claim_type,
            "source_ids": _source_ids(eligible),
            "origin": "real",
            "trace": eligible,
        }

    def _claims(self, bundle: CaseBundle, index: TraceIndex) -> list[dict[str, Any]]:
        result: list[dict] = []
        sections = [
            (bundle.brief_data.get("official") or {}, "brief_data.official", "official_statement"),
            ((bundle.brief_data.get("media") or {}).get("domestic") or {}, "brief_data.media.domestic", "expert_view"),
            ((bundle.brief_data.get("media") or {}).get("overseas") or {}, "brief_data.media.overseas", "media_interpretation"),
            (bundle.brief_data.get("public_opinion") or {}, "brief_data.public_opinion", "social_view"),
        ]
        for section, root, default_type in sections:
            for topic_position, topic in enumerate(section.get("topics") or []):
                for view_position, view in enumerate(topic.get("supporting_views") or []):
                    path = f"{root}.topics[{topic_position}].supporting_views[{view_position}]"
                    source_ids = [view.get("source_id")] if view.get("source_id") else topic.get("source_ids") or []
                    claim = self._claim_item(
                        text=view.get("point"), path=path, trace=index.trace(path, source_ids),
                        speaker=view.get("speaker"), organization=view.get("organization"),
                        claim_type="official_statement" if root.endswith("official") else default_type,
                    )
                    if claim:
                        result.append(claim)
                for view_position, view in enumerate(topic.get("social_views") or []):
                    path = f"{root}.topics[{topic_position}].social_views[{view_position}]"
                    source_ids = [view.get("source_id")] if view.get("source_id") else topic.get("source_ids") or []
                    claim = self._claim_item(
                        text=view.get("point"), path=path, trace=index.trace(path, source_ids),
                        speaker=view.get("account"), claim_type="social_view",
                    )
                    if claim:
                        result.append(claim)
        for group_name, insights in (
            ("media_insights", bundle.prepared_analysis.get("media_insights") or []),
            ("social_insights", bundle.prepared_analysis.get("social_insights") or []),
        ):
            for insight_position, insight in enumerate(insights):
                default_type = "social_view" if group_name == "social_insights" else "media_interpretation"
                for key in ("interpretations", "risks_or_disagreements"):
                    for position, text in enumerate(insight.get(key) or []):
                        path = f"prepared_analysis.{group_name}[{insight_position}].{key}[{position}]"
                        claim = self._claim_item(
                            text=text, path=path, trace=index.trace_for_insight(path, insight),
                            organization=insight.get("source_name"), claim_type=default_type,
                        )
                        if claim:
                            result.append(claim)
                for position, view in enumerate(insight.get("named_views") or []):
                    path = f"prepared_analysis.{group_name}[{insight_position}].named_views[{position}]"
                    speaker = view.get("speaker") or view.get("account")
                    organization = view.get("organization") or view.get("attribution")
                    text = view.get("view") or view.get("point")
                    claim_type = "official_statement" if self._assertion_type(
                        " ".join(map(_clean_text, (speaker, organization, text)))
                    ) == "official_fact" else default_type
                    claim = self._claim_item(
                        text=text, path=path, trace=index.trace_for_insight(path, insight),
                        speaker=speaker, organization=organization, claim_type=claim_type,
                    )
                    if claim:
                        result.append(claim)
        return result

    def _timeline(self, bundle: CaseBundle, index: TraceIndex) -> list[dict[str, Any]]:
        result = []
        for position, item in enumerate(bundle.brief_data.get("timeline") or []):
            path = f"brief_data.timeline[{position}]"
            text = _clean_text(item.get("event"))
            trace = index.trace(path, item.get("source_ids") or [])
            eligible = self._eligible_trace(kind="timeline", text=text, path=path, trace=trace)
            if not eligible:
                continue
            grounded = self._trace_index.grounded(eligible, text) if self._trace_index else []
            if not grounded:
                self._quarantine.append({
                    "kind": "timeline", "text": text, "path": path,
                    "reason": "accepted_document_content_does_not_support_text",
                    "trace": eligible,
                })
                continue
            eligible = grounded
            event_time = parse_datetime(item.get("date"), end_of_day=True)
            if event_time is None:
                self._quarantine.append({
                    "kind": "timeline", "text": text, "path": path,
                    "reason": "event_date_missing_or_ambiguous", "trace": eligible,
                })
                continue
            if event_time > self.scenario.as_of:
                self._excluded.append({
                    "kind": "timeline", "text": text, "path": path,
                    "reason": "event_after_cutoff", "event_time": event_time.isoformat(),
                    "trace": eligible,
                })
                continue
            result.append({
                "timeline_id": _identifier("timeline", f"{event_time.isoformat()}|{text}"),
                "event_time": event_time.isoformat(),
                "event": text,
                "source_ids": _source_ids(eligible),
                "origin": "real",
                "trace": eligible,
            })
        return result

    def _deduplicate(self, items: list[dict], kind: str) -> list[dict]:
        result: list[dict] = []
        text_key = "event" if kind == "timeline" else "text"
        for item in items:
            existing = next((
                candidate for candidate in result
                if _text_similarity(
                    _clean_text(candidate.get(text_key)),
                    _clean_text(item.get(text_key)),
                ) >= 0.82
            ), None)
            if existing is None:
                result.append(item)
                continue
            existing["trace"] = _merge_traces(existing.get("trace", []), item.get("trace", []))
            existing["source_ids"] = _source_ids(existing["trace"])
            self._deduplicated.append({
                "kind": kind,
                "kept_id": existing.get(f"{kind}_id"),
                "merged_id": item.get(f"{kind}_id"),
                "text": item.get(text_key),
                "reason": "normalized_or_high_similarity_match",
            })
        return result

    def _limit(self, items: list[dict], limit: int, kind: str) -> list[dict]:
        if len(items) <= limit:
            return items
        id_key = f"{kind}_id"
        for item in items[limit:]:
            self._not_selected.append({
                "kind": kind,
                "item_id": item.get(id_key),
                "text": item.get("text"),
                "reason": f"mvp_{kind}_limit_{limit}",
                "trace": item.get("trace") or [],
            })
        return items[:limit]

    def _entities(self, evidence: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for item in evidence:
            text = " ".join(map(_clean_text, (
                item.get("speaker"), item.get("organization"),
                item.get("text"), item.get("event"),
            )))
            candidates: dict[str, str] = {}
            speaker = self._clean_entity_name(item.get("speaker"))
            if speaker:
                candidates[speaker] = "person_or_account"
            organization = self._clean_entity_name(item.get("organization"))
            if organization:
                candidates[organization] = "organization"
            for name, entity_type in _KNOWN_ENTITIES.items():
                if name in text:
                    candidates[name] = entity_type
            for name, entity_type in candidates.items():
                if not name:
                    continue
                canonical = _ENTITY_ALIASES.get(name, name)
                current = found.setdefault(canonical, {
                    "entity_id": _identifier("entity", canonical),
                    "name": canonical,
                    "entity_type": entity_type,
                    "aliases": [],
                    "source_ids": [],
                    "trace": [],
                })
                if name != canonical and name not in current["aliases"]:
                    current["aliases"].append(name)
                current["trace"] = _merge_traces(current["trace"], item.get("trace", []))
                current["source_ids"] = _source_ids(current["trace"])
        return list(found.values())

    @staticmethod
    def _clean_entity_name(value: object) -> str:
        name = _clean_text(value).strip("，,。；;：:（）()[]【】")
        if not name or len(name) > 30:
            return ""
        if re.search(r"https?://|www\.|\.(?:com|cn|net|org)(?:\.|$)", name, re.I):
            return ""
        if any(mark in name for mark in ("，", ",", "。", "；", ";")):
            return ""
        return _ENTITY_ALIASES.get(name, name)
