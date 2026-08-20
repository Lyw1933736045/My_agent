"""Simulation lifecycle models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class SimulationDefinition:
    simulation_id: str
    case_id: str
    seed_version: str
    persona_version: str
    platform: str = "twitter"
    rounds: int = 4
    random_seed: int = 42
    minutes_per_round: int = 60
    start_hour: int = 9

    def __post_init__(self) -> None:
        if self.platform != "twitter":
            raise ValueError("case1 MVP supports only the Twitter-like OASIS platform")
        if self.rounds <= 0:
            raise ValueError("rounds must be positive")
        if self.minutes_per_round <= 0:
            raise ValueError("minutes_per_round must be positive")
        if not 0 <= self.start_hour <= 23:
            raise ValueError("start_hour must be between 0 and 23")


@dataclass
class SimulationRunState:
    simulation_id: str
    case_id: str
    status: str = "created"
    current_round: int = 0
    total_rounds: int = 4
    pid: int | None = None
    actions_count: int = 0
    started_at: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()
