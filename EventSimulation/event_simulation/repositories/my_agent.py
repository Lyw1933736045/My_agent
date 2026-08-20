"""Gate 1: read an existing My_agent case into an isolated CaseBundle."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Protocol

from ..models.case import CaseBundle, jsonable


class CaseRepository(Protocol):
    def get_case(self, case_ref: str) -> Any: ...
    def aggregate_case_prepared_analysis(self, case_ref: str) -> dict | None: ...
    def list_case_candidates(self, case_ref: str) -> list[dict]: ...

    def list_candidates(self, run_id: str) -> list[dict]: ...


class MyAgentRepositoryAdapter:
    """Narrow adapter that exposes only the repository's existing read methods."""

    def __init__(self, database_url: str) -> None:
        try:
            from financial_single_agent.run_repository import RunRepository
        except ImportError:
            try:
                from My_agent.run_repository import RunRepository
            except ImportError as exc:
                raise RuntimeError(
                    "My_agent is not importable; install it or add its parent directory "
                    "to PYTHONPATH"
                ) from exc
        self._repository = RunRepository(database_url)

    def get_case(self, case_ref: str) -> Any:
        return self._repository.get_case(case_ref)

    def aggregate_case_prepared_analysis(self, case_ref: str) -> dict | None:
        return self._repository.aggregate_case_prepared_analysis(case_ref)

    def list_case_candidates(self, case_ref: str) -> list[dict]:
        return self._repository.list_case_candidates(case_ref)

    def list_candidates(self, run_id: str) -> list[dict]:
        return self._repository.list_candidates(run_id)


class CaseLoader:
    def __init__(self, repository: CaseRepository) -> None:
        self.repository = repository

    def load(self, case_ref: str) -> CaseBundle:
        case_record = self.repository.get_case(case_ref)
        if case_record is None:
            raise LookupError(f"case not found: {case_ref}")
        raw_case = asdict(case_record) if is_dataclass(case_record) else dict(case_record)
        raw_case = jsonable(raw_case)
        child_runs = list(raw_case.pop("child_runs", []) or [])
        prepared = self.repository.aggregate_case_prepared_analysis(case_ref) or {}
        accepted = self.repository.list_case_candidates(case_ref)
        all_documents = self._all_case_documents(child_runs, accepted)
        brief_data = dict(raw_case.get("report_data") or {})
        if not brief_data:
            brief_data = dict(raw_case.get("metadata", {}).get("brief_data") or {})
        sources = list(brief_data.get("sources") or [])
        warnings = self._quality_warnings(child_runs, accepted, prepared, brief_data)
        return CaseBundle(
            case=raw_case,
            child_runs=jsonable(child_runs),
            accepted_documents=accepted,
            prepared_analysis=prepared,
            brief_data=brief_data,
            source_catalog=sources,
            all_case_documents=all_documents,
            quality_warnings=warnings,
        )

    def _all_case_documents(
        self,
        child_runs: list[dict[str, Any]],
        accepted: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Read all child-run candidates for the Gate 1 count, without serializing bodies."""
        list_candidates = getattr(self.repository, "list_candidates", None)
        if not callable(list_candidates):
            return list(accepted)
        merged: dict[str, dict[str, Any]] = {}
        for child in child_runs:
            run_id = str(child.get("run_id") or "")
            if not run_id:
                continue
            for row in list_candidates(run_id) or []:
                key = str(row.get("canonical_url") or row.get("url") or row.get("document_id") or "")
                if not key:
                    continue
                current = merged.get(key)
                if current is None:
                    merged[key] = {
                        **row,
                        "case_run_ids": [run_id],
                    }
                elif run_id not in current.get("case_run_ids", []):
                    current["case_run_ids"].append(run_id)
        return list(merged.values()) or list(accepted)

    @staticmethod
    def _quality_warnings(
        child_runs: list[dict],
        accepted: list[dict],
        prepared: dict,
        brief_data: dict,
    ) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        if not child_runs:
            warnings.append({"code": "NO_CHILD_RUNS", "message": "case has no child runs"})
        if not accepted:
            warnings.append({"code": "NO_ACCEPTED_DOCUMENTS", "message": "no accepted documents with content"})
        if not prepared:
            warnings.append({"code": "NO_PREPARED_ANALYSIS", "message": "prepared_analysis is empty"})
        if not brief_data:
            warnings.append({"code": "NO_BRIEF_DATA", "message": "case brief_data is empty"})
        undated_documents = [str(item.get("document_id") or "") for item in accepted if not item.get("published_at")]
        if undated_documents:
            warnings.append({
                "code": "UNDATED_ACCEPTED_DOCUMENTS",
                "count": len(undated_documents),
                "document_ids": undated_documents,
            })
        undated_sources = [str(item.get("id") or "") for item in brief_data.get("sources") or [] if not item.get("published_at")]
        if undated_sources:
            warnings.append({
                "code": "UNDATED_BRIEF_SOURCES",
                "count": len(undated_sources),
                "brief_source_ids": undated_sources,
            })
        return warnings
