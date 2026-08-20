"""Case loading orchestration and human-readable audit rendering."""

from __future__ import annotations

from typing import Any

import json
from pathlib import Path


class CaseService:
    """Build the audit and strict Seed artifacts for one case."""

    def __init__(self, loader: Any, seed_builder: Any) -> None:
        self.loader = loader
        self.seed_builder = seed_builder

    def build(self, case_ref: str, output_dir: Path) -> dict[str, Any]:
        bundle = self.loader.load(case_ref)
        seed = self.seed_builder.build(bundle)
        output_dir.mkdir(parents=True, exist_ok=True)
        audit = bundle.audit_dict()
        (output_dir / "case_bundle_audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output_dir / "case_bundle_audit.md").write_text(
            render_audit_markdown(audit), encoding="utf-8"
        )
        (output_dir / "seed.json").write_text(
            json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return seed


def render_audit_markdown(audit: dict[str, Any]) -> str:
    case = audit.get("case") or {}
    counts = audit.get("counts") or {}
    lines = [
        f"# Case Bundle Audit: {case.get('case_key') or case.get('case_id') or 'unknown'}",
        "",
        f"- Topic: {case.get('topic') or ''}",
        f"- Query: {case.get('query') or ''}",
        f"- Status: {case.get('status') or ''}",
        "",
        "## Counts",
        "",
        f"- Child runs: {counts.get('child_runs', 0)}",
        f"- Related documents: {counts.get('related_documents', 0)}",
        f"- Accepted documents: {counts.get('accepted_documents', 0)}",
        f"- Accepted documents with content: {counts.get('accepted_with_content', 0)}",
        f"- Media insights: {counts.get('media_insights', 0)}",
        f"- Social insights: {counts.get('social_insights', 0)}",
        f"- Brief sources: {counts.get('brief_sources', 0)}",
        f"- Brief timeline events: {counts.get('brief_timeline', 0)}",
        f"- Brief key metrics: {counts.get('brief_key_metrics', 0)}",
        "",
        "### Accepted documents by source group",
        "",
    ]
    source_groups = counts.get("source_groups") or {}
    lines.extend(f"- {name}: {count}" for name, count in sorted(source_groups.items()))
    lines.extend(["", "### Related documents by source group", ""])
    related_source_groups = counts.get("related_source_groups") or {}
    lines.extend(f"- {name}: {count}" for name, count in sorted(related_source_groups.items()))
    lines.extend(["", "### Prepared analysis items", ""])
    prepared_items = counts.get("prepared_items") or {}
    lines.extend(f"- {name}: {count}" for name, count in prepared_items.items())
    lines.extend(["", "## Quality warnings", ""])
    warnings = audit.get("quality_warnings") or []
    if not warnings:
        lines.append("- None")
    else:
        for warning in warnings:
            detail = f" (count: {warning['count']})" if "count" in warning else ""
            lines.append(f"- `{warning.get('code', 'UNKNOWN')}`{detail}: {warning.get('message', '')}")
    lines.extend([
        "",
        "> This audit contains metadata and counts only. Accepted document bodies are not copied.",
        "",
    ])
    return "\n".join(lines)
