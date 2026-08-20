"""One-process OASIS simulation engine.

The orchestration is adapted from MiroFish's Twitter simulation runner.
Copyright (c) MiroFish contributors. Modified for EventSimulation.
SPDX-License-Identifier: AGPL-3.0-only
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models.simulation import SimulationRunState
from ..services.memory_service import MemoryLoop, SimulationMemoryStore
from ..services.simulation_graph_memory import SimulationZepMemoryUpdater
from .action_log import ActionLog
from .config import SimulationConfig
from .oasis_adapter import BUSINESS_ACTIONS, OasisTwitterAdapter, read_trace
from .reporting import SimulationReporter


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_model(config: dict[str, Any]) -> Any:
    try:
        from camel.models import ModelFactory
        from camel.types import ModelPlatformType
    except ImportError as exc:
        raise RuntimeError("install event-simulation[simulation] first") from exc
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")
    model_name = os.getenv("LLM_MODEL_NAME") or config.get("model") or "deepseek-chat"
    if not api_key:
        raise RuntimeError("LLM_API_KEY or OPENAI_API_KEY is required")
    os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_API_BASE_URL"] = base_url
    return ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=model_name,
    )


class SimulationEngine:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir.expanduser().resolve()
        self.definition = self._read("definition.json")
        self.seed = self._read("seed.json")
        self.persona_payload = self._read("personas.json")
        self.personas = [
            item for item in self.persona_payload.get("personas") or []
            if item.get("status") == "approved"
        ]
        self.config = SimulationConfig.from_dict(self._read("simulation_config.json"))
        self.state = SimulationRunState(**self._read("run_state.json"))
        self.database_path = self.run_dir / "oasis.db"
        self.action_log = ActionLog(self.run_dir / "twitter" / "actions.jsonl")
        self.memory = SimulationMemoryStore(self.run_dir / "memory" / "simulation_memory.json")
        self.memory_loop = MemoryLoop(
            case_id=self.config.case_id,
            simulation_id=self.config.simulation_id,
            real_group_id=str(
                self.persona_payload.get("graph_group_id")
                or f"{self.config.case_id}:real:unknown"
            ),
        )

    def _read(self, name: str) -> dict[str, Any]:
        payload = json.loads((self.run_dir / name).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{name} must contain a JSON object")
        return payload

    def _save_state(self) -> None:
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        _write_json(self.run_dir / "run_state.json", self.state.as_dict())

    def _initial_posts(self) -> list[dict[str, Any]]:
        claims = {item.get("claim_id"): item for item in self.seed.get("claims") or []}
        for agent_id, persona in enumerate(self.personas):
            for ref in persona.get("stance_refs") or []:
                claim = claims.get(ref)
                if claim and claim.get("text"):
                    return [{
                        "agent_id": agent_id,
                        "content": str(claim["text"]),
                        "evidence_refs": [ref],
                    }]
        raise ValueError("no approved persona has a grounded initial claim")

    def _decorate(
        self,
        actions: list[dict[str, Any]],
        *,
        initial_posts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        initial_lookup = {
            (item["agent_id"], item["content"]): item["evidence_refs"]
            for item in initial_posts or []
        }
        result = []
        for action in actions:
            if action.get("action_type") not in BUSINESS_ACTIONS:
                continue
            agent_id = action.get("agent_id")
            persona = (
                self.personas[agent_id]
                if isinstance(agent_id, int) and 0 <= agent_id < len(self.personas)
                else {}
            )
            action["persona_id"] = persona.get("persona_id")
            action["agent_name"] = persona.get("display_name")
            action["role_group"] = persona.get("role_group") or persona.get("role_type")
            action["persona_evidence_scope"] = list(persona.get("stance_refs") or [])
            evidence_refs = initial_lookup.get((agent_id, action.get("content")), [])
            action["basis_real_refs"] = list(evidence_refs)
            action["initial_grounded_event"] = bool(evidence_refs)
            args = action.get("action_args")
            if isinstance(args, dict):
                author_id = args.get("post_author_id")
                author_name = self._agent_name(author_id)
                if author_name:
                    args.setdefault("post_author_name", author_name)
                    args.setdefault("original_author_name", author_name)
                if args.get("original_content") is None and args.get("post_content"):
                    args["original_content"] = args["post_content"]
                target_id = args.get("followed_user_id") or args.get("target_id")
                target_name = self._agent_name(target_id)
                if target_name:
                    args.setdefault("target_user_name", target_name)
            result.append(action)
        return result

    def _agent_name(self, agent_id: Any) -> str:
        if isinstance(agent_id, int) and 0 <= agent_id < len(self.personas):
            return str(self.personas[agent_id].get("display_name") or "")
        return ""

    def _save_run(self, rounds: list[dict[str, Any]], *, status: str) -> dict[str, Any]:
        payload = {
            "simulation_version": "2.0",
            "simulation_id": self.config.simulation_id,
            "case_id": self.config.case_id,
            "status": status,
            "origin": "simulation",
            "real_context_group_id": self.memory_loop.real_group_id,
            "simulation_group_id": self.memory_loop.simulation_group_id,
            "simulation_zep_graph_id": getattr(self, "_simulation_graph_id", None),
            "rounds": rounds,
        }
        _write_json(self.run_dir / "simulation_run.json", payload)
        return payload

    async def run(self) -> None:
        adapter: OasisTwitterAdapter | None = None
        graph_memory = SimulationZepMemoryUpdater.from_env(
            case_id=self.config.case_id,
            simulation_id=self.config.simulation_id,
        )
        self._simulation_graph_id = None if graph_memory is None else graph_memory.graph_id
        rounds: list[dict[str, Any]] = []
        cursor = 0
        total_business_actions = 0
        self.state.error = None
        self.action_log.reset()
        self.action_log.simulation_start(
            rounds=self.config.rounds,
            agents_count=len(self.personas),
        )
        try:
            adapter = OasisTwitterAdapter(
                profile_path=self.run_dir / "twitter_profiles.csv",
                database_path=self.database_path,
                model=create_model(self.definition),
                action_names=self.config.action_types,
            )
            await adapter.open()
            self.state.status = "running"
            self._save_state()

            initial_posts = self._initial_posts()
            await adapter.initial_posts(initial_posts)
            raw = read_trace(self.database_path, after_rowid=cursor)
            cursor = max((item["trace_rowid"] for item in raw), default=cursor)
            initial_actions = self._decorate(raw, initial_posts=initial_posts)
            rounds.append({
                "round": 0,
                "simulated_hour": self.config.start_hour,
                "active_agent_ids": [item["agent_id"] for item in initial_posts],
                "actions": initial_actions,
            })
            for action in initial_actions:
                self.action_log.action(round_number=0, action=action)
            self.memory_loop.process_round(round_number=0, actions=initial_actions, store=self.memory)
            self.memory.save()
            if graph_memory is not None:
                graph_memory.add_actions(initial_actions)
            total_business_actions += len(initial_actions)
            self._save_run(rounds, status="running")

            for round_number in range(1, self.config.rounds + 1):
                active_ids = self.config.active_agent_ids(round_number)
                simulated_hour = self.config.simulated_hour(round_number)
                self.action_log.round_start(
                    round_number=round_number,
                    simulated_hour=simulated_hour,
                    active_agent_ids=active_ids,
                )
                await adapter.llm_round(active_ids)
                raw = read_trace(self.database_path, after_rowid=cursor)
                cursor = max((item["trace_rowid"] for item in raw), default=cursor)
                actions = self._decorate(raw)
                for action in actions:
                    self.action_log.action(round_number=round_number, action=action)
                self.action_log.round_end(round_number=round_number, actions_count=len(actions))
                rounds.append({
                    "round": round_number,
                    "simulated_hour": simulated_hour,
                    "active_agent_ids": active_ids,
                    "actions": actions,
                })
                self.memory_loop.process_round(
                    round_number=round_number,
                    actions=actions,
                    store=self.memory,
                )
                self.memory.save()
                if graph_memory is not None:
                    graph_memory.add_actions(actions)
                total_business_actions += len(actions)
                self.state.current_round = round_number
                self.state.actions_count = total_business_actions
                self._save_state()
                self._save_run(rounds, status="running")

            run_payload = self._save_run(rounds, status="completed")
            SimulationReporter().generate(
                run=run_payload,
                personas=self.personas,
                output_dir=self.run_dir / "results",
            )
            self.state.status = "completed"
            self.state.error = None
            self.state.completed_at = datetime.now(timezone.utc).isoformat()
            self._save_state()
            self.action_log.simulation_end(
                rounds=self.config.rounds,
                actions_count=total_business_actions,
                status="completed",
            )
        except Exception as exc:
            self._save_run(rounds, status="failed")
            self.state.status = "failed"
            self.state.error = str(exc)
            self._save_state()
            self.action_log.simulation_end(
                rounds=self.state.current_round,
                actions_count=total_business_actions,
                status="failed",
            )
            raise
        finally:
            if graph_memory is not None:
                status = graph_memory.close()
                _write_json(self.run_dir / "simulation_graph_memory.json", status)
            if adapter is not None:
                await adapter.close()
