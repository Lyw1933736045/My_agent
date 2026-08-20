"""Filesystem/subprocess bridge to the isolated EventSimulation runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from threading import Lock, Thread
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
        self.database_url = os.getenv("DATABASE_URL", "")
        self._build_lock = Lock()
        self._running: set[str] = set()

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

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _case_dir(self, case_ref: str) -> Path:
        return self.artifacts / self._validate_ref(case_ref, "case reference")

    def _runs_dir(self, case_ref: str) -> Path:
        return self.artifacts / "cases" / self._validate_ref(case_ref, "case reference") / "runs"

    def _run_dir(self, case_ref: str, simulation_id: str) -> Path:
        run_dir = self._runs_dir(case_ref) / self._validate_ref(simulation_id, "simulation id")
        if not run_dir.is_dir():
            raise SimulationBridgeError(f"simulation not found: {simulation_id}")
        return run_dir

    def run_dir(self, case_ref: str, simulation_id: str) -> Path:
        return self._run_dir(case_ref, simulation_id)

    def _job_path(self, case_ref: str) -> Path:
        return self.artifacts / "jobs" / f"{self._validate_ref(case_ref, 'case reference')}.json"

    def artifact_ref_for(self, case_key: str | None, case_id: str) -> str:
        candidates = []
        for value in (case_key, case_id):
            if value and _SAFE_REF.fullmatch(value):
                candidates.append(value)
        if not candidates:
            raise SimulationBridgeError("案例 ID 无法用于图谱存储")
        for value in candidates:
            if (self.artifacts / value / "seed.json").is_file():
                return value
        for value in candidates:
            if self._job_path(value).is_file():
                return value
        return candidates[0]

    def _command(self, *args: str, timeout: float = 60.0) -> str:
        if not self.python.is_file():
            raise SimulationBridgeError(f"EventSimulation Python not found: {self.python}")
        env = os.environ.copy()
        if self.database_url:
            env["DATABASE_URL"] = self.database_url
        try:
            completed = subprocess.run(
                [str(self.python), "-m", "event_simulation.cli", *args],
                cwd=self.root,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise SimulationBridgeError(
                f"命令超时（{timeout:.0f}s）：{' '.join(args[:3])}"
            ) from exc
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
            raise SimulationBridgeError("这份简报还没有知识图谱，请先生成。")
        compatible = [item for item in candidates if item[0]]
        if not compatible:
            raise SimulationBridgeError("现有知识图谱未使用主体本体，请重新生成。")
        return max(compatible, key=lambda item: (item[1], item[2]))[3]

    def has_seed(self, case_ref: str) -> bool:
        return (self._case_dir(case_ref) / "seed.json").is_file()

    def has_personas(self, case_ref: str) -> bool:
        return (self._case_dir(case_ref) / "personas_approved.json").is_file()

    def read_job(self, case_ref: str) -> dict[str, Any] | None:
        path = self._job_path(case_ref)
        if not path.is_file():
            return None
        try:
            payload = self._read_json(path)
        except (SimulationBridgeError, json.JSONDecodeError):
            return None
        return payload

    def _write_job(self, case_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
        job = {
            **payload,
            "case_ref": case_ref,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_json(self._job_path(case_ref), job)
        return job

    def wipe_case_artifacts(self, case_ref: str) -> None:
        case_dir = self._case_dir(case_ref)
        runs_parent = self.artifacts / "cases" / self._validate_ref(case_ref, "case reference")
        if case_dir.is_dir():
            shutil.rmtree(case_dir)
        if runs_parent.is_dir():
            shutil.rmtree(runs_parent)

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
                status.get("detail") or "知识图谱尚未就绪，请先生成图谱。"
            )
        return status

    def overview(self, case_ref: str) -> dict[str, Any]:
        case_dir = self._case_dir(case_ref)
        job = self.read_job(case_ref)
        seed_path = case_dir / "seed.json"
        if not seed_path.is_file():
            return {
                "case_ref": case_ref,
                "graph_ready": False,
                "can_simulate": False,
                "has_seed": False,
                "has_personas": False,
                "job": job,
                "zep": {"ready": False},
                "counts": {},
                "agents": [],
                "latest_run": None,
                "scenario": (job or {}).get("scenario"),
            }
        seed = self._read_json(seed_path)
        personas_path = case_dir / "personas_approved.json"
        personas = self._read_json(personas_path) if personas_path.is_file() else {"personas": []}
        runs = self.list_runs(case_ref)
        zep = self.zep_status(case_ref) if any(case_dir.glob("graph_manifest*.json")) else {
            "ready": False,
            "detail": "这份简报还没有知识图谱，请先生成。",
        }
        agents = [
            {
                "persona_id": item.get("persona_id"),
                "display_name": item.get("display_name"),
                "role_group": item.get("role_group") or item.get("role_type"),
            }
            for item in personas.get("personas") or []
            if item.get("status") == "approved"
        ]
        graph_ready = bool(zep.get("ready"))
        return {
            "case_ref": case_ref,
            "case_id": seed.get("case_id"),
            "seed_version": seed.get("seed_version"),
            "event_summary": seed.get("event_summary"),
            "graph_ready": graph_ready,
            "can_simulate": graph_ready and bool(agents),
            "has_seed": True,
            "has_personas": bool(agents),
            "job": job,
            "zep": zep,
            "scenario": seed.get("scenario") or (job or {}).get("scenario"),
            "counts": {
                "facts": len(seed.get("facts") or []),
                "claims": len(seed.get("claims") or []),
                "entities": len(seed.get("entities") or []),
                "sources": len(seed.get("sources") or []),
                "agents": len(agents),
                "runs": len(runs),
            },
            "agents": agents,
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
        state["has_memory"] = (run_dir / "memory" / "simulation_memory.json").is_file()
        return state

    def start(self, case_ref: str, rounds: int) -> dict[str, Any]:
        if not 1 <= rounds <= 48:
            raise SimulationBridgeError("rounds must be between 1 and 48")
        self._require_zep(case_ref)
        case_dir = self._case_dir(case_ref)
        personas_path = case_dir / "personas_approved.json"
        if not personas_path.is_file():
            raise SimulationBridgeError("还没有推演角色，请先生成知识图谱。")
        output = self._command(
            "simulation", "create",
            "--case", case_ref,
            "--seed", str(case_dir / "seed.json"),
            "--personas", str(personas_path),
            "--manifest", str(self._manifest_path(case_ref)),
            "--artifacts-root", str(self.artifacts),
            "--rounds", str(rounds),
            timeout=180.0,
        )
        run_dir = Path(output.splitlines()[-1]).resolve()
        self._command("simulation", "start", str(run_dir), timeout=30.0)
        self._retain_only_run(case_ref, run_dir.name)
        return self.run_status(case_ref, run_dir.name)

    def _retain_only_run(self, case_ref: str, simulation_id: str) -> None:
        runs_dir = self._runs_dir(case_ref)
        if not runs_dir.is_dir():
            return
        keep = self._validate_ref(simulation_id, "simulation id")
        for path in runs_dir.iterdir():
            if path.is_dir() and path.name != keep:
                shutil.rmtree(path, ignore_errors=True)

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
            timeout=120.0,
        )
        graph = json.loads(output)
        return self._mark_simulation_starters(case_ref, graph)

    def _mark_simulation_starters(self, case_ref: str, graph: dict[str, Any]) -> dict[str, Any]:
        personas_path = self._case_dir(case_ref) / "personas_approved.json"
        if not personas_path.is_file():
            for node in graph.get("nodes") or []:
                node["simulation_start"] = False
            return graph
        personas = self._read_json(personas_path)
        entity_ids: set[str] = set()
        for item in personas.get("personas") or []:
            if item.get("status") != "approved":
                continue
            if item.get("graph_entity_uuid"):
                entity_ids.add(str(item["graph_entity_uuid"]))
            for related in item.get("entity_refs") or []:
                if related:
                    entity_ids.add(str(related))
        for node in graph.get("nodes") or []:
            node["simulation_start"] = str(node.get("id")) in entity_ids
        return graph

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

    def start_graph_build(
        self,
        case_ref: str,
        *,
        question: str,
        as_of: str,
        horizon_hours: int,
        allow_undated_social: bool = True,
        source_case: str,
        max_agents: int = 35,
    ) -> dict[str, Any]:
        question = " ".join(question.split())
        if len(question) < 8:
            raise SimulationBridgeError("请填写推演问题")
        if not 1 <= horizon_hours <= 168:
            raise SimulationBridgeError("时间窗需在 1 到 168 小时之间")
        if not self.database_url:
            raise SimulationBridgeError("未配置 DATABASE_URL，无法从简报生成图谱")
        scenario = {
            "question": question,
            "as_of": as_of,
            "horizon_hours": horizon_hours,
            "allow_undated_social": allow_undated_social,
            "max_agents": max(2, min(35, int(max_agents))),
        }
        with self._build_lock:
            if case_ref in self._running:
                raise SimulationBridgeError("这份简报的知识图谱正在生成")
            job = self.read_job(case_ref)
            if job and job.get("status") == "running":
                raise SimulationBridgeError("这份简报的知识图谱正在生成")
            self._running.add(case_ref)
            self._write_job(case_ref, {
                "status": "running",
                "progress": "准备生成知识图谱",
                "error": None,
                "scenario": scenario,
            })
        thread = Thread(
            target=self._run_graph_build,
            kwargs={
                "case_ref": case_ref,
                "source_case": source_case,
                "scenario": scenario,
            },
            daemon=True,
            name=f"graph-build-{case_ref[:12]}",
        )
        thread.start()
        return self.read_job(case_ref) or {}

    def _run_graph_build(self, case_ref: str, source_case: str, scenario: dict[str, Any]) -> None:
        try:
            self._write_job(case_ref, {
                "status": "running",
                "progress": "正在清除旧图谱和推演结果",
                "error": None,
                "scenario": scenario,
            })
            self.wipe_case_artifacts(case_ref)
            case_dir = self._case_dir(case_ref)
            case_dir.mkdir(parents=True, exist_ok=True)
            build_args = [
                "build",
                "--case", source_case,
                "--as-of", str(scenario["as_of"]),
                "--horizon-hours", str(scenario["horizon_hours"]),
                "--question", str(scenario["question"]),
                "--output-dir", str(case_dir),
                "--database-url", self.database_url,
            ]
            if scenario.get("allow_undated_social"):
                build_args.append("--allow-undated-social")
            self._write_job(case_ref, {
                "status": "running",
                "progress": "正在从简报抽取 Seed",
                "error": None,
                "scenario": scenario,
            })
            self._command(*build_args, timeout=180.0)
            manifest = case_dir / "graph_manifest_v3.json"
            self._write_job(case_ref, {
                "status": "running",
                "progress": "正在写入 Zep 知识图谱，可能需要数分钟",
                "error": None,
                "scenario": scenario,
            })
            self._command(
                "graph-init",
                "--seed", str(case_dir / "seed.json"),
                "--manifest", str(manifest),
                "--real-version", "v3",
                "--wait-seconds", "600",
                timeout=900.0,
            )
            self._write_job(case_ref, {
                "status": "running",
                "progress": "正在选择推演起点",
                "error": None,
                "scenario": scenario,
            })
            self._command(
                "personas",
                "--seed", str(case_dir / "seed.json"),
                "--manifest", str(manifest),
                "--output-dir", str(case_dir),
                "--max-agents", str(int(scenario.get("max_agents") or 35)),
                timeout=180.0,
            )
            personas = self._read_json(case_dir / "personas.json")
            if not personas.get("personas"):
                raise SimulationBridgeError("图谱已生成，但没有可进入推演的角色，无法开始演化。")
            if not (case_dir / "personas_approved.json").is_file():
                self._command(
                    "personas-approve",
                    "--input", str(case_dir / "personas.json"),
                    "--output", str(case_dir / "personas_approved.json"),
                    timeout=30.0,
                )
            self._write_job(case_ref, {
                "status": "completed",
                "progress": "知识图谱已就绪，可设置轮数开始演化",
                "error": None,
                "scenario": scenario,
            })
        except Exception as exc:
            self._write_job(case_ref, {
                "status": "failed",
                "progress": "生成失败",
                "error": str(exc),
                "scenario": scenario,
            })
        finally:
            with self._build_lock:
                self._running.discard(case_ref)
