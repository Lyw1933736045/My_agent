"""评测命令行入口。"""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from ..llms import LLMClient
from .judge import evaluate, load_case
from .metrics import summarize
from .rubric_builder import build_rubrics


def _client() -> LLMClient:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    use_query_engine = os.getenv("JUDGE_USE_QUERY_ENGINE_API", "").lower() in {"1", "true", "yes"}
    return LLMClient(
        api_key=(
            os.getenv("QUERY_ENGINE_API_KEY", "") if use_query_engine
            else os.getenv("JUDGE_API_KEY") or os.getenv("QUERY_ENGINE_API_KEY", "")
        ),
        base_url=(
            os.getenv("QUERY_ENGINE_BASE_URL") if use_query_engine
            else os.getenv("JUDGE_BASE_URL") or os.getenv("QUERY_ENGINE_BASE_URL") or None
        ),
        model_name=os.getenv("JUDGE_MODEL_NAME", "qwen3.7-plus"),
        timeout=float(os.getenv("JUDGE_LLM_REQUEST_TIMEOUT", "300")),
        # 与 MiroFish 的 Qwen 测试一致，评测请求绕过本地代理。
        trust_env=False,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reference-based LLM-as-a-Judge 评测")
    parser.add_argument("command", choices=("build-rubrics", "evaluate", "show"))
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    case = args.case
    if args.command == "build-rubrics":
        print("[1/2] 正在读取 reference.md 并生成 atomic rubrics……", flush=True)
        build_rubrics(_client(), case / "reference.md", case / "rubrics.json", overwrite=args.overwrite)
        print(f"[2/2] 已生成：{case / 'rubrics.json'}", flush=True)
        return 0
    if args.command == "show":
        print((case / "result.json").read_text(encoding="utf-8"))
        return 0
    print("[1/3] 正在读取 rubrics、新闻材料和报告……", flush=True)
    rubrics, documents, report = load_case(case)
    print("[2/3] 正在执行 Coverage Judge……", flush=True)
    result = evaluate(
        _client(), rubrics, documents, report,
        progress=lambda message: print(f"      {message}", flush=True),
    )
    print("[3/3] Coverage 已完成，正在计算指标……", flush=True)
    output = {
        "summary": summarize(result),
        "judgments": result.model_dump(exclude={"rubrics"}),
    }
    (case / "result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print("结果已保存。", flush=True)
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
