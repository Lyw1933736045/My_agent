"""把运行时状态导出为可复现的评测输入。"""

import json
from pathlib import Path

from ..state import RunState


def write_weibo_raw(state: RunState, directory: Path) -> Path | None:
    """保存脱敏后的微博原始结果；空结果不创建文件。"""
    if not state.weibo_raw:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "weibo_raw.json"
    path.write_text(
        json.dumps(state.weibo_raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_snapshot(state: RunState, directory: Path) -> tuple[Path, Path]:
    """只保存通过正文复核、实际交给后续节点的正文片段。"""
    directory.mkdir(parents=True, exist_ok=True)
    write_weibo_raw(state, directory)
    if state.retrieval_reflection:
        (directory / "retrieval_reflection.json").write_text(
            json.dumps(state.retrieval_reflection, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    relevant = {
        id(document): decision.relevant
        for document, decision in zip(state.selected_documents, state.content_decisions)
    }
    documents = []
    for document in state.selected_documents:
        if not relevant.get(id(document), True):
            continue
        candidate = document.candidate
        documents.append({
            "title": candidate.title,
            "source": candidate.source_name,
            "url": document.final_url,
            "snippet": candidate.snippet,
            "published_at": candidate.published_at,
            "fetched_at": document.fetched_at,
            "source_group": candidate.source_group,
            "content": document.content,
        })
    documents_path = directory / "retrieved_documents.json"
    documents_path.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = directory / "report.md"
    report_path.write_text(state.brief.strip() + "\n", encoding="utf-8")
    return documents_path, report_path
