"""Generate machine-readable and Markdown summaries for one simulation run."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
from typing import Any


POSITIVE_TERMS = ("支持", "积极", "利好", "期待", "完善", "提升", "推进", "便利", "认同")
NEGATIVE_TERMS = ("风险", "担忧", "警惕", "压力", "困难", "冲击", "质疑", "波动")
ACTION_LABELS = {
    "CREATE_POST": "发帖",
    "CREATE_COMMENT": "评论",
    "LIKE_POST": "点赞",
    "DISLIKE_POST": "点踩",
    "REPOST": "转发",
    "QUOTE_POST": "引用",
    "FOLLOW": "关注",
    "DO_NOTHING": "无操作",
}


def _sentiment(text: str) -> str:
    positive = sum(text.count(term) for term in POSITIVE_TERMS)
    negative = sum(text.count(term) for term in NEGATIVE_TERMS)
    if positive and negative:
        return "mixed"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return "neutral"


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("report model did not return a JSON object")
    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("report model result must be an object")
    return payload


class SimulationReporter:
    def generate(
        self,
        *,
        run: dict[str, Any],
        personas: list[dict[str, Any]],
        output_dir: Path,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        persona_by_agent = {index: item for index, item in enumerate(personas)}
        actions = [
            action
            for round_data in run.get("rounds") or []
            for action in round_data.get("actions") or []
        ]
        action_counts = Counter(action.get("action_type") for action in actions)
        role_content: dict[str, list[str]] = defaultdict(list)
        agent_content: dict[str, dict[str, Any]] = {}
        netizen_content: list[str] = []
        round_rows = []
        for round_data in run.get("rounds") or []:
            round_actions = round_data.get("actions") or []
            counts = Counter(item.get("action_type") for item in round_actions)
            texts = [str(item.get("content")) for item in round_actions if item.get("content")]
            round_rows.append({
                "round": round_data.get("round"),
                "simulated_hour": round_data.get("simulated_hour"),
                "active_agent_ids": round_data.get("active_agent_ids") or [],
                "action_counts": dict(counts),
                "content_samples": [text[:240] for text in texts[:3]],
            })
            for action in round_actions:
                text = str(action.get("content") or "").strip()
                if not text:
                    continue
                persona = persona_by_agent.get(action.get("agent_id"), {})
                agent_name = str(
                    action.get("agent_name")
                    or persona.get("display_name")
                    or f"Agent {action.get('agent_id')}"
                )
                role = str(persona.get("role_group") or persona.get("role_type") or "unknown")
                role_content[role].append(text)
                agent_content.setdefault(agent_name, {
                    "agent_name": agent_name,
                    "role_group": role,
                    "texts": [],
                })["texts"].append(text)
                if any(token in role.lower() for token in ("netizen", "investor", "网民", "投资者")):
                    netizen_content.append(text)

        sentiment_counts = Counter(_sentiment(text) for text in netizen_content)
        report = {
            "report_version": "1.0",
            "case_id": run.get("case_id"),
            "simulation_id": run.get("simulation_id"),
            "origin": "simulation",
            "disclaimer": "This report summarizes simulated output; it is not a real-world fact or prediction.",
            "agent_count": len(personas),
            "round_count": max(0, len(run.get("rounds") or []) - 1),
            "action_count": len(actions),
            "action_counts": dict(action_counts),
            "trajectory": round_rows,
            "role_views": {
                role: [text[:320] for text in texts[:5]]
                for role, texts in role_content.items()
            },
            "agent_views": [
                {
                    "agent_name": item["agent_name"],
                    "role_group": item["role_group"],
                    "texts": [text[:320] for text in item["texts"][:8]],
                }
                for item in agent_content.values()
            ],
            "netizen_sentiment": {
                "available": bool(netizen_content),
                "sample_count": len(netizen_content),
                "distribution": dict(sentiment_counts),
                "method": "keyword_heuristic",
            },
        }
        report["llm_summary"] = self._llm_summary(report)
        (output_dir / "simulation_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output_dir / "simulation_report.md").write_text(
            self._markdown(report), encoding="utf-8"
        )
        (output_dir / "rounds_detail.md").write_text(
            self._rounds_markdown(run), encoding="utf-8"
        )
        return report

    @staticmethod
    def _llm_summary(report: dict[str, Any]) -> dict[str, Any]:
        fallback = SimulationReporter._deterministic_summary(report)
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {
                "available": False,
                "llm_generated": False,
                "provider": None,
                "error": "LLM API key is not configured",
                **fallback,
            }
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=api_key,
                base_url=os.getenv("LLM_BASE_URL") or None,
                timeout=120.0,
                max_retries=0,
            )
            compact = {
                "trajectory": report["trajectory"],
                "agent_views": report["agent_views"],
                "action_counts": report["action_counts"],
                "netizen_sentiment": report["netizen_sentiment"],
            }
            response = client.chat.completions.create(
                model=os.getenv("LLM_MODEL_NAME") or "deepseek-chat",
                temperature=0.1,
                max_tokens=1200,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你只分析提供的多Agent模拟记录。不得把模拟言论写成现实事实。"
                            "返回JSON对象，字段为executive_summary字符串、event_trajectory字符串数组、"
                            "consensus字符串数组、disagreements字符串数组、netizen_emotion字符串。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
                ],
            )
            payload = _extract_json(response.choices[0].message.content or "")
            return {
                "available": True,
                "llm_generated": True,
                "provider": "DeepSeek",
                "model": os.getenv("LLM_MODEL_NAME") or "deepseek-chat",
                **payload,
            }
        except Exception as exc:
            return {
                "available": False,
                "llm_generated": False,
                "provider": "DeepSeek",
                "error": str(exc),
                **fallback,
            }

    @staticmethod
    def _deterministic_summary(report: dict[str, Any]) -> dict[str, Any]:
        contents = [
            text
            for item in (report.get("agent_views") or [])
            for text in item.get("texts") or []
        ]
        topics = (
            "人民币外汇期货试点",
            "汇率风险",
            "套保工具",
            "人民币国际化",
            "香港5年期人民币国债期货",
        )
        topic_counts = {
            topic: sum(topic in text for text in contents)
            for topic in topics
        }
        common = [topic for topic, count in sorted(topic_counts.items(), key=lambda item: -item[1]) if count >= 2]
        sentiment = report.get("netizen_sentiment") or {}
        distribution = sentiment.get("distribution") or {}
        consensus = [f"多条模拟表达共同围绕“{topic}”展开。" for topic in common[:3]]
        disagreements = []
        if distribution.get("negative") or distribution.get("mixed"):
            disagreements.append("部分模拟表达在支持推进的同时，也提到汇率波动、风险管理或落地条件。")
        if not disagreements:
            disagreements.append("本轮模拟没有检测到明确的立场对立文本。")
        return {
            "executive_summary": "模拟讨论从初始政策信息扩散到机构和网民互动，行为以评论、点赞、引用和转发为主。",
            "event_trajectory": [
                f"第 {item.get('round')} 轮产生 {sum((item.get('action_counts') or {}).values())} 条业务行为。"
                for item in report.get("trajectory") or []
            ],
            "consensus": consensus or ["多条模拟表达聚焦同一初始政策主题。"],
            "disagreements": disagreements,
            "netizen_emotion": f"网民样本的规则统计为 {distribution}；该统计仅反映模拟文本。",
        }

    @staticmethod
    def _markdown(report: dict[str, Any]) -> str:
        summary = report.get("llm_summary") or {}
        lines = [
            f"# Case1 模拟结果：{report.get('simulation_id')}",
            "",
            "> 本文只总结模拟生成内容，不代表现实事实、真实人物观点或投资建议。",
            "",
            "## 总览",
            "",
            f"- Agent：{report.get('agent_count')} 个",
            f"- 推演轮次：{report.get('round_count')} 轮",
            f"- 行为总数：{report.get('action_count')} 条",
        ]
        if summary.get("llm_generated"):
            lines.append("- 报告分析：DeepSeek 生成")
        else:
            lines.append("- 报告分析：本地规则兜底（DeepSeek 未返回）")
        if summary.get("executive_summary"):
            lines.extend(["", str(summary.get("executive_summary") or "")])
        lines.extend(["", "## 行为统计", ""])
        for action, count in sorted((report.get("action_counts") or {}).items()):
            lines.append(f"- {ACTION_LABELS.get(action, action)}：{count}")
        lines.extend(["", "## 事情走向", ""])
        if summary.get("available"):
            for item in summary.get("event_trajectory") or []:
                lines.append(f"- {item}")
        else:
            for item in report.get("trajectory") or []:
                labels = "、".join(
                    f"{ACTION_LABELS.get(action, action)} {count}"
                    for action, count in item.get("action_counts", {}).items()
                ) or "没有产生行为"
                lines.append(f"- 第 {item.get('round')} 轮：{labels}")
        lines.extend(["", "## 共识与分歧", ""])
        for title, key in (("共识", "consensus"), ("分歧", "disagreements")):
            lines.append(f"### {title}")
            lines.append("")
            values = summary.get(key) or []
            if values:
                lines.extend(f"- {item}" for item in values)
            else:
                lines.append("- 当前模拟样本不足以形成可靠归纳。")
            lines.append("")
        sentiment = report.get("netizen_sentiment") or {}
        lines.extend(["## 网民情绪", ""])
        if sentiment.get("available"):
            lines.append(f"- 样本数：{sentiment.get('sample_count')}")
            lines.append(f"- 规则统计：{sentiment.get('distribution')}")
            lines.append(f"- 总结：{summary.get('netizen_emotion') or '未形成明确结论'}")
        else:
            lines.append("- 没有证据派生的网民 Agent 内容，暂不输出网民情绪结论。")
        lines.extend(["", "## 各 Agent 模拟观点", ""])
        for item in report.get("agent_views") or []:
            lines.append(f"### {item.get('agent_name')}")
            lines.append("")
            lines.extend(
                f"- {item.get('agent_name')} 认为：{text}"
                for text in (item.get("texts") or [])[:5]
            )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _rounds_markdown(run: dict[str, Any]) -> str:
        lines = [
            f"# 逐轮 Agent 行为记录：{run.get('simulation_id')}",
            "",
            "> 以下全部内容是模拟记录，不是现实事实。",
            "",
        ]
        for round_data in run.get("rounds") or []:
            round_number = round_data.get("round")
            lines.extend([
                f"## 第 {round_number} 轮",
                "",
                f"模拟时间：{round_data.get('simulated_hour', '-')} 时；"
                f"激活 Agent：{', '.join(str(item) for item in round_data.get('active_agent_ids') or []) or '无'}",
                "",
            ])
            actions = round_data.get("actions") or []
            if not actions:
                lines.append("本轮没有业务行为。\n")
                continue
            for action in actions:
                name = action.get("agent_name") or f"Agent {action.get('agent_id')}"
                action_type = str(action.get("action_type") or "UNKNOWN")
                label = ACTION_LABELS.get(action_type, action_type)
                text = str(action.get("content") or "").strip()
                args = action.get("action_args") or {}
                target = args.get("post_id") or args.get("quoted_id") or args.get("new_post_id")
                if text:
                    if action_type in {"CREATE_POST", "CREATE_COMMENT", "QUOTE_POST"}:
                        lines.append(f"- **{name}**：{label}，认为：{text}")
                    else:
                        suffix = f"（目标帖子 {target}）" if target is not None else ""
                        lines.append(f"- **{name}**：{label}{suffix}；附带内容：{text}")
                else:
                    suffix = f"（目标帖子 {target}）" if target is not None else ""
                    lines.append(f"- **{name}**：{label}{suffix}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
