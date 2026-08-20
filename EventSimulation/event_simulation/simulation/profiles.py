"""Convert approved evidence Personas to the CSV consumed by OASIS."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def export_profiles(personas: list[dict[str, Any]], path: Path) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, int] = {}
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["user_id", "name", "username", "user_char", "description"],
        )
        writer.writeheader()
        for agent_id, persona in enumerate(personas):
            persona_id = str(persona["persona_id"])
            display_name = str(persona.get("display_name") or persona_id)
            grounded = [
                f"[{unit.get('ref_id')}] {unit.get('text')}"
                for unit in persona.get("grounded_utterance_units") or []
                if unit.get("ref_id") and unit.get("text")
            ]
            relations = [
                "；".join(filter(None, (
                    str(item.get("relation") or "相关"),
                    str(item.get("related_entity") or ""),
                    str(item.get("fact") or ""),
                )))
                for item in (persona.get("relations") or [])[:12]
            ]
            user_char = "\n".join([
                f"你在本次模拟中代表：{display_name}。",
                f"知识图谱确认的主体类型：{persona.get('entity_type_zh') or '可发声主体'}。",
                f"主体摘要：{persona.get('summary') or '暂无补充摘要'}",
                "知识图谱确认的关系：",
                *(relations or ["暂无补充关系。"]),
                "下面是初始化阶段可确认的证据内容：",
                *grounded,
                "模拟开始后，你可以基于这些背景形成新的看法和互动。",
                "新表达是模拟结果，不代表现实事实，也不要声称掌握未提供的内幕信息。",
            ])
            writer.writerow({
                "user_id": agent_id,
                "name": display_name,
                "username": f"agent_{agent_id:03d}",
                "user_char": user_char,
                "description": f"来自真实证据的案例智能体：{display_name}",
            })
            mapping[persona_id] = agent_id
    return mapping
