import unittest
import json

from My_agent.evaluation.judge import evaluate
from My_agent.evaluation.metrics import summarize
from My_agent.evaluation.schemas import (
    CoverageJudgeOutput,
    CoverageJudgment,
    Evidence,
    JudgeOutput,
    Rubric,
    RubricSet,
)


class FakeJudgeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def invoke(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return next(self.responses)


class EvaluationTests(unittest.TestCase):
    def test_coverage_output_requires_both_scores(self):
        coverage = CoverageJudgeOutput.model_validate({
            "coverage": [{
                "rubric_id": "R1",
                "retrieval": {"score": 0.0},
                "report": {"score": 1.0, "evidence": "事实"},
            }],
        })
        self.assertEqual(len(coverage.coverage), 1)

    def test_invalid_score_retries_once_then_records_zero(self):
        invalid_coverage = json.dumps({
            "coverage": [{
                "rubric_id": "R1",
                "retrieval": {"score": 0.8},
                "report": {"score": 1.0, "evidence": "事实"},
            }],
        })
        client = FakeJudgeClient([
            invalid_coverage,
            invalid_coverage,
        ])
        rubrics = RubricSet(rubrics=[Rubric(
            id="R1", criterion="事实", reference_evidence="事实",
            importance="core", importance_reason="核心事实",
        )])
        with self.assertWarnsRegex(UserWarning, "非法 score"):
            result = evaluate(client, rubrics, [], "事实")

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result.coverage[0].retrieval.score, 0.0)
        self.assertEqual(result.coverage[0].report.score, 0.0)
        coverage_payload = json.loads(client.calls[0][1].split("\n", 1)[-1])
        self.assertNotIn("importance", coverage_payload["rubrics"][0])

    def test_metrics_and_diagnosis(self):
        result = JudgeOutput(
            rubrics=[
                Rubric(
                    id="R1", criterion="事实", reference_evidence="事实",
                    importance="core", importance_reason="核心事实",
                ),
                Rubric(
                    id="R2", criterion="事实", reference_evidence="事实",
                    importance="important", importance_reason="重要背景",
                ),
            ],
            coverage=[
                CoverageJudgment(
                    rubric_id="R1",
                    retrieval=Evidence(score=1.0, evidence="a"),
                    report=Evidence(score=1.0, evidence="b"),
                ),
                CoverageJudgment(
                    rubric_id="R2",
                    retrieval=Evidence(score=1.0, evidence="a"),
                    report=Evidence(score=0.0),
                ),
            ],
        )
        summary = summarize(result)
        self.assertEqual(summary["retrieval_coverage"], 100.0)
        self.assertEqual(summary["report_coverage"], 50.0)
        self.assertEqual(summary["diagnosis"]["summarization_miss"], 1)
        self.assertEqual(summary["reference_hit_count"], 1)
        self.assertEqual(summary["reference_total_count"], 2)
        self.assertEqual(summary["reference_hits"][0]["rubric_id"], "R1")
        self.assertEqual(summary["reference_hits"][0]["criterion"], "事实")
        self.assertEqual(summary["reference_hits"][0]["report_score"], 1.0)


if __name__ == "__main__":
    unittest.main()
