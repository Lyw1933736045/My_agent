"""Small, explicit simulation configuration.

Scheduling follows the useful part of MiroFish's Twitter runner: rounds map to
simulated time and each round selects active agents.  We intentionally do not
generate demographic or personality attributes that are absent from case
evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import random
from typing import Any


@dataclass(frozen=True)
class AgentActivityConfig:
    agent_id: int
    persona_id: str
    display_name: str
    role_group: str
    activity_level: float = 1.0


@dataclass(frozen=True)
class SimulationConfig:
    simulation_id: str
    case_id: str
    rounds: int
    random_seed: int
    minutes_per_round: int
    start_hour: int
    agents_per_round_min: int
    agents_per_round_max: int
    agent_configs: tuple[AgentActivityConfig, ...]
    action_types: tuple[str, ...] = (
        "CREATE_POST",
        "CREATE_COMMENT",
        "LIKE_POST",
        "DISLIKE_POST",
        "REPOST",
        "QUOTE_POST",
        "FOLLOW",
        "DO_NOTHING",
    )

    def __post_init__(self) -> None:
        if self.rounds <= 0:
            raise ValueError("rounds must be positive")
        if not self.agent_configs:
            raise ValueError("at least one approved agent is required")
        if not 0 <= self.start_hour <= 23:
            raise ValueError("start_hour must be between 0 and 23")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["agent_configs"] = [asdict(item) for item in self.agent_configs]
        payload["action_types"] = list(self.action_types)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SimulationConfig":
        values = dict(payload)
        values["agent_configs"] = tuple(
            AgentActivityConfig(**item) for item in payload.get("agent_configs") or []
        )
        values["action_types"] = tuple(payload.get("action_types") or ())
        return cls(**values)

    def simulated_hour(self, round_number: int) -> int:
        elapsed_minutes = max(0, round_number - 1) * self.minutes_per_round
        return (self.start_hour + elapsed_minutes // 60) % 24

    def active_agent_ids(self, round_number: int) -> list[int]:
        """Choose a reproducible active subset without inventing persona facts."""
        eligible = [item.agent_id for item in self.agent_configs]
        if len(eligible) <= 10:
            return eligible
        generator = random.Random(self.random_seed + round_number)
        target = generator.randint(
            min(self.agents_per_round_min, len(eligible)),
            min(self.agents_per_round_max, len(eligible)),
        )
        return sorted(generator.sample(eligible, target))


def build_simulation_config(
    *, definition: dict[str, Any], approved_personas: list[dict[str, Any]]
) -> SimulationConfig:
    count = len(approved_personas)
    minimum = count if count <= 10 else max(2, int(count * 0.6))
    return SimulationConfig(
        simulation_id=str(definition["simulation_id"]),
        case_id=str(definition["case_id"]),
        rounds=int(definition.get("rounds", 4)),
        random_seed=int(definition.get("random_seed", 42)),
        minutes_per_round=int(definition.get("minutes_per_round", 60)),
        start_hour=int(definition.get("start_hour", 9)),
        agents_per_round_min=minimum,
        agents_per_round_max=count,
        agent_configs=tuple(
            AgentActivityConfig(
                agent_id=index,
                persona_id=str(persona["persona_id"]),
                display_name=str(persona.get("display_name") or persona["persona_id"]),
                role_group=str(persona.get("role_group") or persona.get("role_type") or "unknown"),
            )
            for index, persona in enumerate(approved_personas)
        ),
    )
