"""Gate 4: build Persona candidates only from accepted real evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .ontology_service import is_speakable_actor_name


_ALIASES = {
    "证监会": "中国证监会",
    "证监会主席": "中国证监会",
    "中国证监会主席": "中国证监会",
    "央行": "中国人民银行",
    "人民银行": "中国人民银行",
    "中金所": "中国金融期货交易所",
}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical(value: object) -> str:
    name = _clean(value).strip("，,。；;：:（）()[]【】")
    if not name or len(name) > 60:
        return ""
    if re.search(r"https?://|www\.|\.(?:com|cn|net|org)(?:\.|$)", name, re.I):
        return ""
    if any(mark in name for mark in ("，", ",", "。", "；", ";")):
        return ""
    return _ALIASES.get(name, name)


def _persona_id(name: str) -> str:
    return "persona_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]


def _entity_mentioned(name: str, text: object) -> bool:
    value = _clean(text)
    names = {name, *(alias for alias, canonical in _ALIASES.items() if canonical == name)}
    return any(candidate and candidate in value for candidate in names)


def _role_type(name: str, entity_type: str) -> tuple[str, str]:
    if name == "中国证监会":
        return "regulator", "exact_entity_name"
    if name == "中国人民银行":
        return "central_bank", "exact_entity_name"
    if name == "中国金融期货交易所":
        return "exchange", "exact_entity_name"
    if "期货" in name and entity_type == "organization":
        return "futures_company", "explicit_entity_name_contains_期货"
    if entity_type == "person":
        return "named_person", "seed_entity_type"
    if entity_type == "person_or_account":
        return "named_speaker_or_account", "seed_entity_type"
    if entity_type in {"organization", "regulator", "central_bank", "exchange"}:
        return "named_organization", "seed_entity_type"
    return "evidence_subject", "seed_entity_type"


def _valid_actor_name(value: object) -> bool:
    return is_speakable_actor_name(value)


def _role_from_graph(entity_type: str, actor_kind: str) -> tuple[str, str]:
    lowered = entity_type.lower()
    if any(token in lowered for token in ("government", "regulator", "official", "agency")):
        return "regulator", "ontology_entity_type"
    if "bank" in lowered and "investment" not in lowered:
        return "central_bank", "ontology_entity_type"
    if "exchange" in lowered:
        return "exchange", "ontology_entity_type"
    if any(token in lowered for token in ("social", "netizen", "weibo", "account", "investor")):
        return "named_speaker_or_account", "ontology_entity_type"
    if any(token in lowered for token in ("media", "journal", "news")):
        return "media", "ontology_entity_type"
    if any(token in lowered for token in ("expert", "analyst")):
        return "named_person", "ontology_entity_type"
    if any(token in lowered for token in ("company", "enterprise", "firm", "bank")):
        return "named_organization", "ontology_entity_type"
    if actor_kind == "person":
        return "named_person", "ontology_actor_kind"
    return "named_organization", "ontology_actor_kind"


class PersonaBuilder:
    """Build one Persona per selected typed graph actor."""

    def build(
        self,
        seed: dict[str, Any],
        graph_manifest: dict[str, Any],
        graph: dict[str, Any],
        *,
        selected_actor_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if graph_manifest.get("status") != "complete" or not graph_manifest.get("zep_graph_id"):
            raise ValueError("Zep 真实图谱未就绪，已终止人设生成")
        if graph_manifest.get("case_id") != seed.get("case_id"):
            raise ValueError("seed and graph_manifest case_id differ")
        episodes = list(graph_manifest.get("episodes") or [])
        document_mode = any(item.get("item_type") == "document" for item in episodes)
        ingested_items = {
            item.get("item_id")
            for item in episodes
            if item.get("status") == "processed"
        }
        if document_mode:
            facts = {item["fact_id"]: item for item in seed.get("facts") or []}
            claims = list(seed.get("claims") or [])
        else:
            facts = {
                item["fact_id"]: item
                for item in seed.get("facts") or []
                if item.get("fact_id") in ingested_items
            }
            claims = [
                item for item in seed.get("claims") or []
                if item.get("claim_id") in ingested_items
            ]
        nodes = {str(item.get("id")): item for item in graph.get("nodes") or []}
        if selected_actor_ids is None:
            actors = [item for item in nodes.values() if item.get("is_actor")]
        else:
            allowed = {str(item) for item in selected_actor_ids}
            actors = [item for item in nodes.values() if str(item.get("id")) in allowed]
        actor_aliases: dict[str, set[str]] = {}
        alias_to_actor: dict[str, str] = {}
        rejected = []
        for actor in actors:
            actor_id = str(actor.get("id"))
            display_name = _clean(actor.get("display_name") or actor.get("name_zh") or actor.get("name"))
            if not _valid_actor_name(display_name):
                rejected.append({
                    "entity_id": actor_id,
                    "name": display_name,
                    "reason": "invalid_or_generic_actor_name",
                })
                continue
            aliases = {
                value for value in (
                    _canonical(actor.get("name")),
                    _canonical(actor.get("name_zh")),
                    _canonical(actor.get("display_name")),
                ) if value
            }
            actor_aliases[actor_id] = aliases
            for alias in aliases:
                alias_to_actor.setdefault(alias, actor_id)

        claims_by_actor: dict[str, list[dict[str, Any]]] = {}
        identity_evidence: dict[str, list[dict[str, str]]] = {}
        for claim in claims:
            matched = False
            for field in ("speaker", "organization"):
                raw_value = _clean(claim.get(field))
                subject = _canonical(raw_value)
                actor_id = alias_to_actor.get(subject)
                if actor_id:
                    claims_by_actor.setdefault(actor_id, []).append(claim)
                    identity_evidence.setdefault(actor_id, []).append({
                        "claim_id": claim["claim_id"],
                        "matched_field": field,
                        "evidence_value": raw_value,
                    })
                    matched = True
            if matched:
                continue
            blob = _clean(claim.get("text"))
            for alias, actor_id in alias_to_actor.items():
                if alias and alias in blob:
                    claims_by_actor.setdefault(actor_id, []).append(claim)
                    identity_evidence.setdefault(actor_id, []).append({
                        "claim_id": claim["claim_id"],
                        "matched_field": "text",
                        "evidence_value": alias,
                    })
                    break

        personas = []
        edges = graph.get("edges") or []
        for actor in actors:
            actor_id = str(actor.get("id"))
            aliases = actor_aliases.get(actor_id)
            if not aliases:
                continue
            subject = _clean(actor.get("display_name") or actor.get("name_zh") or actor.get("name"))
            subject_claims = list({item["claim_id"]: item for item in claims_by_actor.get(actor_id, [])}.values())
            entity_type = str(actor.get("entity_type") or "")
            actor_kind = str(actor.get("actor_kind") or "organization")
            claim_ids = [item["claim_id"] for item in subject_claims]
            claim_source_ids = list(dict.fromkeys(
                source_id
                for item in subject_claims
                for source_id in item.get("source_ids") or []
            ))
            allowed_facts = [
                fact for fact in facts.values()
                if any(_entity_mentioned(alias, fact.get("text")) for alias in aliases)
            ]
            role_type, role_derivation = _role_from_graph(entity_type, actor_kind)
            role_group = role_type
            relations = []
            relation_source_ids: list[str] = []
            for edge in edges:
                source = str(edge.get("source") or "")
                target = str(edge.get("target") or "")
                if actor_id not in {source, target}:
                    continue
                other_id = target if source == actor_id else source
                other = nodes.get(other_id) or {}
                source_ids = [str(value) for value in edge.get("source_ids") or []]
                relation_source_ids.extend(source_ids)
                relations.append({
                    "direction": "outgoing" if source == actor_id else "incoming",
                    "relation": edge.get("name_zh") or edge.get("name") or "相关",
                    "related_entity": other.get("display_name") or other.get("name_zh") or other.get("name") or other_id,
                    "fact": edge.get("summary_zh") or edge.get("summary") or "",
                    "source_ids": source_ids,
                })
            personas.append({
                "persona_id": _persona_id(subject),
                "display_name": subject,
                "role_type": role_type,
                "role_group": role_group,
                "role_derivation": role_derivation,
                "entity_refs": [actor_id],
                "graph_entity_uuid": actor_id,
                "entity_type": entity_type,
                "entity_type_zh": actor.get("entity_type_zh") or "可发声主体",
                "actor_kind": actor_kind,
                "summary": actor.get("summary_zh") or actor.get("summary") or "",
                "attributes": actor.get("attributes") or {},
                "relations": relations,
                "identity_evidence": identity_evidence.get(actor_id, []),
                "stance_refs": claim_ids,
                "allowed_fact_refs": [item["fact_id"] for item in allowed_facts],
                "evidence_source_ids": list(dict.fromkeys([
                    *claim_source_ids,
                    *relation_source_ids,
                    *(source_id for fact in allowed_facts for source_id in fact.get("source_ids") or []),
                ])),
                "grounded_utterance_units": [
                    *({
                        "kind": "claim",
                        "ref_id": item["claim_id"],
                        "text": item["text"],
                    } for item in subject_claims),
                    *({
                        "kind": "fact",
                        "ref_id": item["fact_id"],
                        "text": item["text"],
                    } for item in allowed_facts),
                ],
                "trace": self._merge_trace([*subject_claims, *allowed_facts]),
                "origin": "evidence_derived",
                "persona_kind": "entity",
                "status": "approved",
                "generation_policy": {
                    "allow_new_facts": False,
                    "allow_unreferenced_relationships": False,
                    "allow_unreferenced_stances": False,
                    "allow_paraphrase": False,
                    "require_ref_ids_on_every_utterance": True,
                },
            })
        return {
            "persona_version": "1.0",
            "case_id": seed.get("case_id"),
            "graph_group_id": graph_manifest.get("group_id"),
            "agent_count": len(personas),
            "count_policy": "all_typed_actors_cap_35",
            "personas": personas,
            "rejected_entities": rejected,
            "status": "approved",
        }

    @staticmethod
    def _merge_trace(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for item in items:
            for trace in item.get("trace") or []:
                key = (
                    trace.get("brief_path"), trace.get("brief_source_id"),
                    trace.get("source_id"), trace.get("document_id"),
                )
                if key not in seen:
                    result.append(trace)
                    seen.add(key)
        return result


def render_persona_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Persona Review: {payload.get('case_id')}", "",
        f"Evidence-derived Agent candidates: {payload.get('agent_count', 0)}", "",
        "| Persona | Role type | Claims | Facts | Sources | Status |",
        "|---|---|---:|---:|---:|---|",
    ]
    for persona in payload.get("personas") or []:
        lines.append(
            f"| {persona['display_name']} | {persona['role_type']} | "
            f"{len(persona['stance_refs'])} | {len(persona['allowed_fact_refs'])} | "
            f"{len(persona['evidence_source_ids'])} | {persona['status']} |"
        )
    lines.extend([
        "", "> 默认可发声主体全部上场；超过 35 个时按图谱连边和被点名次数去掉较次要的。", "",
    ])
    return "\n".join(lines)


def write_persona_artifacts(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "personas.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "personas.md").write_text(
        render_persona_markdown(payload), encoding="utf-8"
    )
    (output_dir / "personas_approved.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def approve_personas(
    payload: dict[str, Any],
    persona_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Approve selected evidence-derived candidates without changing content."""
    available = {str(item.get("persona_id")) for item in payload.get("personas") or []}
    selected = set(persona_ids or available)
    unknown = selected - available
    if unknown:
        raise ValueError(f"unknown persona_ids: {sorted(unknown)}")
    result = dict(payload)
    result["personas"] = [
        {**item, "status": "approved" if item.get("persona_id") in selected else "rejected"}
        for item in payload.get("personas") or []
    ]
    result["status"] = "approved"
    result["approval"] = {
        "approved_persona_ids": sorted(selected),
        "rejected_persona_ids": sorted(available - selected),
        "policy": "human_confirmed_evidence_derived_candidates",
    }
    return result
