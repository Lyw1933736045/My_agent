"""Classify graph nodes for simulation without deciding how many Agents to keep."""

from __future__ import annotations

from typing import Any

from .ontology_service import is_speakable_actor_name


_PASSIVE_TOKENS = (
    "government", "regulator", "regulatory", "official", "agency", "exchange",
    "ministry", "commission", "监管", "政府", "官方", "交易所", "部委",
)
_SOCIAL_TOKENS = (
    "investor", "social", "netizen", "weibo", "account", "individualinvestor",
    "投资者", "社交", "网民", "股民",
)
_CONCEPT_TOKENS = (
    "policy", "product", "market", "valuation", "technology", "risk", "event",
    "theme", "concept", "industry", "trend", "ipo",
    "政策", "产品", "估值", "技术", "风险", "事件", "概念", "行业", "市场",
)


def _type_text(node: dict[str, Any], definition: dict[str, Any]) -> str:
    return " ".join(
        str(item or "")
        for item in (
            node.get("entity_type"),
            node.get("entity_type_zh"),
            definition.get("name"),
            definition.get("display_name_zh"),
            definition.get("actor_kind"),
            node.get("actor_kind"),
        )
    ).lower()


class ActorPolicy:
    """Map ontology-typed nodes to active / passive / none for fintech events."""

    def classify(self, node: dict[str, Any], definition: dict[str, Any] | None = None) -> dict[str, Any]:
        definition = definition or {}
        entity_type = str(node.get("entity_type") or definition.get("name") or "")
        actor_kind = str(node.get("actor_kind") or definition.get("actor_kind") or "").lower()
        display_name = str(node.get("display_name") or node.get("name_zh") or node.get("name") or "")
        type_text = _type_text(node, definition)

        if actor_kind == "concept" or not entity_type:
            return self._role("none", False, False, False, "concept_or_untyped")
        if any(token in type_text for token in _CONCEPT_TOKENS) and actor_kind not in {"person", "organization"}:
            return self._role("none", False, False, False, "concept_type")
        if not is_speakable_actor_name(display_name):
            return self._role("none", False, False, False, "unspeakable_name")
        if any(token in type_text for token in _PASSIVE_TOKENS):
            return self._role("passive", True, False, False, "official_or_regulator")
        if any(token in type_text for token in _SOCIAL_TOKENS):
            return self._role("active", True, True, True, "social_or_investor", pool="social")
        return self._role("active", True, True, True, "market_or_media", pool="entity")

    def apply(self, nodes: list[dict[str, Any]], ontology: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        definitions = {
            str(item.get("name")): item
            for item in (ontology or {}).get("entity_types") or []
        }
        result = []
        for node in nodes:
            definition = definitions.get(str(node.get("entity_type") or ""))
            classified = self.classify(node, definition)
            updated = dict(node)
            updated.update(classified)
            updated["node_type"] = "actor" if updated["is_actor"] else "concept"
            result.append(updated)
        return result

    @staticmethod
    def _role(
        simulation_role: str,
        is_actor: bool,
        can_speak: bool,
        can_act: bool,
        reason: str,
        *,
        pool: str = "none",
    ) -> dict[str, Any]:
        return {
            "simulation_role": simulation_role,
            "is_actor": is_actor,
            "can_speak": can_speak,
            "can_act": can_act,
            "actor_pool": pool,
            "policy_reason": reason,
        }
