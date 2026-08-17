"""FastAPI routes for the case simulation workspace."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .simulation_bridge import SimulationBridge, SimulationBridgeError


router = APIRouter(prefix="/api/v1/simulation", tags=["simulation"])
bridge = SimulationBridge()


class StartSimulationRequest(BaseModel):
    rounds: int = Field(default=4, ge=1, le=48)


def _call(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except SimulationBridgeError as exc:
        message = str(exc)
        status = 404 if "not found" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc


@router.get("/cases")
def list_simulation_cases() -> list[dict]:
    return _call(bridge.list_cases)


@router.get("/cases/{case_ref}/overview")
def simulation_overview(case_ref: str) -> dict:
    return _call(bridge.overview, case_ref)


@router.get("/cases/{case_ref}/graph/evidence")
def evidence_graph(case_ref: str) -> dict:
    return _call(bridge.evidence_graph, case_ref)


@router.get("/cases/{case_ref}/runs")
def list_simulation_runs(case_ref: str) -> list[dict]:
    return _call(bridge.list_runs, case_ref)


@router.post("/cases/{case_ref}/runs", status_code=202)
def start_simulation(case_ref: str, body: StartSimulationRequest) -> dict:
    return _call(bridge.start, case_ref, body.rounds)


@router.get("/cases/{case_ref}/runs/{simulation_id}")
def simulation_status(case_ref: str, simulation_id: str) -> dict:
    return _call(bridge.run_status, case_ref, simulation_id)


@router.post("/cases/{case_ref}/runs/{simulation_id}/stop")
def stop_simulation(case_ref: str, simulation_id: str) -> dict:
    return _call(bridge.stop, case_ref, simulation_id)


@router.get("/cases/{case_ref}/runs/{simulation_id}/rounds")
def simulation_rounds(case_ref: str, simulation_id: str) -> dict:
    return _call(bridge.rounds, case_ref, simulation_id)


@router.get("/cases/{case_ref}/runs/{simulation_id}/analysis")
def simulation_analysis(case_ref: str, simulation_id: str) -> dict:
    return _call(bridge.analysis, case_ref, simulation_id)


@router.get("/cases/{case_ref}/runs/{simulation_id}/graph/interactions")
def interaction_graph(
    case_ref: str,
    simulation_id: str,
    round: int | None = Query(default=None, ge=0),
) -> dict:
    return _call(bridge.interaction_graph, case_ref, simulation_id, round)


@router.get("/cases/{case_ref}/runs/{simulation_id}/report")
def simulation_report(case_ref: str, simulation_id: str) -> dict:
    return _call(bridge.report, case_ref, simulation_id)
