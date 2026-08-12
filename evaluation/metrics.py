"""评测指标的确定性汇总。"""

from collections import Counter

from .schemas import JudgeOutput


def _coverage(
    items,
    field: str,
    rubric_by_id: dict,
    importance: str | None = None,
    *,
    empty: float = 0.0,
) -> float:
    if importance:
        items = [
            item for item in items
            if rubric_by_id[item.rubric_id].importance == importance
        ]
    if not items:
        return empty
    return round(sum(getattr(item, field).score for item in items) / len(items) * 100, 1)


def summarize(result: JudgeOutput) -> dict:
    rubric_by_id = {item.id: item for item in result.rubrics}
    retrieval = _coverage(result.coverage, "retrieval", rubric_by_id)
    report = _coverage(result.coverage, "report", rubric_by_id)
    core_retrieval = _coverage(result.coverage, "retrieval", rubric_by_id, "core")
    core_report = _coverage(result.coverage, "report", rubric_by_id, "core")
    important_retrieval = _coverage(
        result.coverage, "retrieval", rubric_by_id, "important", empty=100.0
    )
    important_report = _coverage(
        result.coverage, "report", rubric_by_id, "important", empty=100.0
    )
    diagnosis = Counter()
    for item in result.coverage:
        retrieval_score = item.retrieval.score
        report_score = item.report.score
        if retrieval_score > 0 and report_score > 0:
            diagnosis["correctly_covered"] += 1
        elif retrieval_score == 0 and report_score == 0:
            diagnosis["retrieval_miss"] += 1
        elif retrieval_score > 0 and report_score == 0:
            diagnosis["summarization_miss"] += 1
        else:
            diagnosis["potential_unsupported"] += 1
    bonus_points = min(
        5.0,
        sum(
            0.5 if item.report.score == 1.0 else 0.25
            for item in result.coverage
            if rubric_by_id[item.rubric_id].importance == "bonus" and item.report.score > 0
        ),
    )
    overall_base = round(
        (0.60 * core_report + 0.25 * important_report) / 0.85,
        1,
    )
    # 向前端提供报告已经覆盖的 Reference 信息点。criterion 是从
    # reference.md 拆出的 atomic rubric，比直接展示长篇原文更便于核对。
    importance_order = {"core": 0, "important": 1, "bonus": 2}
    reference_hits = []
    for index, item in enumerate(result.coverage):
        rubric = rubric_by_id.get(item.rubric_id)
        if rubric is None or item.report.score <= 0:
            continue
        reference_hits.append({
            "rubric_id": rubric.id,
            "criterion": rubric.criterion,
            "importance": rubric.importance,
            "report_score": item.report.score,
            "evidence": item.report.evidence,
            "source_url": item.report.source_url,
            "_index": index,
        })
    reference_hits.sort(
        key=lambda item: (
            importance_order.get(item["importance"], 99),
            item["_index"],
        )
    )
    for item in reference_hits:
        item.pop("_index", None)
    return {
        "retrieval_coverage": retrieval,
        "report_coverage": report,
        "core_retrieval_coverage": core_retrieval,
        "core_report_coverage": core_report,
        "important_retrieval_coverage": important_retrieval,
        "important_report_coverage": important_report,
        "overall_base_score": overall_base,
        "bonus_points": bonus_points,
        "overall_score": min(100.0, round(overall_base + bonus_points, 1)),
        "reference_hit_count": len(reference_hits),
        "reference_total_count": len(result.rubrics),
        "reference_hits": reference_hits,
        "diagnosis": {
            "correctly_covered": diagnosis["correctly_covered"],
            "retrieval_miss": diagnosis["retrieval_miss"],
            "summarization_miss": diagnosis["summarization_miss"],
            "potential_unsupported": diagnosis["potential_unsupported"],
        },
    }
