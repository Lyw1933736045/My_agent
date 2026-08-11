"""评测数据结构。"""

from typing import Literal

from pydantic import BaseModel, Field


Score = Literal[0.0, 0.5, 1.0]
Importance = Literal["core", "important", "bonus"]


class Rubric(BaseModel):
    id: str
    category: str = "general"
    criterion: str
    reference_evidence: str
    reference_source: list[str] = Field(default_factory=list)
    importance: Importance
    importance_reason: str


class RubricSet(BaseModel):
    rubrics: list[Rubric]


class Evidence(BaseModel):
    score: Score
    evidence: str | None = None
    source_url: str | None = None


class CoverageJudgment(BaseModel):
    rubric_id: str
    retrieval: Evidence
    report: Evidence


class ClaimJudgment(BaseModel):
    claim: str
    claim_type: Literal["fact", "attributed_opinion"]
    evidence_status: Literal["supported", "partial", "unsupported"]
    evidence: str | None = None
    source_url: str | None = None
    reference_match: bool = False
    matched_rubric_id: str | None = None
    extra_type: Literal["useful_extra", "minor_extra", "none"] = "none"


class CoverageJudgeOutput(BaseModel):
    coverage: list[CoverageJudgment] = Field(default_factory=list)


class JudgeOutput(BaseModel):
    coverage: list[CoverageJudgment] = Field(default_factory=list)
    # Metrics 需要 rubric 的 importance；Judge 输出本身不重复 rubric 定义。
    rubrics: list[Rubric] = Field(default_factory=list)
