"""Simulation memory loop with explicit real-context and simulation lineage."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol


HIGH_INFORMATION_ACTIONS = {"CREATE_POST", "CREATE_COMMENT", "QUOTE_POST", "REPOST"}


class SimulationGraphSink(Protocol):
    def write_episode(self, *, group_id: str, body: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class MemoryLoop:
    case_id: str
    simulation_id: str
    real_group_id: str

    @property
    def simulation_group_id(self) -> str:
        return f"{self.case_id}:simulation:{self.simulation_id}"

    def process_round(
        self,
        *,
        round_number: int,
        actions: list[dict[str, Any]],
        store: "SimulationMemoryStore",
        graph_sink: SimulationGraphSink | None = None,
    ) -> list[dict[str, Any]]:
        summaries = []
        for action in actions:
            if action.get("action_type") not in HIGH_INFORMATION_ACTIONS:
                continue
            real_refs = list(action.get("evidence_refs") or action.get("basis_real_refs") or [])
            parent_ids = list(action.get("parent_action_ids") or [])
            if not action.get("validated", True):
                continue
            summary = {
                "memory_id": store.memory_id(round_number, action["action_id"]),
                "round": round_number,
                "action_id": action["action_id"],
                "agent_id": action["agent_id"],
                "action_type": action["action_type"],
                "content": action.get("content") or "",
                "basis_real_refs": real_refs,
                "parent_action_ids": parent_ids,
                "origin": "simulation",
                "memory_policy": "high_information_simulation_action",
            }
            store.append(summary)
            if graph_sink is not None:
                summary["graph_episode_uuid"] = graph_sink.write_episode(
                    group_id=self.simulation_group_id,
                    body={
                        "case_id": self.case_id,
                        "simulation_id": self.simulation_id,
                        "round": round_number,
                        "action_id": action["action_id"],
                        "agent_id": action["agent_id"],
                        "content": action.get("content") or "",
                        "basis_real_refs": real_refs,
                        "parent_action_ids": parent_ids,
                        "origin": "simulation",
                    },
                )
            summaries.append(summary)
        return summaries

    def context_for_agent(self, store: "SimulationMemoryStore", agent_id: str) -> dict[str, Any]:
        return {
            "real_group_id": self.real_group_id,
            "simulation_group_id": self.simulation_group_id,
            "agent_id": agent_id,
            "simulation_memories": list(store.items),
            "agent_memories": [
                item for item in store.items if item.get("agent_id") == agent_id
            ],
            "policy": {
                "may_form_new_simulation_views": True,
                "may_form_new_simulation_relationships": True,
                "must_not_write_real_group": True,
            },
        }


class SimulationMemoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.items: list[dict[str, Any]] = []

    @staticmethod
    def memory_id(round_number: int, action_id: str) -> str:
        return f"memory_r{round_number}_{action_id}"

    def append(self, item: dict[str, Any]) -> None:
        if item.get("origin") != "simulation":
            raise ValueError("simulation memory must be labeled origin=simulation")
        if not item.get("basis_real_refs") and not item.get("parent_action_ids") and not item.get("content"):
            raise ValueError("simulation memory needs content or lineage")
        self.items.append(dict(item))

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"memory_version": "1.0", "items": self.items}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "SimulationMemoryStore":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("memory_version") != "1.0":
            raise ValueError("unsupported simulation memory version")
        store = cls(path)
        store.items = list(payload.get("items") or [])
        if any(item.get("origin") != "simulation" for item in store.items):
            raise ValueError("simulation memory contains a non-simulation item")
        return store
