"""Select simulation starters from typed graph actors.

Everyone with is_actor goes on stage. If that exceeds 35, drop the least
important: fewer accepted-claim mentions, then fewer graph links.
"""

from __future__ import annotations

from typing import Any


DEFAULT_MAX_AGENTS = 35
MAX_AGENTS = 35
MIN_AGENTS = 2


def clamp_max_agents(value: object, default: int = DEFAULT_MAX_AGENTS) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default
    return max(MIN_AGENTS, min(MAX_AGENTS, count))


def score_entity(node: dict[str, Any], claims: list[dict[str, Any]], edges: list[dict[str, Any]]) -> float:
    actor_id = str(node.get("id"))
    names = {
        str(node.get("display_name") or "").strip(),
        str(node.get("name") or "").strip(),
        str(node.get("name_zh") or "").strip(),
    }
    names.discard("")
    claim_hits = 0
    for claim in claims:
        blob = " ".join(str(claim.get(key) or "") for key in ("speaker", "organization", "text"))
        if any(name and name in blob for name in names):
            claim_hits += 1
    degree = sum(1 for edge in edges if actor_id in {str(edge.get("source")), str(edge.get("target"))})
    return claim_hits * 3 + degree + (5 if claim_hits else 0)


class ActorSelector:
    def select(
        self,
        *,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        max_agents: int = DEFAULT_MAX_AGENTS,
    ) -> dict[str, Any]:
        limit = clamp_max_agents(max_agents)
        candidates = [node for node in nodes if node.get("is_actor")]
        ranked = sorted(
            ((score_entity(node, claims, edges), node) for node in candidates),
            key=lambda item: (-item[0], str(item[1].get("display_name") or "")),
        )
        kept = [node for _, node in ranked[:limit]]
        dropped = [
            {
                "id": str(node.get("id")),
                "display_name": node.get("display_name") or node.get("name"),
                "score": score,
            }
            for score, node in ranked[limit:]
        ]
        return {
            "max_agents": limit,
            "entity_ids": [str(node["id"]) for node in kept],
            "dropped": dropped,
            "starter_count": len(kept),
            "candidate_count": len(candidates),
        }
