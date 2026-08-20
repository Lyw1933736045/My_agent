"""Evidence-derived role, persona, and runtime agent models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RoleGroup:
    role_group_id: str
    label: str
    persona_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Persona:
    persona_id: str
    display_name: str
    role_type: str
    entity_refs: list[str]
    identity_evidence: list[dict[str, Any]]
    stance_refs: list[str]
    allowed_fact_refs: list[str]
    status: str = "pending_human_review"


@dataclass(frozen=True)
class AgentInstance:
    """OASIS mapping; runtime fields are technical and not real-world facts."""

    agent_id: int
    persona_id: str
    runtime_username: str
    role_group: str

