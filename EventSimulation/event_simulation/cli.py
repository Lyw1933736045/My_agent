"""Terminal entry point for the case-to-simulation workflow."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

from .models.case import ScenarioConfig
from .repositories.my_agent import CaseLoader, MyAgentRepositoryAdapter
from .services.case_service import CaseService
from .services.seed_builder import SeedBuilder
from .integrations.zep import ZepConfig, ZepGraphNotReady, ZepGraphWriter
from .services.persona_service import PersonaBuilder, approve_personas, write_persona_artifacts
from .services.actor_selector import ActorSelector, clamp_max_agents
from .services.analysis_service import HoldoutEvaluator, ResultAnalyzer, write_result_artifacts
from .runtime.manager import SimulationManager
from .services.network_service import build_interaction_graph
from .services.visualization_service import build_visualization_analysis


DEFAULT_QUESTION = (
    "人民币外汇期货试点被公开提出后，未来 48 小时内不同金融主体可能形成"
    "哪些关注点、分歧和传播路径？"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="event-simulation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build a case audit and strict Seed")
    build.add_argument("--case", required=True)
    build.add_argument("--as-of", required=True, help="timezone-aware ISO 8601 cutoff")
    build.add_argument("--horizon-hours", type=int, default=48)
    build.add_argument("--question", default=DEFAULT_QUESTION)
    build.add_argument(
        "--allow-undated-social",
        action="store_true",
        help="temporarily allow undated Weibo/social claims; facts and other sources remain date-gated",
    )
    build.add_argument("--database-url", default=None)
    build.add_argument("--output-dir", type=Path, required=True)

    graph = subparsers.add_parser("graph-init", help="initialize the real Zep Cloud graph")
    graph.add_argument("--seed", type=Path, required=True)
    graph.add_argument("--manifest", type=Path, required=True)
    graph.add_argument("--zep-api-key", default=None)
    graph.add_argument("--real-version", default="v1")
    graph.add_argument("--wait-seconds", type=int, default=600)

    query = subparsers.add_parser("graph-query", help="run real-graph trace queries")
    query.add_argument("--manifest", type=Path, required=True)
    query.add_argument("--output", type=Path, required=True)
    query.add_argument("--zep-api-key", default=None)

    status = subparsers.add_parser("graph-status", help="require a ready Zep real graph or exit")
    status.add_argument("--manifest", type=Path, required=True)
    status.add_argument("--zep-api-key", default=None)

    personas = subparsers.add_parser("personas", help="build evidence-derived Persona candidates")
    personas.add_argument("--seed", type=Path, required=True)
    personas.add_argument("--manifest", type=Path, required=True)
    personas.add_argument("--output-dir", type=Path, required=True)
    personas.add_argument("--zep-api-key", default=None)
    personas.add_argument("--max-agents", type=int, default=35)

    approve = subparsers.add_parser("personas-approve", help="approve evidence-derived Persona candidates")
    approve.add_argument("--input", type=Path, required=True)
    approve.add_argument("--output", type=Path, required=True)
    approve.add_argument("--persona-id", action="append", default=None)

    analyze = subparsers.add_parser("analyze-run", help="analyze a completed simulation run")
    analyze.add_argument("--seed", type=Path, required=True)
    analyze.add_argument("--run", type=Path, required=True)
    analyze.add_argument("--holdout", type=Path, default=None)
    analyze.add_argument("--output-dir", type=Path, required=True)

    simulation = subparsers.add_parser("simulation", help="manage an OASIS simulation run")
    simulation_sub = simulation.add_subparsers(dest="simulation_command", required=True)
    create = simulation_sub.add_parser("create")
    create.add_argument("--case", required=True)
    create.add_argument("--seed", type=Path, required=True)
    create.add_argument("--personas", type=Path, required=True)
    create.add_argument("--manifest", type=Path, required=True)
    create.add_argument("--zep-api-key", default=None)
    create.add_argument("--artifacts-root", type=Path, default=Path("artifacts"))
    create.add_argument("--rounds", type=int, default=4)
    create.add_argument("--random-seed", type=int, default=42)
    create.add_argument("--minutes-per-round", type=int, default=60)
    create.add_argument("--start-hour", type=int, default=9)
    run = simulation_sub.add_parser("run", help="create, execute, and summarize a simulation")
    run.add_argument("--case", required=True)
    run.add_argument("--seed", type=Path, required=True)
    run.add_argument("--personas", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--zep-api-key", default=None)
    run.add_argument("--artifacts-root", type=Path, default=Path("artifacts"))
    run.add_argument("--rounds", type=int, default=4)
    run.add_argument("--random-seed", type=int, default=42)
    run.add_argument("--minutes-per-round", type=int, default=60)
    run.add_argument("--start-hour", type=int, default=9)
    start = simulation_sub.add_parser("start")
    start.add_argument("run_dir", type=Path)
    start.add_argument("--foreground", action="store_true")
    status = simulation_sub.add_parser("status")
    status.add_argument("run_dir", type=Path)
    stop = simulation_sub.add_parser("stop")
    stop.add_argument("run_dir", type=Path)

    graph_view = subparsers.add_parser("graph-view", help="export web-ready graph JSON")
    graph_view_sub = graph_view.add_subparsers(dest="graph_view_command", required=True)
    evidence_view = graph_view_sub.add_parser("evidence")
    evidence_view.add_argument("--manifest", type=Path, required=True)
    evidence_view.add_argument("--seed", type=Path, default=None)
    evidence_view.add_argument("--zep-api-key", default=None)
    interaction_view = graph_view_sub.add_parser("interactions")
    interaction_view.add_argument("--run-dir", type=Path, required=True)
    interaction_view.add_argument("--personas", type=Path, required=True)
    interaction_view.add_argument("--round", type=int, default=None)

    analysis_view = subparsers.add_parser("analysis-view", help="export lightweight dashboard metrics")
    analysis_view.add_argument("--run-dir", type=Path, required=True)
    analysis_view.add_argument("--personas", type=Path, required=True)
    return parser


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _zep_config(args: argparse.Namespace, *, real_version: str = "v1") -> ZepConfig:
    api_key = getattr(args, "zep_api_key", None) or os.getenv("ZEP_API_KEY")
    if not api_key:
        raise SystemExit("ZEP_API_KEY or --zep-api-key is required")
    return ZepConfig(api_key=api_key, graph_version=real_version)


def _writer_for_manifest(args: argparse.Namespace, manifest: dict) -> ZepGraphWriter:
    version = str(manifest.get("group_id") or "").rsplit(":", 1)[-1] or "v1"
    return ZepGraphWriter(_zep_config(args, real_version=version))


def _require_zep(args: argparse.Namespace, manifest_path: Path) -> dict:
    manifest = _read_json(manifest_path)
    writer = _writer_for_manifest(args, manifest)
    try:
        status = writer.require_ready(manifest)
    except ZepGraphNotReady as exc:
        raise SystemExit(str(exc)) from exc
    if manifest.get("status") != "complete":
        manifest["status"] = "complete"
        manifest["zep_nodes"] = status["nodes"]
        manifest["zep_edges"] = status["edges"]
        _write_json(manifest_path, manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = _parser().parse_args(argv)
    if args.command == "analysis-view":
        run_dir = args.run_dir.resolve()
        payload = build_visualization_analysis(
            _read_json(run_dir / "simulation_run.json"),
            _read_json(args.personas),
            run_dir / "oasis.db",
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "graph-view":
        if args.graph_view_command == "evidence":
            manifest = _read_json(args.manifest)
            writer = _writer_for_manifest(args, manifest)
            seed = _read_json(args.seed) if args.seed else None
            try:
                payload = writer.export_evidence_graph(manifest, seed)
            except ZepGraphNotReady as exc:
                raise SystemExit(str(exc)) from exc
        else:
            run_dir = args.run_dir.resolve()
            payload = build_interaction_graph(
                _read_json(run_dir / "simulation_run.json"),
                _read_json(args.personas),
                run_dir / "oasis.db",
                up_to_round=args.round,
            )
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.command == "simulation":
        manager = SimulationManager(args.artifacts_root if hasattr(args, "artifacts_root") else Path("artifacts"))
        if args.simulation_command in {"create", "run"}:
            _require_zep(args, args.manifest)
            seed = _read_json(args.seed)
            personas = _read_json(args.personas)
            run_dir = manager.create(
                case_id=args.case,
                seed=seed,
                personas=personas,
                rounds=args.rounds,
                random_seed=args.random_seed,
                minutes_per_round=args.minutes_per_round,
                start_hour=args.start_hour,
            )
            if args.simulation_command == "run":
                state = manager.start(run_dir, foreground=True)
                print(json.dumps({"run_dir": str(run_dir), "state": state}, ensure_ascii=False, indent=2))
                return 0 if state.get("status") == "completed" else 1
            print(run_dir)
            return 0
        if args.simulation_command == "start":
            print(json.dumps(manager.start(args.run_dir, foreground=args.foreground), ensure_ascii=False, indent=2))
            return 0
        if args.simulation_command == "status":
            print(json.dumps(manager.status(args.run_dir), ensure_ascii=False, indent=2))
            return 0
        if args.simulation_command == "stop":
            print(json.dumps(manager.stop(args.run_dir), ensure_ascii=False, indent=2))
            return 0
    if args.command == "graph-init":
        writer = ZepGraphWriter(_zep_config(args, real_version=args.real_version))
        try:
            writer.initialize(
                _read_json(args.seed),
                args.manifest,
                wait_seconds=args.wait_seconds,
            )
        except ZepGraphNotReady as exc:
            raise SystemExit(str(exc)) from exc
        return 0
    if args.command == "graph-status":
        manifest = _read_json(args.manifest)
        writer = _writer_for_manifest(args, manifest)
        try:
            status = writer.require_ready(manifest)
        except ZepGraphNotReady as exc:
            print(json.dumps({"ready": False, "detail": str(exc)}, ensure_ascii=False))
            return 1
        manifest["status"] = "complete"
        manifest["zep_nodes"] = status["nodes"]
        manifest["zep_edges"] = status["edges"]
        _write_json(args.manifest, manifest)
        print(json.dumps(status, ensure_ascii=False))
        return 0
    if args.command == "graph-query":
        manifest = _require_zep(args, args.manifest)
        writer = _writer_for_manifest(args, manifest)
        writer.acceptance_queries(manifest, args.output)
        return 0
    if args.command == "personas":
        manifest = _require_zep(args, args.manifest)
        seed = _read_json(args.seed)
        writer = _writer_for_manifest(args, manifest)
        graph = writer.export_evidence_graph(manifest, seed)
        selection = ActorSelector().select(
            nodes=graph.get("nodes") or [],
            edges=graph.get("edges") or [],
            claims=seed.get("claims") or [],
            max_agents=clamp_max_agents(args.max_agents),
        )
        payload = PersonaBuilder().build(
            seed,
            manifest,
            graph,
            selected_actor_ids=selection["entity_ids"],
        )
        payload["selection"] = selection
        write_persona_artifacts(payload, args.output_dir)
        return 0
    if args.command == "personas-approve":
        payload = approve_personas(_read_json(args.input), args.persona_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(args.output)
        return 0
    if args.command == "analyze-run":
        seed = _read_json(args.seed)
        run = _read_json(args.run)
        result = ResultAnalyzer().analyze(run, seed)
        evaluation = None
        if args.holdout is not None:
            holdout = json.loads(args.holdout.read_text(encoding="utf-8"))
            if not isinstance(holdout, list):
                raise SystemExit("--holdout must contain a JSON array")
            evaluation = HoldoutEvaluator().evaluate(result=result, holdout_items=holdout)
        write_result_artifacts(result=result, evaluation=evaluation, output_dir=args.output_dir)
        return 0

    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    try:
        as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"invalid --as-of value: {args.as_of}") from exc
    scenario = ScenarioConfig(
        as_of=as_of,
        horizon_hours=args.horizon_hours,
        question=args.question,
        allow_undated_social=args.allow_undated_social,
    )
    loader = CaseLoader(MyAgentRepositoryAdapter(database_url))
    CaseService(loader, SeedBuilder(scenario)).build(args.case, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
