"""从参考报告生成并固定保存 atomic rubrics。"""

from pathlib import Path

from ..utils.text_processing import extract_json
from .prompts import RUBRIC_BUILDER_PROMPT
from .schemas import RubricSet


def build_rubrics(client, reference_path: Path, output_path: Path, *, overwrite: bool = False) -> RubricSet:
    if output_path.exists() and not overwrite:
        return RubricSet.model_validate_json(output_path.read_text(encoding="utf-8"))
    reference = reference_path.read_text(encoding="utf-8").strip()
    if not reference:
        raise ValueError("reference.md 不能为空")
    response = client.invoke(RUBRIC_BUILDER_PROMPT, reference)
    payload = extract_json(response)
    result = RubricSet.model_validate(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
