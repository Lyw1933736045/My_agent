"""FastAPI routes for the case simulation workspace."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .run_repository import RunRepository
from .simulation_bridge import SimulationBridge, SimulationBridgeError
from .tools.knowledge_indexer import SIMULATION_PG_TYPES, index_simulation_safely
from .utils.config import Settings


router = APIRouter(prefix="/api/v1/simulation", tags=["simulation"])
_settings = Settings()
_repository = RunRepository(_settings.DATABASE_URL)
bridge = SimulationBridge()
bridge.database_url = _settings.DATABASE_URL


class StartSimulationRequest(BaseModel):
    rounds: int = Field(default=4, ge=1, le=48)


class StartGraphRequest(BaseModel):
    question: str = Field(min_length=8, max_length=500)
    as_of: str = Field(min_length=10, max_length=64)
    horizon_hours: int = Field(default=48, ge=1, le=168)
    allow_undated_social: bool = True
    max_agents: int = Field(default=35, ge=2, le=35)


def _call(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except SimulationBridgeError as exc:
        message = str(exc)
        lowered = message.lower()
        if "正在生成" in message:
            status = 409
        elif "not found" in lowered or "不存在" in message:
            status = 404
        else:
            status = 400
        raise HTTPException(status_code=status, detail=message) from exc


def _resolve(case_ref: str) -> tuple[object, str]:
    case = _repository.get_case(case_ref)
    if case is None:
        raise HTTPException(status_code=404, detail="研究案例不存在，请从简报工作区进入。")
    artifact_ref = bridge.artifact_ref_for(case.case_key, case.case_id)
    return case, artifact_ref


def _with_case(case, payload: dict) -> dict:
    payload = dict(payload)
    payload.update({
        "case_id": case.case_id,
        "case_key": case.case_key,
        "topic": case.topic,
        "query": case.query,
        "has_brief": bool(case.report or case.report_data),
    })
    return payload


@router.get("/cases")
def list_simulation_cases() -> list[dict]:
    return _call(bridge.list_cases)


@router.get("/cases/{case_ref}/overview")
def simulation_overview(case_ref: str) -> dict:
    case, artifact_ref = _resolve(case_ref)
    return _with_case(case, _call(bridge.overview, artifact_ref))


@router.post("/cases/{case_ref}/graph", status_code=202)
def start_graph_build(case_ref: str, body: StartGraphRequest) -> dict:
    case, artifact_ref = _resolve(case_ref)
    if not (case.report or case.report_data):
        raise HTTPException(status_code=409, detail="当前案例还没有简报，无法生成知识图谱")
    job = _call(
        bridge.start_graph_build,
        artifact_ref,
        question=body.question,
        as_of=body.as_of,
        horizon_hours=body.horizon_hours,
        allow_undated_social=body.allow_undated_social,
        source_case=case.case_id,
        max_agents=body.max_agents,
    )
    return _with_case(case, {"job": job, **bridge.overview(artifact_ref)})


@router.get("/cases/{case_ref}/graph/job")
def graph_job(case_ref: str) -> dict:
    case, artifact_ref = _resolve(case_ref)
    return _with_case(case, {"job": bridge.read_job(artifact_ref), **bridge.overview(artifact_ref)})


@router.get("/cases/{case_ref}/graph/evidence")
def evidence_graph(case_ref: str) -> dict:
    _case, artifact_ref = _resolve(case_ref)
    return _call(bridge.evidence_graph, artifact_ref)


@router.get("/cases/{case_ref}/runs")
def list_simulation_runs(case_ref: str) -> list[dict]:
    _case, artifact_ref = _resolve(case_ref)
    return _call(bridge.list_runs, artifact_ref)


@router.post("/cases/{case_ref}/runs", status_code=202)
def start_simulation(case_ref: str, body: StartSimulationRequest) -> dict:
    case, artifact_ref = _resolve(case_ref)
    state = _call(bridge.start, artifact_ref, body.rounds)
    try:
        _repository.replace_knowledge_chunks(
            case.case_id,
            [],
            source_types=list(SIMULATION_PG_TYPES),
        )
    except Exception:
        pass
    return state


@router.get("/cases/{case_ref}/runs/{simulation_id}")
def simulation_status(case_ref: str, simulation_id: str) -> dict:
    case, artifact_ref = _resolve(case_ref)
    state = _call(bridge.run_status, artifact_ref, simulation_id)
    if state.get("has_result") and state.get("status") in {"completed", "failed", "stopped"}:
        index_simulation_safely(
            _repository,
            case.case_id,
            bridge.run_dir(artifact_ref, simulation_id),
            simulation_id,
            _settings,
        )
    return state


@router.post("/cases/{case_ref}/runs/{simulation_id}/stop")
def stop_simulation(case_ref: str, simulation_id: str) -> dict:
    _case, artifact_ref = _resolve(case_ref)
    return _call(bridge.stop, artifact_ref, simulation_id)


@router.get("/cases/{case_ref}/runs/{simulation_id}/rounds")
def simulation_rounds(case_ref: str, simulation_id: str) -> dict:
    _case, artifact_ref = _resolve(case_ref)
    return _call(bridge.rounds, artifact_ref, simulation_id)


@router.get("/cases/{case_ref}/runs/{simulation_id}/analysis")
def simulation_analysis(case_ref: str, simulation_id: str) -> dict:
    _case, artifact_ref = _resolve(case_ref)
    return _call(bridge.analysis, artifact_ref, simulation_id)


@router.get("/cases/{case_ref}/runs/{simulation_id}/graph/interactions")
def interaction_graph(
    case_ref: str,
    simulation_id: str,
    round: int | None = Query(default=None, ge=0),
) -> dict:
    _case, artifact_ref = _resolve(case_ref)
    return _call(bridge.interaction_graph, artifact_ref, simulation_id, round)


@router.get("/cases/{case_ref}/runs/{simulation_id}/report")
def simulation_report(case_ref: str, simulation_id: str) -> dict:
    _case, artifact_ref = _resolve(case_ref)
    return _call(bridge.report, artifact_ref, simulation_id)
