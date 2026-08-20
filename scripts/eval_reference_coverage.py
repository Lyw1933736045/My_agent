"""Offline reference coverage for one brief. Does not write to Postgres or the frontend.

Each reference section: retrieve brief Top3 → four-dimension scores
(deepseek-v4-flash, 3 independent runs, average).

    python3 scripts/eval_reference_coverage.py --case-key case1
"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import math
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from My_agent.evaluation.prompts import REFERENCE_SECTION_JUDGE_PROMPT
from My_agent.llms import LLMClient
from My_agent.run_repository import RunRepository
from My_agent.tools.embedding_service import EmbeddingService
from My_agent.utils.config import Settings
from My_agent.utils.text_processing import extract_json

HIT = 70
PARTIAL = 45
DIMENSIONS = ("relevance", "accuracy", "completeness", "usefulness")
EMPTY_SCORES = {key: 0 for key in DIMENSIONS}

SECTION_MARKERS = [
    ("境内·总览", "境内媒体方面"),
    ("要点·一", "一是"),
    ("要点·二", "二是"),
    ("要点·三", "三是"),
    ("要点·四", "四是"),
    ("要点·五", "五是"),
    ("境外媒体", "境外媒体方面"),
    ("境外·港交所陈翊庭", "香港交易所集团行政总裁"),
    ("自媒体", "财经自媒体和网民方面"),
    ("自媒体", "自媒体及网民"),
]


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    na = math.sqrt(sum(a * a for a in left))
    nb = math.sqrt(sum(b * b for b in right))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def preview(text: str, limit: int = 72) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def fmt_score(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def resolve_reference_path(case_key: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    folders = [case_key]
    match = re.fullmatch(r"case(\d+)", case_key)
    if match:
        folders.append(f"case_{match.group(1)}")
    for folder in folders:
        path = ROOT / "data" / "evaluation_cases" / folder / "reference.md"
        if path.exists():
            return path
    return ROOT / "data" / "evaluation_cases" / case_key / "reference.md"


def split_reference(text: str) -> list[dict]:
    starts = []
    used_labels = set()
    for label, needle in SECTION_MARKERS:
        if label in used_labels:
            continue
        index = text.find(needle)
        if index >= 0:
            starts.append((index, label))
            used_labels.add(label)
    starts.sort()
    sections = []
    if starts and starts[0][0] > 0:
        lead = text[: starts[0][0]].strip()
        if len(lead) >= 20:
            sections.append({"id": "开篇", "text": lead})
    for index, (start, label) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append({"id": label, "text": body})
    if not sections:
        chunks = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        if len(chunks) <= 1:
            return [{"id": "全文", "text": text.strip()}]
        return [{"id": f"段{index}", "text": chunk} for index, chunk in enumerate(chunks, 1)]
    return sections


def _add_unit(units: list[dict], location: str, text: str) -> None:
    value = " ".join(str(text or "").split())
    if len(value) < 8:
        return
    units.append({"location": location, "text": value[:1800]})


def flatten_brief(report_data: dict) -> list[dict]:
    units: list[dict] = []
    data = report_data if isinstance(report_data, dict) else {}
    _add_unit(units, "title", str(data.get("title") or ""))
    for index, item in enumerate(data.get("executive_summary") or [], 1):
        if isinstance(item, dict):
            _add_unit(units, f"executive_summary[{index}]", item.get("text") or json.dumps(item, ensure_ascii=False))
        else:
            _add_unit(units, f"executive_summary[{index}]", str(item))

    def walk_section(prefix: str, section: dict) -> None:
        if not isinstance(section, dict):
            return
        _add_unit(units, f"{prefix}.overview", section.get("overview") or "")
        for index, topic in enumerate(section.get("topics") or [], 1):
            if not isinstance(topic, dict):
                continue
            loc = f"{prefix}.topics[{index}] {topic.get('title') or ''}".strip()
            parts = [topic.get("title"), topic.get("summary")]
            for view in topic.get("supporting_views") or []:
                if isinstance(view, dict):
                    parts.append(" ".join(str(view.get(key) or "") for key in ("speaker", "organization", "point")))
            for view in topic.get("social_views") or []:
                if isinstance(view, dict):
                    parts.append(" ".join(str(view.get(key) or "") for key in ("account", "point")))
            _add_unit(units, loc, "；".join(part for part in parts if part))

    walk_section("official", data.get("official") or {})
    media = data.get("media") if isinstance(data.get("media"), dict) else {}
    walk_section("media.domestic", media.get("domestic") or {})
    walk_section("media.overseas", media.get("overseas") or {})
    walk_section("public_opinion", data.get("public_opinion") or {})
    for index, item in enumerate(data.get("timeline") or [], 1):
        if isinstance(item, dict):
            _add_unit(units, f"timeline[{index}]", f"{item.get('date') or ''} {item.get('event') or ''}")
    for index, item in enumerate(data.get("key_metrics") or [], 1):
        if isinstance(item, dict):
            _add_unit(
                units,
                f"key_metrics[{index}]",
                f"{item.get('label') or ''} {item.get('value') or ''} {item.get('context') or ''}",
            )
    synthesis = data.get("synthesis") if isinstance(data.get("synthesis"), dict) else {}
    for key in ("consensus", "differences", "risks", "watch_points"):
        for index, item in enumerate(synthesis.get(key) or [], 1):
            text = item.get("text") if isinstance(item, dict) else str(item)
            _add_unit(units, f"synthesis.{key}[{index}]", text)
    return units


def top_matches(
    query_vec: list[float],
    units: list[dict],
    vectors: list[list[float]],
    top_k: int = 3,
) -> list[dict]:
    if not units:
        return []
    scored = [(cosine(query_vec, vector), unit) for unit, vector in zip(units, vectors)]
    scored.sort(key=lambda item: item[0], reverse=True)
    matches = []
    for score, unit in scored[: max(1, top_k)]:
        matches.append({
            "location": unit["location"],
            "text": unit["text"],
            "score": score,
        })
    return matches


def clamp_score(value, default: int = 0) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(0, min(25, number))


def parse_judge_payload(payload) -> dict:
    if not isinstance(payload, dict):
        payload = {}
    scores = {key: clamp_score(payload.get(key)) for key in DIMENSIONS}
    scores["comment"] = str(payload.get("comment") or "").strip()
    scores["total"] = sum(scores[key] for key in DIMENSIONS)
    return scores


def judge_once(
    client: LLMClient,
    ref_id: str,
    ref_text: str,
    brief_matches: list[dict],
    temperature: float,
) -> dict:
    brief_block = "\n".join(
        f"{index}. [{item['location']}] {item['text']}"
        for index, item in enumerate(brief_matches, 1)
    )
    user_prompt = (
        f"参考段落标题：{ref_id}\n\n参考段落：\n{ref_text}\n\n简报 Top3：\n{brief_block}"
    )
    last_error = None
    for _attempt in range(2):
        try:
            raw = client.invoke(REFERENCE_SECTION_JUDGE_PROMPT, user_prompt, temperature=temperature)
            return parse_judge_payload(extract_json(raw))
        except Exception as error:
            last_error = error
    raise RuntimeError(f"{ref_id} 评测解析失败：{last_error}") from last_error


def average_runs(runs: list[dict]) -> dict:
    if not runs:
        result = dict(EMPTY_SCORES)
        result.update({"total": 0.0, "comment": "三次打分均失败", "runs": []})
        return result
    count = len(runs)
    averaged = {key: round(sum(item[key] for item in runs) / count, 1) for key in DIMENSIONS}
    averaged["total"] = round(sum(averaged[key] for key in DIMENSIONS), 1)
    closest = min(runs, key=lambda item: abs(item["total"] - averaged["total"]))
    averaged["comment"] = closest.get("comment") or ""
    averaged["runs"] = runs
    return averaged


def judge_group(
    client: LLMClient,
    ref_id: str,
    ref_text: str,
    brief_matches: list[dict],
    runs: int,
    temperature: float,
) -> dict:
    if not brief_matches:
        result = dict(EMPTY_SCORES)
        result.update({"total": 0.0, "comment": "简报无召回块", "runs": []})
        return result
    collected = []
    for index in range(max(1, runs)):
        try:
            collected.append(judge_once(client, ref_id, ref_text, brief_matches, temperature))
        except Exception as error:
            print(f"  第 {index + 1} 次打分失败：{error}", flush=True)
    return average_runs(collected)


def verdict_from_total(total: float) -> str:
    if total >= HIT:
        return "命中"
    if total >= PARTIAL:
        return "部分"
    return "弱覆盖"


def build_judge_client(settings: Settings) -> LLMClient:
    use_query_engine = settings.JUDGE_USE_QUERY_ENGINE_API
    api_key = (
        settings.QUERY_ENGINE_API_KEY
        if use_query_engine
        else (settings.JUDGE_API_KEY or settings.QUERY_ENGINE_API_KEY)
    )
    base_url = (
        settings.QUERY_ENGINE_BASE_URL
        if use_query_engine
        else (settings.JUDGE_BASE_URL or settings.QUERY_ENGINE_BASE_URL)
    )
    model_name = settings.JUDGE_MODEL_NAME or "deepseek-v4-flash"
    if not api_key:
        raise ValueError("评测缺少 API Key：请设置 JUDGE_API_KEY 或 QUERY_ENGINE_API_KEY")
    return LLMClient(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        timeout=settings.JUDGE_LLM_REQUEST_TIMEOUT or settings.LLM_REQUEST_TIMEOUT,
        trust_env=False,
    )


def render(rows: list[dict], meta: dict) -> str:
    hits = sum(1 for row in rows if row["verdict"] == "命中")
    partial = sum(1 for row in rows if row["verdict"] == "部分")
    weak = sum(1 for row in rows if row["verdict"] == "弱覆盖")
    overall = sum(row["llm_total"] for row in rows) / len(rows) if rows else 0
    dim_avg = {
        key: (sum(row[f"llm_{key}"] for row in rows) / len(rows) if rows else 0)
        for key in DIMENSIONS
    }
    run_n = meta["runs"]
    lines = [
        f"# {meta['case_key']} reference 覆盖对照",
        "",
        f"- case_key: `{meta['case_key']}`",
        f"- case_id: `{meta['case_id']}`",
        f"- 简报模型: `{meta['brief_model']}`（本脚本不调用）",
        f"- 评测模型: `{meta['judge_model']}`",
        f"- 独立打分: 每段 {run_n} 次，四维分别取平均",
        f"- 综合得分: {fmt_score(overall)}/100（{len(rows)} 段总分再平均）",
        (
            f"- 四维平均: 相关 {fmt_score(dim_avg['relevance'])} / "
            f"准确 {fmt_score(dim_avg['accuracy'])} / "
            f"完整 {fmt_score(dim_avg['completeness'])} / "
            f"有用 {fmt_score(dim_avg['usefulness'])}"
        ),
        f"- 命中(≥{HIT}): {hits}/{len(rows)}",
        f"- 部分(≥{PARTIAL}): {partial}/{len(rows)}",
        f"- 弱覆盖: {weak}/{len(rows)}",
        f"- 每段参考对应简报 Top{rows[0]['top_k'] if rows else 3}",
        "",
        "| 参考位置 | 相关 | 准确 | 完整 | 有用 | 总分 | 三次明细 | 判定 | 简报 Top3 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        brief_cell = "<br>".join(
            f"{index}. `{item['location']}` ({item['score']:.2f})"
            for index, item in enumerate(row["brief_matches"], 1)
        ) or "—"
        run_cell = "/".join(str(item["total"]) for item in row["llm_runs"]) or "—"
        lines.append(
            "| "
            + " | ".join([
                row["ref_id"],
                fmt_score(row["llm_relevance"]),
                fmt_score(row["llm_accuracy"]),
                fmt_score(row["llm_completeness"]),
                fmt_score(row["llm_usefulness"]),
                fmt_score(row["llm_total"]),
                run_cell,
                row["verdict"],
                brief_cell,
            ])
            + " |"
        )
    lines.extend(["", "## 逐段对照（参考 vs 简报 Top3）", ""])
    for row in rows:
        run_detail = "；".join(
            f"第{index}次 {item['total']}（相关{item['relevance']}/准确{item['accuracy']}/完整{item['completeness']}/有用{item['usefulness']}）"
            for index, item in enumerate(row["llm_runs"], 1)
        ) or "无有效打分"
        lines.extend([
            f"### {row['ref_id']}｜{row['verdict']} {fmt_score(row['llm_total'])}/100",
            "",
            (
                f"- 平均 相关 {fmt_score(row['llm_relevance'])} / "
                f"准确 {fmt_score(row['llm_accuracy'])} / "
                f"完整 {fmt_score(row['llm_completeness'])} / "
                f"有用 {fmt_score(row['llm_usefulness'])}"
            ),
            f"- 三次：{run_detail}",
            f"- {row['llm_comment'] or '—'}",
            "",
            f"**参考：** {preview(row['ref_text'], 220)}",
            "",
        ])
        if not row["brief_matches"]:
            lines.append("简报无对应块。")
            lines.append("")
            continue
        for index, item in enumerate(row["brief_matches"], 1):
            lines.append(f"{index}. **{item['location']}**（召回 {item['score']:.2f}）")
            lines.append(f"   {preview(item['text'], 180)}")
            lines.append("")
    gaps = [row for row in rows if row["verdict"] != "命中"]
    if gaps:
        lines.extend(["## 缺口清单", ""])
        for row in gaps:
            lines.append(
                f"- **{row['verdict']} {fmt_score(row['llm_total'])}/100**｜{row['ref_id']}："
                f"{row['llm_comment'] or preview(row['ref_text'], 80)}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-key", default="case1")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--runs", type=int, default=0, help="覆盖 EVAL_JUDGE_RUNS，默认 3")
    parser.add_argument("--reference", default="", help="默认按 case-key 解析 evaluation_cases")
    args = parser.parse_args()
    settings = Settings()
    runs = args.runs if args.runs > 0 else max(1, settings.EVAL_JUDGE_RUNS)
    repository = RunRepository(settings.DATABASE_URL)
    llm = build_judge_client(settings)
    case = repository.get_case(args.case_key)
    if case is None:
        print(f"找不到 case: {args.case_key}")
        return 1
    reference_path = resolve_reference_path(args.case_key, args.reference or None)
    if not reference_path.exists():
        print(f"找不到 reference: {reference_path}")
        return 1
    reference = reference_path.read_text(encoding="utf-8")
    sections = split_reference(reference)
    if not sections:
        print("reference 为空，无法评测")
        return 1
    brief_units = flatten_brief(case.report_data)
    if not brief_units:
        print("简报 report_data 为空，无法评测")
        return 1
    embedding = EmbeddingService.from_settings(settings)
    ref_vectors = embedding.embed_documents([item["text"] for item in sections])
    brief_vectors = embedding.embed_documents([item["text"] for item in brief_units])

    print(
        f"评测模型 {llm.model_name}，每段独立打分 {runs} 次后取平均；简报模型 {settings.QUERY_ENGINE_MODEL_NAME}",
        flush=True,
    )
    rows = []
    for section, vector in zip(sections, ref_vectors):
        brief_matches = top_matches(vector, brief_units, brief_vectors, top_k=args.top_k)
        print(f"正在评 {section['id']}（{runs} 次）…", flush=True)
        judged = judge_group(
            llm,
            section["id"],
            section["text"],
            brief_matches,
            runs=runs,
            temperature=settings.EVAL_JUDGE_TEMPERATURE,
        )
        rows.append({
            "ref_id": section["id"],
            "ref_text": section["text"],
            "brief_matches": brief_matches,
            "top_k": args.top_k,
            "llm_relevance": judged["relevance"],
            "llm_accuracy": judged["accuracy"],
            "llm_completeness": judged["completeness"],
            "llm_usefulness": judged["usefulness"],
            "llm_total": judged["total"],
            "llm_comment": judged["comment"],
            "llm_runs": judged["runs"],
            "verdict": verdict_from_total(judged["total"]),
        })

    report = render(rows, {
        "case_key": case.case_key,
        "case_id": case.case_id,
        "brief_model": settings.QUERY_ENGINE_MODEL_NAME,
        "judge_model": llm.model_name,
        "runs": runs,
    })
    out_path = reference_path.with_name("coverage.md")
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"已写入 {out_path}（不入库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
