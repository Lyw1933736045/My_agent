"""Append-only action stream adapted from MiroFish's action logger.

Copyright (c) MiroFish contributors. Modified for EventSimulation.
SPDX-License-Identifier: AGPL-3.0-only
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class ActionLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        self.path.write_text("", encoding="utf-8")

    def append(self, payload: dict[str, Any]) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": "twitter",
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def simulation_start(self, *, rounds: int, agents_count: int) -> None:
        self.append({
            "event_type": "simulation_start",
            "total_rounds": rounds,
            "agents_count": agents_count,
        })

    def round_start(self, *, round_number: int, simulated_hour: int, active_agent_ids: list[int]) -> None:
        self.append({
            "event_type": "round_start",
            "round": round_number,
            "simulated_hour": simulated_hour,
            "active_agent_ids": active_agent_ids,
        })

    def action(self, *, round_number: int, action: dict[str, Any]) -> None:
        self.append({
            "event_type": "agent_action",
            "round": round_number,
            **action,
        })

    def round_end(self, *, round_number: int, actions_count: int) -> None:
        self.append({
            "event_type": "round_end",
            "round": round_number,
            "actions_count": actions_count,
        })

    def simulation_end(self, *, rounds: int, actions_count: int, status: str) -> None:
        self.append({
            "event_type": "simulation_end",
            "total_rounds": rounds,
            "total_actions": actions_count,
            "status": status,
        })
