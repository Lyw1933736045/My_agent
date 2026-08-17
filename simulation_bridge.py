"""Filesystem/subprocess bridge to the isolated EventSimulation runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


_SAFE_REF = re.compile(r"^[A-Za-z0-9_.-]+$")


class SimulationBridgeError(RuntimeError):
    pass


class SimulationBridge:
    def __init__(self) -> None:
        default_root = Path(__file__).resolve().parent.parent / "EventSimulation"
        self.root = Path(os.getenv("EVENT_SIMULATION_ROOT", str(default_root))).resolve()
        self.artifacts = Path(
            os.getenv("EVENT_SIMULATION_ARTIFACTS_ROOT", str(self.root / "artifacts"))
        ).resolve()
        self.python = Path(
            os.getenv("EVENT_SIMULATION_PYTHON", str(self.root / ".venv" / "bin" / "python"))
        ).expanduser()

    @staticmethod
    def _validate_ref(value: str, label: str) -> str:
        if not _SAFE_REF.fullmatch(value):
            raise SimulationBridgeError(f"invalid {label}: {value!r}")
        return value

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise SimulationBridgeError(f"artifact not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise SimulationBridgeError(f"expected JSON object: {path}")
        return payload

    def _case_dir(self, case_ref: str) -> Path:
        return self.artifacts / self._validate_ref(case_ref, "case reference")

    def _runs_dir(self, case_ref: str) -> Path:
        return self.artifacts / "cases" / self._validate_ref(case_ref, "case reference") / "runs"

    def _run_dir(self, case_ref: str, simulation_id: str) -> Path:
        run_dir = self._runs_dir(case_ref) / self._validate_ref(simulation_id, "simulation id")
        if not run_dir.is_dir():
            raise SimulationBridgeError(f"simulation not found: {simulation_id}")
        return run_dir

    def _command(self, *args: str, timeout: float = 60.0) -> str:
        if not self.python.is_file():
            raise SimulationBridgeError(f"EventSimulation Python not found: {self.python}")
        completed = subprocess.run(
            [str(self.python), "-m", "event_simulation.cli", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise SimulationBridgeError(detail or f"command exited with {completed.returncode}")
        return completed.stdout.strip()

    def list_cases(self) -> list[dict[str, Any]]:
        if not self.artifacts.is_dir():
            return []
        results = []
        for seed_path in sorted(self.artifacts.glob("*/seed.json")):
            seed = self._read_json(seed_path)
            case_ref = seed_path.parent.name
            results.append({
                "case_ref": case_ref,
                "case_id": seed.get("case_id"),
                "seed_version": seed.get("seed_version"),
                "event_summary": seed.get("event_summary"),
            })
        return results

    def _manifest_path(self, case_ref: str) -> Path:
        case_dir = self._case_dir(case_ref)
        candidates = []
        for path in case_dir.glob("graph_manifest*.json"):
            if not path.is_file():
                continue
            try:
                manifest = self._read_json(path)
            except (SimulationBridgeError, json.JSONDecodeError):
                continue
            candidates.append((
                bool(manifest.get("ontology")),
                str(manifest.get("manifest_version") or ""),
                path.stat().st_mtime,
                path,
            ))
        if not candidates:
            raise SimulationBridgeError("知识图谱清单不存在，请先按本体链路构建图谱。")
        compatible = [item for item in candidates if item[0]]
        if not compatible:
            raise SimulationBridgeError("现有知识图谱未使用主体本体，请用新的图谱版本重新构建。")
        return max(compatible, key=lambda item: (item[1], item[2]))[3]

    def zep_status(self, case_ref: str) -> dict[str, Any]:
        try:
            output = self._command("graph-status", "--manifest", str(self._manifest_path(case_ref)))
            payload = json.loads(output.splitlines()[-1])
            if not isinstance(payload, dict):
                raise SimulationBridgeError("invalid graph-status payload")
            return payload
        except SimulationBridgeError as exc:
            text = str(exc).strip().splitlines()[-1]
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict) and payload.get("detail"):
                payload.setdefault("ready", False)
                return payload
            return {"ready": False, "detail": str(exc)}

    def _require_zep(self, case_ref: str) -> dict[str, Any]:
        status = self.zep_status(case_ref)
        if not status.get("ready"):
            raise SimulationBridgeError(
                status.get("detail") or "Zep 真实图谱未就绪，已终止。"
            )
        return status

    def overview(self, case_ref: str) -> dict[str, Any]:
        case_dir = self._case_dir(case_ref)
        seed = self._read_json(case_dir / "seed.json")
        personas_path = case_dir / "personas_approved.json"
        personas = self._read_json(personas_path) if personas_path.is_file() else {"personas": []}
        runs = self.list_runs(case_ref)
        zep = self.zep_status(case_ref)
        return {
            "case_ref": case_ref,
            "case_id": seed.get("case_id"),
            "seed_version": seed.get("seed_version"),
            "event_summary": seed.get("event_summary"),
            "zep": zep,
            "counts": {
                "facts": len(seed.get("facts") or []),
                "claims": len(seed.get("claims") or []),
                "entities": len(seed.get("entities") or []),
                "sources": len(seed.get("sources") or []),
                "agents": len(personas.get("personas") or []),
                "runs": len(runs),
            },
            "agents": [
                {
                    "persona_id": item.get("persona_id"),
                    "display_name": item.get("display_name"),
                    "role_group": item.get("role_group") or item.get("role_type"),
                }
                for item in personas.get("personas") or []
            ],
            "latest_run": runs[0] if runs else None,
        }

    def list_runs(self, case_ref: str) -> list[dict[str, Any]]:
        runs_dir = self._runs_dir(case_ref)
        if not runs_dir.is_dir():
            return []
        results = []
        for path in runs_dir.iterdir():
            state_path = path / "run_state.json"
            if path.is_dir() and state_path.is_file():
                state = self._read_json(state_path)
                state["run_dir"] = str(path)
                results.append(state)
        return sorted(results, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def run_status(self, case_ref: str, simulation_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(case_ref, simulation_id)
        state = self._read_json(run_dir / "run_state.json")
        state["has_result"] = (run_dir / "simulation_run.json").is_file()
        state["has_report"] = (run_dir / "results" / "simulation_report.json").is_file()
        return state

    def start(self, case_ref: str, rounds: int) -> dict[str, Any]:
        if not 1 <= rounds <= 48:
            raise SimulationBridgeError("rounds must be between 1 and 48")
        self._require_zep(case_ref)
        case_dir = self._case_dir(case_ref)
        output = self._command(
            "simulation", "create",
            "--case", case_ref,
            "--seed", str(case_dir / "seed.json"),
            "--personas", str(case_dir / "personas_approved.json"),
            "--manifest", str(self._manifest_path(case_ref)),
            "--artifacts-root", str(self.artifacts),
            "--rounds", str(rounds),
        )
        run_dir = Path(output.splitlines()[-1]).resolve()
        self._command("simulation", "start", str(run_dir), timeout=30.0)
        return self.run_status(case_ref, run_dir.name)

    def stop(self, case_ref: str, simulation_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(case_ref, simulation_id)
        output = self._command("simulation", "stop", str(run_dir), timeout=30.0)
        return json.loads(output)

    def evidence_graph(self, case_ref: str) -> dict[str, Any]:
        case_dir = self._case_dir(case_ref)
        output = self._command(
            "graph-view", "evidence",
            "--manifest", str(self._manifest_path(case_ref)),
            "--seed", str(case_dir / "seed.json"),
        )
        return json.loads(output)

    def interaction_graph(
        self, case_ref: str, simulation_id: str, up_to_round: int | None
    ) -> dict[str, Any]:
        run_dir = self._run_dir(case_ref, simulation_id)
        personas_path = self._case_dir(case_ref) / "personas_approved.json"
        args = [
            "graph-view", "interactions", "--run-dir", str(run_dir),
            "--personas", str(personas_path),
        ]
        if up_to_round is not None:
            args.extend(["--round", str(up_to_round)])
        return json.loads(self._command(*args))

    def rounds(self, case_ref: str, simulation_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(case_ref, simulation_id)
        run = self._read_json(run_dir / "simulation_run.json")
        return {
            "simulation_id": simulation_id,
            "case_id": run.get("case_id"),
            "status": run.get("status"),
            "rounds": run.get("rounds") or [],
        }

    def analysis(self, case_ref: str, simulation_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(case_ref, simulation_id)
        personas_path = self._case_dir(case_ref) / "personas_approved.json"
        output = self._command(
            "analysis-view",
            "--run-dir", str(run_dir),
            "--personas", str(personas_path),
        )
        return json.loads(output)

    def report(self, case_ref: str, simulation_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(case_ref, simulation_id)
        result_dir = run_dir / "results"
        payload = self._read_json(result_dir / "simulation_report.json")
        markdown_path = result_dir / "simulation_report.md"
        payload["markdown"] = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""
        return payload
