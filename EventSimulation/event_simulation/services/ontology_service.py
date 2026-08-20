"""MiroFish-style ontology, document ingest, and Chinese graph localization.

The front of the graph pipeline matches Miro-Fish: de-linked report → news-style
plain text → ontology from that text plus the simulation question → 500/50
chunks into Zep. Internal type names stay ASCII; each type also carries a
Chinese display label for the web application.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable


ONTOLOGY_SYSTEM_PROMPT = """你是一个专业的知识图谱本体设计专家。你的任务是分析给定的文本内容和模拟需求，设计适合**社交媒体舆论模拟**的实体类型和关系类型。

**重要：你必须输出有效的JSON格式数据，不要输出任何其他内容。不要打开、访问或猜测任何链接；（S01）只是来源编号。**

## 核心任务背景

我们正在构建一个**社交媒体舆论模拟系统**。在这个系统中：
- 每个实体都是一个可以在社交媒体上发声、互动、传播信息的"账号"或"主体"
- 实体之间会相互影响、转发、评论、回应
- 我们需要模拟舆论事件中各方的反应和信息传播路径

因此，**实体必须是现实中真实存在的、可以在社媒上发声和互动的主体**：

**可以是**：
- 具体的个人（公众人物、当事人、意见领袖、专家学者、普通人、实名网民账号）
- 公司、企业（包括其官方账号）
- 组织机构（大学、协会、NGO、工会等）
- 政府部门、监管机构
- 媒体机构（报纸、电视台、自媒体、网站、记者）
- 社交媒体平台本身
- 特定群体代表（如校友会、粉丝团、维权群体等）

**不可以是**：
- 抽象概念（如"舆论"、"情绪"、"趋势"、"估值"、"政策主题"）
- 主题/话题（如"学术诚信"、"教育改革"、"IPO"）
- 观点/态度（如"支持方"、"反对方"）

## 输出格式

请输出JSON对象，包含以下结构：

{
  "entity_types": [
    {
      "name": "PascalCaseEnglishName",
      "display_name_zh": "中文显示名",
      "actor_kind": "person或organization",
      "description": "English, under 100 chars",
      "description_zh": "中文说明",
      "attributes": [{"name": "full_name", "type": "text", "description": "..."}],
      "examples": ["示例实体1"]
    }
  ],
  "edge_types": [
    {
      "name": "UPPER_SNAKE_CASE",
      "display_name_zh": "中文关系名",
      "description": "English, under 100 chars",
      "description_zh": "中文说明",
      "source_targets": [{"source": "源类型", "target": "目标类型"}],
      "attributes": []
    }
  ],
  "analysis_summary": "对文本内容的简要分析说明（中文）"
}

## 设计指南（极其重要）

1. 必须正好10个实体类型。最后两个必须依次为 Person、Organization 兜底类型。
2. 前8个是根据文本设计的具体可发声类型。其中必须同时包含：媒体机构类，以及社交账号/网民/个人投资者类，以便图谱能抽出媒体与微博账号。
3. actor_kind 只能是 person 或 organization，禁止 concept。
4. Person 的 actor_kind 必须是 person；Organization 的 actor_kind 必须是 organization。
5. 必须生成6至10个关系类型，并为每个关系限定 source_targets。
6. 内部实体类型名使用英文大驼峰，内部关系类型名使用英文大写下划线；同时提供准确的中文显示名称。
7. 属性名不得使用 name、uuid、group_id、summary、created_at。
"""

DOCUMENT_COMPOSE_SYSTEM_PROMPT = """你是财经新闻编辑。把给定材料改写成一篇连贯的中文新闻报道体纯文本。

硬性规则：
1. 不得改变事实、数字、日期、专有名词和真实账号名。
2. 不得编造发言人、机构、评论或材料中未出现的事实。
3. 不要打开、访问或猜测任何链接内容。（S01）这类标记只是来源编号，不是网址。
4. 媒体报道保留媒体名与核心事实。
5. 网民/微博发言可改写成「账号名 / 针对什么 / 情绪或事实观点」的短句，必须保留原文中的真实账号名，不得合并成匿名「网民」。
6. 只输出正文，不要Markdown链接，不要解释你的做法。
"""

MAX_TEXT_LENGTH_FOR_LLM = 50000

_TYPE_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_EDGE_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ATTR_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED = {"name", "uuid", "group_id", "name_embedding", "summary", "created_at"}
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+|www\.[^)]+)\)")
_BARE_URL = re.compile(r"https?://[^\s)>\]]+")


def _json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", value)
        if not match:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("模型必须返回JSON对象")
    return payload


def validate_ontology(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete MiroFish ontology contract without silent padding."""
    entity_types = payload.get("entity_types")
    edge_types = payload.get("edge_types")
    if not isinstance(entity_types, list) or len(entity_types) != 10:
        raise ValueError("本体必须正好包含10个实体类型")
    if not isinstance(edge_types, list) or not 6 <= len(edge_types) <= 10:
        raise ValueError("本体必须包含6至10个关系类型")

    entity_names: list[str] = []
    normalized_entities: list[dict[str, Any]] = []
    for index, raw in enumerate(entity_types):
        if not isinstance(raw, dict):
            raise ValueError(f"第{index + 1}个实体类型不是对象")
        name = str(raw.get("name") or "").strip()
        if not _TYPE_NAME.fullmatch(name):
            raise ValueError(f"实体类型名不合法：{name}")
        if name in entity_names:
            raise ValueError(f"实体类型重复：{name}")
        display_name = str(raw.get("display_name_zh") or "").strip()
        if not display_name:
            raise ValueError(f"实体类型{name}缺少中文显示名称")
        actor_kind = str(raw.get("actor_kind") or "").strip().lower()
        if actor_kind not in {"person", "organization"}:
            raise ValueError(f"实体类型{name}的actor_kind必须是person或organization，不能是抽象概念")
        attrs = []
        for attr in raw.get("attributes") or []:
            if not isinstance(attr, dict):
                continue
            attr_name = str(attr.get("name") or "").strip()
            if not _ATTR_NAME.fullmatch(attr_name) or attr_name in _RESERVED:
                raise ValueError(f"实体类型{name}包含不合法属性：{attr_name}")
            attrs.append({
                "name": attr_name,
                "type": "text",
                "description": str(attr.get("description") or attr_name)[:200],
            })
        entity_names.append(name)
        normalized_entities.append({
            "name": name,
            "display_name_zh": display_name,
            "actor_kind": actor_kind,
            "description": str(raw.get("description") or f"A speakable {name} actor.")[:300],
            "description_zh": str(raw.get("description_zh") or display_name)[:300],
            "attributes": attrs[:3],
            "examples": [str(item) for item in (raw.get("examples") or [])[:5] if str(item).strip()],
        })

    if entity_names[-2:] != ["Person", "Organization"]:
        raise ValueError("最后两个实体类型必须依次为Person和Organization")
    if normalized_entities[-2]["actor_kind"] != "person":
        raise ValueError("Person的actor_kind必须是person")
    if normalized_entities[-1]["actor_kind"] != "organization":
        raise ValueError("Organization的actor_kind必须是organization")

    normalized_edges: list[dict[str, Any]] = []
    edge_names: set[str] = set()
    known = set(entity_names)
    for index, raw in enumerate(edge_types):
        if not isinstance(raw, dict):
            raise ValueError(f"第{index + 1}个关系类型不是对象")
        name = str(raw.get("name") or "").strip()
        if not _EDGE_NAME.fullmatch(name) or name in edge_names:
            raise ValueError(f"关系类型名不合法或重复：{name}")
        display_name = str(raw.get("display_name_zh") or "").strip()
        if not display_name:
            raise ValueError(f"关系类型{name}缺少中文显示名称")
        pairs = []
        for pair in raw.get("source_targets") or []:
            source = str((pair or {}).get("source") or "")
            target = str((pair or {}).get("target") or "")
            if source not in known or target not in known:
                raise ValueError(f"关系{name}引用了未定义的实体类型：{source}->{target}")
            pairs.append({"source": source, "target": target})
        if not pairs:
            raise ValueError(f"关系类型{name}必须声明源和目标类型")
        edge_names.add(name)
        normalized_edges.append({
            "name": name,
            "display_name_zh": display_name,
            "description": str(raw.get("description") or name)[:300],
            "description_zh": str(raw.get("description_zh") or display_name)[:300],
            "source_targets": pairs,
            "attributes": [],
        })

    return {
        "ontology_version": "mirofish-actor-v1",
        "entity_types": normalized_entities,
        "edge_types": normalized_edges,
        "analysis_summary": str(payload.get("analysis_summary") or "").strip(),
    }


def _truncate_for_llm(text: str) -> str:
    value = clean_text(text)
    if len(value) <= MAX_TEXT_LENGTH_FOR_LLM:
        return value
    return (
        value[:MAX_TEXT_LENGTH_FOR_LLM]
        + f"\n\n...(原文共{len(value)}字，已截取前{MAX_TEXT_LENGTH_FOR_LLM}字用于分析)..."
    )


def strip_report_links(text: str) -> str:
    """Drop URLs from a brief report. Keep [S01](url) as a source id only."""

    def _replace(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        if re.fullmatch(r"S\d+", label, re.I):
            return f"（{label}）"
        return label

    value = _MD_LINK.sub(_replace, str(text or ""))
    value = _BARE_URL.sub("", value)
    return clean_text(value)


def graph_source_text(seed: dict[str, Any]) -> str:
    """Plain report used for Miro-Fish ingest, with a Seed-evidence fallback."""
    document = clean_text(seed.get("graph_document"))
    if document:
        return document
    report = strip_report_links(str(seed.get("source_report") or ""))
    if report:
        return report
    return evidence_text(seed)


class DocumentComposer:
    """Rewrite a de-linked brief into news-style plain text without new facts."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        self._client = client
        self.model = model or os.getenv("LLM_MODEL_NAME") or "deepseek-chat"

    def _llm(self):
        if self._client is not None:
            return self._client
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("改写图谱输入文本需要配置 LLM_API_KEY 或 OPENAI_API_KEY")
        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL") or None,
            timeout=300.0,
            max_retries=1,
        )
        return self._client

    def compose_from_seed(self, seed: dict[str, Any]) -> str:
        existing = clean_text(seed.get("graph_document"))
        if existing:
            return existing
        source = strip_report_links(str(seed.get("source_report") or "")) or evidence_text(seed)
        if not source:
            raise ValueError("缺少可用于图谱抽取的报告正文")
        question = str((seed.get("scenario") or {}).get("question") or "").strip()
        return self.compose(source, question)

    def compose(self, source_report: str, simulation_requirement: str) -> str:
        prompt = (
            f"## 模拟需求\n\n{simulation_requirement or '社交媒体舆论模拟'}\n\n"
            f"## 材料（链接已去掉，不要打开任何网址）\n\n"
            f"{_truncate_for_llm(source_report)}\n\n"
            "请改写成新闻报道体纯文本。"
        )
        response = self._llm().chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": DOCUMENT_COMPOSE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        article = clean_text(response.choices[0].message.content or "")
        if not article:
            raise RuntimeError("报告改写未返回正文")
        return article


class OntologyGenerator:
    """Generate and strictly validate an actor-only ontology using the configured LLM."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        self._client = client
        self.model = model or os.getenv("LLM_MODEL_NAME") or "deepseek-chat"

    def _llm(self):
        if self._client is not None:
            return self._client
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("生成知识图谱本体需要配置 LLM_API_KEY 或 OPENAI_API_KEY")
        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL") or None,
            timeout=180.0,
            max_retries=1,
        )
        return self._client

    def generate(self, seed: dict[str, Any]) -> dict[str, Any]:
        document = graph_source_text(seed)
        question = str((seed.get("scenario") or {}).get("question") or "").strip()
        return self.generate_from_document(document, question)

    def generate_from_document(self, document_text: str, simulation_requirement: str) -> dict[str, Any]:
        combined = _truncate_for_llm(document_text)
        prompt = f"""## 模拟需求

{simulation_requirement or '社交媒体舆论模拟'}

## 文档内容

{combined}

请根据以上内容，设计适合社会舆论模拟的实体类型和关系类型。

**必须遵守的规则**：
1. 必须正好输出10个实体类型
2. 最后2个必须是兜底类型：Person（个人兜底）和 Organization（组织兜底）
3. 前8个是根据文本内容设计的具体类型
4. 所有实体类型必须是现实中可以发声的主体，不能是抽象概念
5. 前8类中必须包含媒体机构，以及社交账号/网民/个人投资者
6. 每个类型提供 display_name_zh 和 actor_kind（person或organization）
7. 属性名不能使用 name、uuid、group_id 等保留字
"""
        error: Exception | None = None
        for attempt in range(3):
            suffix = "" if error is None else f"\n\n上次输出未通过校验：{error}。请完整修正。"
            response = self._llm().chat.completions.create(
                model=self.model,
                temperature=max(0.0, 0.25 - attempt * 0.1),
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": ONTOLOGY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt + suffix},
                ],
            )
            try:
                return validate_ontology(_json_object(response.choices[0].message.content or ""))
            except (ValueError, json.JSONDecodeError) as exc:
                error = exc
        raise RuntimeError(f"本体生成连续3次未通过校验：{error}")


def clean_text(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def split_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Use MiroFish's sentence-aware 500/50 character chunking policy."""
    value = clean_text(text)
    if not value:
        return []
    if len(value) <= chunk_size:
        return [value]
    chunks: list[str] = []
    start = 0
    separators = ("。", "！", "？", ".\n", "!\n", "?\n", "\n\n", ". ", "! ", "? ")
    while start < len(value):
        end = min(len(value), start + chunk_size)
        if end < len(value):
            window = value[start:end]
            for separator in separators:
                index = window.rfind(separator)
                if index > chunk_size * 0.3:
                    end = start + index + len(separator)
                    break
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap if end < len(value) else len(value)
    return chunks


def evidence_records(seed: dict[str, Any]) -> list[dict[str, Any]]:
    """Return traceable, accepted evidence records ready for chunking."""
    records: list[dict[str, Any]] = []
    for kind, collection, id_key in (
        ("fact", seed.get("facts") or [], "fact_id"),
        ("claim", seed.get("claims") or [], "claim_id"),
    ):
        for item in collection:
            text = clean_text(item.get("text"))
            if not text:
                continue
            if kind == "claim":
                subject = " ".join(
                    part for part in (
                        clean_text(item.get("speaker")),
                        clean_text(item.get("organization")),
                    ) if part
                )
                body = f"言论主体：{subject}。言论内容：{text}" if subject else f"言论内容：{text}"
            else:
                body = f"事实内容：{text}"
            event_time = clean_text(item.get("event_time"))
            if event_time:
                body += f"\n事件时间：{event_time}"
            records.append({
                "item_id": str(item[id_key]),
                "item_type": kind,
                "text": body,
                "source_ids": [str(value) for value in item.get("source_ids") or []],
                "trace": item.get("trace") or [],
            })
    return records


def evidence_text(seed: dict[str, Any]) -> str:
    return "\n\n---\n\n".join(record["text"] for record in evidence_records(seed))


_GENERIC_ACTOR_NAMES = {
    "实体", "节点", "主体", "个人", "人物", "组织", "机构", "公司", "企业",
    "政府", "媒体", "公众", "网民", "用户", "发言人", "未知", "未分类",
    "创新", "改革", "情绪", "趋势", "观点", "舆论", "话题", "态度",
    "支持方", "反对方", "市场", "政策", "事件", "概念",
}
_TIME_OR_SLOGAN = re.compile(
    r"(今年以来|近年来|长期以来|日前|近日|目前|当前|截至|以来$|"
    r"\d{2,4}年|\d+月|\d+日|未来\d+|进一步|健全|推进试点)"
)
_GENERIC_GROUP = re.compile(
    r"^(个别|相关|部分|各类|一些|符合条件)|行业$|投资者$|^地方政府$|"
    r"^(保险|年金|社保|A股|港股|市场|资本市场|中小企业|A股公司)$"
)


def is_speakable_actor_name(value: object) -> bool:
    """Reject generic, temporal, slogan-like, or sentence-length node names."""
    name = re.sub(r"\s+", " ", str(value or "")).strip().strip("，,。；;：:（）()[]【】\"“”")
    if not name or name in _GENERIC_ACTOR_NAMES or len(name) < 2 or len(name) > 40:
        return False
    if re.match(r"^\d", name):
        return False
    if re.search(r"https?://|www\.|\.(?:com|cn|net|org)(?:\.|$)", name, re.I):
        return False
    if any(mark in name for mark in ("。", "！", "？", "；", ";", "\n", "、")):
        return False
    if _TIME_OR_SLOGAN.search(name) or _GENERIC_GROUP.search(name):
        return False
    return True


class GraphChineseLocalizer:
    """Translate graph display strings once, outside the Agent simulation loop."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        self.generator = OntologyGenerator(client=client, model=model)

    @staticmethod
    def _needs_translation(value: object) -> bool:
        text = str(value or "").strip()
        return bool(re.search(r"[A-Za-z]{2,}|[A-Za-z]+_[A-Za-z_]", text))

    def translate(self, records: Iterable[dict[str, Any]]) -> dict[str, dict[str, str]]:
        pending = [dict(record) for record in records]
        result: dict[str, dict[str, str]] = {}
        for offset in range(0, len(pending), 25):
            batch = pending[offset:offset + 25]
            if not any(
                self._needs_translation(value)
                for record in batch
                for key, value in record.items()
                if key != "id"
            ):
                for record in batch:
                    result[str(record["id"])] = {
                        key: str(value or "") for key, value in record.items() if key != "id"
                    }
                continue
            prompt = {
                "task": "把每条记录中的英文名称和说明准确翻译成自然、专业的简体中文；中文原文保持不变；专有名词采用通行中文译名；不得概括、删减或改写事实。返回相同id和相同字段。",
                "records": batch,
            }
            response = self.generator._llm().chat.completions.create(
                model=self.generator.model,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "你是专业金融与公共事务翻译。只返回JSON对象，格式为{\"translations\":[...]}。"},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
            )
            payload = _json_object(response.choices[0].message.content or "")
            translated = payload.get("translations") or []
            translated_by_id = {
                str(item.get("id")): item for item in translated if isinstance(item, dict) and item.get("id")
            }
            for record in batch:
                record_id = str(record["id"])
                item = translated_by_id.get(record_id)
                if not item:
                    raise RuntimeError(f"图谱中文翻译缺少记录：{record_id}")
                result[record_id] = {
                    key: str(item.get(key) or value or "").strip()
                    for key, value in record.items()
                    if key != "id"
                }
        return result
