"""Simulation run directories and subprocess lifecycle."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models.simulation import SimulationDefinition, SimulationRunState
from ..simulation.config import build_simulation_config
from ..simulation.profiles import export_profiles


class SimulationManager:
    def __init__(self, artifacts_root: Path) -> None:
        self.root = artifacts_root

    def case_root(self, case_id: str) -> Path:
        return self.root / "cases" / case_id

    def run_root(self, case_id: str, simulation_id: str) -> Path:
        return self.case_root(case_id) / "runs" / simulation_id

    def create(
        self,
        *,
        case_id: str,
        seed: dict[str, Any],
        personas: dict[str, Any],
        rounds: int = 4,
        random_seed: int = 42,
        minutes_per_round: int = 60,
        start_hour: int = 9,
    ) -> Path:
        if personas.get("status") != "approved":
            raise ValueError("simulation requires approved personas")
        approved = [p for p in personas.get("personas") or [] if p.get("status") == "approved"]
        if not approved:
            raise ValueError("no approved personas")
        simulation_id = f"sim_{case_id}_{uuid.uuid4().hex[:10]}"
        definition = SimulationDefinition(
            simulation_id=simulation_id,
            case_id=case_id,
            seed_version=str(seed.get("seed_version")),
            persona_version=str(personas.get("persona_version", "1.0")),
            rounds=rounds,
            random_seed=random_seed,
            minutes_per_round=minutes_per_round,
            start_hour=start_hour,
        )
        run_dir = self.run_root(case_id, simulation_id)
        run_dir = run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "memory").mkdir()
        (run_dir / "results").mkdir()
        (run_dir / "twitter").mkdir()
        (run_dir / "definition.json").write_text(
            json.dumps(definition.__dict__, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        export_profiles(approved, run_dir / "twitter_profiles.csv")
        simulation_config = build_simulation_config(
            definition=definition.__dict__,
            approved_personas=approved,
        )
        (run_dir / "simulation_config.json").write_text(
            json.dumps(simulation_config.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (run_dir / "seed.json").write_text(
            json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (run_dir / "personas.json").write_text(
            json.dumps(personas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._save_state(run_dir, SimulationRunState(
            simulation_id=simulation_id,
            case_id=case_id,
            total_rounds=rounds,
        ))
        return run_dir

    def start(self, run_dir: Path, *, foreground: bool = False) -> dict[str, Any]:
        run_dir = run_dir.expanduser().resolve()
        definition = json.loads((run_dir / "definition.json").read_text(encoding="utf-8"))
        state = self._load_state(run_dir)
        if state.status in {"running", "starting"}:
            raise ValueError("simulation is already running")
        state.error = None
        command = [sys.executable, "-m", "event_simulation.simulation.worker", "--run-dir", str(run_dir)]
        state.status = "starting"
        state.started_at = datetime.now(timezone.utc).isoformat()
        self._save_state(run_dir, state)
        if foreground:
            result = subprocess.run(command, cwd=str(run_dir), check=False)
            state = self._load_state(run_dir)
            if result.returncode and state.status != "failed":
                state.status = "failed"
                state.error = f"worker exited with code {result.returncode}"
                self._save_state(run_dir, state)
            return state.as_dict()
        log = (run_dir / "simulation.log").open("w", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=str(run_dir),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"),
        )
        state.pid = process.pid
        state.status = "running"
        self._save_state(run_dir, state)
        return state.as_dict()

    def status(self, run_dir: Path) -> dict[str, Any]:
        run_dir = run_dir.expanduser().resolve()
        return self._load_state(run_dir).as_dict()

    def stop(self, run_dir: Path) -> dict[str, Any]:
        run_dir = run_dir.expanduser().resolve()
        state = self._load_state(run_dir)
        if not state.pid:
            return state.as_dict()
        if os.name != "nt":
            os.killpg(state.pid, signal.SIGTERM)
        else:
            os.kill(state.pid, signal.SIGTERM)
        state.status = "stopped"
        self._save_state(run_dir, state)
        return state.as_dict()

    @staticmethod
    def _state_path(run_dir: Path) -> Path:
        return run_dir / "run_state.json"

    def _load_state(self, run_dir: Path) -> SimulationRunState:
        return SimulationRunState(**json.loads(self._state_path(run_dir).read_text(encoding="utf-8")))

    def _save_state(self, run_dir: Path, state: SimulationRunState) -> None:
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._state_path(run_dir).write_text(
            json.dumps(state.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
