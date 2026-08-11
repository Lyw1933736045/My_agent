"""执行 coverage 与 grounding 两个方向的 Judge。"""

import json
from pathlib import Path
import warnings

from pydantic import ValidationError

from ..utils.text_processing import extract_json
from .prompts import COVERAGE_JUDGE_PROMPT
from .schemas import (
    CoverageJudgeOutput,
    CoverageJudgment,
    Evidence,
    JudgeOutput,
    RubricSet,
)


def _contains(text: str, fragment: str) -> bool:
    return " ".join(fragment.split()) in " ".join(text.split())


def _validate_evidence(result: JudgeOutput, rubrics: RubricSet, documents: list[dict], report: str) -> None:
    rubric_ids = {item.id for item in rubrics.rubrics}
    urls = {str(item.get("url") or "") for item in documents}
    for item in result.coverage:
        if item.rubric_id not in rubric_ids:
            raise ValueError(f"Judge 返回未知 rubric_id：{item.rubric_id}")
        for evidence, source in ((item.retrieval, "retrieval"), (item.report, "report")):
            if evidence.score > 0 and not evidence.evidence:
                warnings.warn(f"{item.rubric_id} 的 {source} 非零评分缺少 evidence，已降为 0")
                evidence.score = 0.0
            if evidence.source_url and evidence.source_url not in urls:
                warnings.warn(f"{item.rubric_id} 的 source_url 不在输入材料中，已清空证据")
                evidence.score = 0.0
                evidence.evidence = None
                evidence.source_url = None
            if evidence.evidence:
                source_text = report if source == "report" else "\n".join(
                    str(doc.get("content") or "") for doc in documents
                )
                if not _contains(source_text, evidence.evidence):
                    warnings.warn(
                        f"{item.rubric_id} 的 {source} evidence 不是逐字片段，保留语义评分"
                    )
def _compact_documents(documents: list[dict], max_chars: int = 1800) -> list[dict]:
    """只保留有限证据片段，避免把完整网页正文再次发送给 Judge。"""
    return [
        {
            "title": item.get("title"),
            "source": item.get("source"),
            "url": item.get("url"),
            "snippet": item.get("snippet", ""),
            "content": str(item.get("content") or "")[:max_chars],
        }
        for item in documents
    ]


def _has_invalid_score(error: ValidationError) -> bool:
    return any(
        item["type"] == "literal_error" and item["loc"][-1:] == ("score",)
        for item in error.errors()
    )


def _parse_coverage(response: str) -> CoverageJudgeOutput:
    data = extract_json(response)
    try:
        return CoverageJudgeOutput.model_validate(data)
    except ValidationError as error:
        if not _has_invalid_score(error):
            raise

    repaired = []
    for index, item in enumerate(data.get("coverage", [])):
        try:
            repaired.append(CoverageJudgment.model_validate(item))
        except ValidationError as item_error:
            if not _has_invalid_score(item_error):
                raise
            rubric_id = str(item.get("rubric_id") or f"coverage[{index}]")
            warnings.warn(f"{rubric_id} 返回了非法 score，已将该项记录为 0")
            repaired.append(CoverageJudgment(
                rubric_id=rubric_id,
                retrieval=Evidence(score=0.0),
                report=Evidence(score=0.0),
            ))
    return CoverageJudgeOutput(coverage=repaired)


def evaluate(client, rubrics: RubricSet, documents: list[dict], report: str, progress=None) -> JudgeOutput:
    judge_rubrics = [
        {"id": item.id, "criterion": item.criterion, "reference_evidence": item.reference_evidence}
        for item in rubrics.rubrics
    ]
    rubric_payload = {
        "rubrics": judge_rubrics,
        "retrieved_documents": _compact_documents(documents),
        "report": report,
    }
    if progress:
        progress("Coverage Judge：处理 retrieval 与 report……")
    coverage_response = client.invoke(
        COVERAGE_JUDGE_PROMPT,
        json.dumps(rubric_payload, ensure_ascii=False),
    )
    try:
        coverage = CoverageJudgeOutput.model_validate(extract_json(coverage_response))
    except ValidationError as error:
        if not _has_invalid_score(error):
            raise
        if progress:
            progress("Coverage Judge 返回非法 score，正在重试一次……")
        coverage_response = client.invoke(
            COVERAGE_JUDGE_PROMPT,
            json.dumps(rubric_payload, ensure_ascii=False),
        )
        coverage = _parse_coverage(coverage_response)
    result = JudgeOutput(
        coverage=coverage.coverage,
        rubrics=list(rubrics.rubrics),
    )
    _validate_evidence(result, rubrics, documents, report)
    return result


def load_case(case_dir: Path) -> tuple[RubricSet, list[dict], str]:
    rubrics = RubricSet.model_validate_json((case_dir / "rubrics.json").read_text(encoding="utf-8"))
    documents = json.loads((case_dir / "retrieved_documents.json").read_text(encoding="utf-8"))
    report = (case_dir / "report.md").read_text(encoding="utf-8")
    if not isinstance(documents, list):
        raise ValueError("retrieved_documents.json 必须是数组")
    return rubrics, documents, report
