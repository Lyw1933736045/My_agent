"""Simulation result analysis and human-readable summary rendering."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from typing import Any


POSITIVE_TERMS = ("支持", "积极", "利好", "期待", "完善", "提升", "推进", "便利", "认同")
NEGATIVE_TERMS = ("风险", "担忧", "警惕", "压力", "困难", "冲击", "质疑", "波动")


def _terms(text: object) -> set[str]:
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "")).casefold()
    if len(value) < 2:
        return set()
    return {value[index:index + 2] for index in range(len(value) - 1)}


def _sentiment(text: object) -> dict[str, Any]:
    value = str(text or "")
    positive = sum(value.count(term) for term in POSITIVE_TERMS)
    negative = sum(value.count(term) for term in NEGATIVE_TERMS)
    return {
        "label": "mixed" if positive and negative else "positive" if positive else "negative" if negative else "neutral",
        "positive_hits": positive,
        "negative_hits": negative,
        "method": "keyword_heuristic",
    }


class ResultAnalyzer:
    def analyze(self, run: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any]:
        evidence = {
            item[key]: item
            for key, collection in (
                ("fact_id", seed.get("facts") or []),
                ("claim_id", seed.get("claims") or []),
            )
            for item in collection
        }
        action_rows = [
            action
            for round_data in run.get("rounds") or []
            for action in round_data.get("actions") or []
            if action.get("validated", True)
        ]
        ref_counts = Counter(
            ref
            for action in action_rows
            for ref in (action.get("evidence_refs") or action.get("basis_real_refs") or [])
        )
        agent_counts = Counter(action.get("agent_id") for action in action_rows)
        round_refs = defaultdict(list)
        for round_data in run.get("rounds") or []:
            round_refs[round_data.get("round")].extend(
                ref
                for action in round_data.get("actions") or []
                for ref in (action.get("evidence_refs") or action.get("basis_real_refs") or [])
            )
        topic_rows = []
        for ref, count in ref_counts.most_common():
            item = evidence.get(ref)
            if not item:
                continue
            topic_rows.append({
                "evidence_ref": ref,
                "text": item.get("text"),
                "reference_count": count,
                "source_ids": item.get("source_ids") or [],
                "origin": "Simulation Result",
            })
        simulated_content = [
            {
                "action_id": action.get("action_id"),
                "agent_id": action.get("agent_id"),
                "role_group": action.get("role_group"),
                "text": action.get("content") or action.get("action_args", {}).get("content", ""),
                "sentiment": _sentiment(action.get("content") or action.get("action_args", {}).get("content", "")),
                "origin": "Simulation Result",
            }
            for action in action_rows
            if action.get("content") or action.get("action_args", {}).get("content")
        ]
        netizen_rows = [
            row for row in simulated_content
            if any(token in str(row.get("role_group") or "").lower()
                   for token in ("netizen", "investor", "网民", "投资者"))
        ]
        sentiment_counts = Counter(row["sentiment"]["label"] for row in netizen_rows)
        return {
            "result_version": "1.1",
            "case_id": run.get("case_id"),
            "simulation_id": run.get("simulation_id"),
            "origin": "Simulation Result",
            "major_evidence_topics": topic_rows,
            "simulated_content": simulated_content,
            "netizen_sentiment": {
                "available": bool(netizen_rows),
                "sample_count": len(netizen_rows),
                "distribution": dict(sentiment_counts),
                "items": netizen_rows,
                "caveat": "Simulation-only heuristic; unavailable when no evidence-derived netizen Agent participated.",
            },
            "agent_activity": [
                {"agent_id": agent_id, "validated_action_count": count, "origin": "Simulation Result"}
                for agent_id, count in agent_counts.most_common()
            ],
            "round_evidence_changes": [
                {
                    "round": round_number,
                    "evidence_refs": list(dict.fromkeys(refs)),
                    "origin": "Simulation Result",
                }
                for round_number, refs in sorted(round_refs.items())
            ],
            "propagation_paths": self._propagation_paths(action_rows),
            "unsupported_claims": [],
            "trace": {
                "all_result_topics_reference_seed_items": all(ref in evidence for ref in ref_counts),
                "new_facts_generated": False,
                "new_relationships_generated": False,
                "simulation_content_is_not_real_fact": True,
            },
        }

    @staticmethod
    def _propagation_paths(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        paths = []
        for action in actions:
            parent = (
                action.get("reply_to")
                or action.get("quote_of")
                or action.get("repost_of")
                or next(iter(action.get("parent_action_ids") or []), None)
            )
            if parent:
                paths.append({
                    "from_action_id": parent,
                    "to_action_id": action.get("action_id"),
                    "agent_id": action.get("agent_id"),
                    "evidence_refs": action.get("evidence_refs") or [],
                    "origin": "Simulation Result",
                })
        return paths


class HoldoutEvaluator:
    def evaluate(
        self,
        *,
        result: dict[str, Any],
        holdout_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        simulated = [item for item in result.get("major_evidence_topics") or []]
        rows = []
        matched = 0
        for holdout in holdout_items:
            holdout_terms = _terms(holdout.get("text") or holdout.get("event") or holdout.get("summary"))
            best = None
            for topic in simulated:
                overlap = len(holdout_terms & _terms(topic.get("text"))) / max(1, len(holdout_terms))
                if best is None or overlap > best["overlap"]:
                    best = {"evidence_ref": topic.get("evidence_ref"), "overlap": round(overlap, 4)}
            is_match = bool(best and best["overlap"] >= 0.25)
            matched += is_match
            rows.append({
                "holdout_id": holdout.get("id") or holdout.get("document_id") or holdout.get("url"),
                "matched_simulation_topic": best,
                "matched": is_match,
                "origin": "Holdout Evaluation",
            })
        return {
            "evaluation_version": "1.0",
            "origin": "Holdout Evaluation",
            "holdout_count": len(holdout_items),
            "matched_count": matched,
            "match_rate": round(matched / len(holdout_items), 4) if holdout_items else None,
            "rows": rows,
            "caveat": "This compares topic overlap only; it is not a real-world prediction score.",
        }


def write_result_artifacts(
    *,
    result: dict[str, Any],
    evaluation: dict[str, Any] | None,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "simulation_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if evaluation is not None:
        (output_dir / "evaluation.json").write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    lines = [
        f"# Simulation Result: {result.get('case_id')}", "",
        "All items below are Simulation Result, not real-world facts.", "",
        "## Executive summary", "",
        "This document summarizes simulated event trajectory and agent views; it is not a real-world prediction.", "",
        "## Simulated trajectory", "",
    ]
    for item in result.get("round_evidence_changes") or []:
        refs = ", ".join(item.get("evidence_refs") or []) or "none"
        lines.append(f"- Round {item.get('round')}: {refs}")
    lines.extend(["", "## Major evidence topics", ""])
    for item in result.get("major_evidence_topics") or []:
        lines.append(f"- `{item['evidence_ref']}` ({item['reference_count']} refs): {item['text']}")
    lines.extend(["", "## Agent activity", ""])
    for item in result.get("agent_activity") or []:
        lines.append(f"- {item['agent_id']}: {item['validated_action_count']} validated actions")
    sentiment = result.get("netizen_sentiment") or {}
    lines.extend(["", "## Simulated netizen sentiment", ""])
    if sentiment.get("available"):
        lines.append(f"- Samples: {sentiment.get('sample_count', 0)}")
        lines.append(f"- Distribution: {sentiment.get('distribution') or {}}")
        lines.append("- Method: keyword heuristic over simulated content; inspect the trace before interpretation.")
    else:
        lines.append("- Not available: no evidence-derived netizen Agent participated in this run.")
    (output_dir / "simulation_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
