"""Read-only graph views for the web application.

The evidence graph deliberately models only fields and literal mentions present
in Seed. It does not infer social relationships. Simulation interactions are
kept in a separate graph and labelled with ``origin=simulation``.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable


ROLE_LABELS = {
    "regulator": "监管机构",
    "futures_company": "期货机构",
    "netizen_or_account": "公众账号",
    "named_person": "具名人物",
    "named_organization": "具名机构",
}
ROLE_COLORS = {
    "regulator": "#527991",
    "futures_company": "#d98b45",
    "netizen_or_account": "#759b88",
    "named_person": "#9a789e",
    "named_organization": "#b7a45b",
}


def _edge_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    return "edge_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _source_ids(item: dict[str, Any]) -> list[str]:
    return [str(value) for value in item.get("source_ids") or []]


def _contains_alias(text: str, aliases: Iterable[str]) -> bool:
    return any(alias and alias in text for alias in aliases)


def build_evidence_graph(seed: dict[str, Any]) -> dict[str, Any]:
    """Build an audit-friendly graph from Seed without adding inferred facts."""
    entities = seed.get("entities") or []
    claims = seed.get("claims") or []
    facts = seed.get("facts") or []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    entity_aliases: dict[str, list[str]] = {}
    exact_entity: dict[str, str] = {}
    for entity in entities:
        node_id = str(entity["entity_id"])
        name = str(entity.get("name") or node_id)
        aliases = [name, *[str(value) for value in entity.get("aliases") or []]]
        entity_aliases[node_id] = aliases
        for alias in aliases:
            exact_entity.setdefault(alias, node_id)
        nodes.append({
            "id": node_id,
            "name": name,
            "node_type": "entity",
            "entity_type": entity.get("entity_type") or "unknown",
            "summary": name,
            "source_ids": _source_ids(entity),
            "origin": "real",
            "trace": entity.get("trace") or [],
        })

    def add_evidence_node(item: dict[str, Any], kind: str) -> None:
        node_id = str(item[f"{kind}_id"])
        content = str(item.get("text") or node_id)
        nodes.append({
            "id": node_id,
            "name": content[:72] + ("…" if len(content) > 72 else ""),
            "node_type": kind,
            "summary": content,
            "claim_type": item.get("claim_type") if kind == "claim" else None,
            "assertion_type": item.get("assertion_type") if kind == "fact" else None,
            "event_time": item.get("event_time"),
            "source_ids": _source_ids(item),
            "origin": "real",
            "trace": item.get("trace") or [],
        })

        linked: set[tuple[str, str]] = set()
        if kind == "claim":
            for field, relation in (("speaker", "发表"), ("organization", "组织相关言论")):
                value = str(item.get(field) or "").strip()
                entity_id = exact_entity.get(value)
                if entity_id:
                    linked.add((entity_id, relation))
        for entity_id, aliases in entity_aliases.items():
            if _contains_alias(content, aliases):
                linked.add((entity_id, "言论提及" if kind == "claim" else "事实提及"))
        for entity_id, relation in sorted(linked):
            edges.append({
                "id": _edge_id(entity_id, node_id, relation),
                "source": entity_id,
                "target": node_id,
                "name": relation,
                "origin": "real",
                "evidence_id": node_id,
                "source_ids": _source_ids(item),
            })

    for claim in claims:
        add_evidence_node(claim, "claim")
    for fact in facts:
        add_evidence_node(fact, "fact")

    node_counts = Counter(str(node["node_type"]) for node in nodes)
    return {
        "graph_version": "1.0",
        "case_id": seed.get("case_id"),
        "mode": "evidence",
        "origin": "real",
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "node_types": dict(node_counts),
            "sources": len(seed.get("sources") or []),
        },
        "notice": "边仅来自 Seed 的明确字段或文本字面提及，不表示额外推断的社会关系。",
    }


def _read_post_targets(database_path: Path) -> tuple[dict[int, int], dict[int, int]]:
    if not database_path.exists():
        return {}, {}
    connection = sqlite3.connect(database_path)
    try:
        post_owner = {
            int(post_id): int(user_id)
            for post_id, user_id in connection.execute("SELECT post_id, user_id FROM post")
        }
        comment_post = {
            int(comment_id): int(post_id)
            for comment_id, post_id in connection.execute("SELECT comment_id, post_id FROM comment")
        }
        return post_owner, comment_post
    finally:
        connection.close()


def _target_agent(
    action: dict[str, Any], post_owner: dict[int, int], comment_post: dict[int, int]
) -> int | None:
    args = action.get("action_args") or {}
    action_type = str(action.get("action_type") or "")
    if action_type == "CREATE_POST":
        return None
    if action_type == "FOLLOW":
        for key in ("followee_id", "target_user_id", "target_id"):
            if args.get(key) is not None:
                return int(args[key])
    post_id = (
        args.get("post_id")
        or args.get("quoted_id")
        or args.get("reposted_id")
        or args.get("original_post_id")
    )
    if post_id is None and args.get("comment_id") is not None:
        post_id = comment_post.get(int(args["comment_id"]))
    if post_id is not None:
        return post_owner.get(int(post_id))
    return None


def build_interaction_graph(
    run: dict[str, Any],
    personas: dict[str, Any],
    database_path: Path,
    *,
    up_to_round: int | None = None,
) -> dict[str, Any]:
    """Aggregate recorded OASIS actions into a directed agent network."""
    approved = personas.get("personas") or []
    persona_by_id = {str(item.get("persona_id")): item for item in approved}
    post_owner, comment_post = _read_post_targets(database_path)
    activity: Counter[int] = Counter()
    agent_meta: dict[int, dict[str, Any]] = {}
    grouped: dict[tuple[int, int, str], dict[str, Any]] = {}
    included_actions = 0

    for round_item in run.get("rounds") or []:
        round_number = int(round_item.get("round", 0))
        if up_to_round is not None and round_number > up_to_round:
            continue
        for action in round_item.get("actions") or []:
            source = int(action.get("agent_id", -1))
            if source < 0:
                continue
            included_actions += 1
            activity[source] += 1
            persona = persona_by_id.get(str(action.get("persona_id"))) or {}
            role = str(action.get("role_group") or persona.get("role_group") or "unknown")
            agent_meta[source] = {
                "id": f"agent_{source}",
                "agent_id": source,
                "name": action.get("agent_name") or persona.get("display_name") or f"Agent {source}",
                "node_type": "agent",
                "role_group": ROLE_LABELS.get(role, "其他角色"),
                "group_type": role,
                "color": ROLE_COLORS.get(role, "#8a938f"),
                "persona_id": action.get("persona_id") or persona.get("persona_id"),
                "origin": "simulation",
            }
            target = _target_agent(action, post_owner, comment_post)
            if target is None:
                continue
            action_type = str(action.get("action_type") or "UNKNOWN")
            key = (source, target, action_type)
            aggregate = grouped.setdefault(key, {"rounds": set(), "count": 0, "samples": []})
            aggregate["rounds"].add(round_number)
            aggregate["count"] += 1
            content = str(action.get("content") or "").strip()
            if content and content not in aggregate["samples"] and len(aggregate["samples"]) < 3:
                aggregate["samples"].append(content)

    for agent_id, persona in enumerate(approved):
        role = str(persona.get("role_group") or persona.get("role_type") or "unknown")
        agent_meta.setdefault(agent_id, {
            "id": f"agent_{agent_id}",
            "agent_id": agent_id,
            "name": persona.get("display_name") or f"Agent {agent_id}",
            "node_type": "agent",
            "role_group": ROLE_LABELS.get(role, "其他角色"),
            "group_type": role,
            "color": ROLE_COLORS.get(role, "#8a938f"),
            "persona_id": persona.get("persona_id"),
            "origin": "simulation",
        })

    edges = []
    for (source, target, action_type), aggregate in sorted(grouped.items()):
        edges.append({
            "id": _edge_id(source, target, action_type),
            "source": f"agent_{source}",
            "target": f"agent_{target}",
            "name": action_type,
            "action_type": action_type,
            "count": aggregate["count"],
            "rounds": sorted(aggregate["rounds"]),
            "samples": aggregate["samples"],
            "origin": "simulation",
        })
    incoming: Counter[int] = Counter()
    outgoing: Counter[int] = Counter()
    for edge in edges:
        source = int(str(edge["source"]).removeprefix("agent_"))
        target = int(str(edge["target"]).removeprefix("agent_"))
        outgoing[source] += int(edge.get("count") or 0)
        incoming[target] += int(edge.get("count") or 0)
    nodes = [
        {
            **node,
            "activity_count": activity[agent_id],
            "incoming_count": incoming[agent_id],
            "outgoing_count": outgoing[agent_id],
            "influence_count": activity[agent_id] + incoming[agent_id],
        }
        for agent_id, node in sorted(agent_meta.items())
    ]
    return {
        "graph_version": "1.0",
        "case_id": run.get("case_id"),
        "simulation_id": run.get("simulation_id"),
        "mode": "interaction",
        "origin": "simulation",
        "up_to_round": up_to_round,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "actions": included_actions,
            "action_types": dict(
                Counter({
                    action_type: sum(
                        int(edge.get("count") or 0)
                        for edge in edges if edge["action_type"] == action_type
                    )
                    for action_type in {edge["action_type"] for edge in edges}
                })
            ),
        },
        "notice": "边来自本次 OASIS 推演实际记录的行为，不属于初始化事实。",
    }


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload
