"""Stable report data models and deterministic Markdown rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EvidenceItem(_Model):
    text: str = ""
    source_ids: list[str] = Field(default_factory=list)


class SupportingView(_Model):
    speaker: str = ""
    organization: str = ""
    point: str = ""
    source_id: str | None = None


class SocialView(_Model):
    account: str = ""
    point: str = ""
    likes: int | None = None
    shares: int | None = None
    comments: int | None = None
    source_id: str | None = None


class BriefTopic(_Model):
    title: str = ""
    summary: str = ""
    supporting_views: list[SupportingView] = Field(default_factory=list)
    social_views: list[SocialView] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class TopicSection(_Model):
    overview: str = ""
    topics: list[BriefTopic] = Field(default_factory=list)


class MediaSection(_Model):
    overview: str = ""
    domestic: TopicSection = Field(default_factory=TopicSection)
    overseas: TopicSection = Field(default_factory=TopicSection)


class TimelineItem(_Model):
    date: str | None = None
    event: str = ""
    source_ids: list[str] = Field(default_factory=list)


class KeyMetric(_Model):
    label: str = ""
    value: str = ""
    context: str = ""
    source_ids: list[str] = Field(default_factory=list)


class SynthesisSection(_Model):
    consensus: list[EvidenceItem] = Field(default_factory=list)
    differences: list[EvidenceItem] = Field(default_factory=list)
    risks: list[EvidenceItem] = Field(default_factory=list)
    watch_points: list[EvidenceItem] = Field(default_factory=list)


class BriefSource(_Model):
    id: str
    title: str = ""
    source_name: str = ""
    url: str
    published_at: str | None = None
    source_type: Literal["official", "media", "social"] = "media"


class BriefData(_Model):
    title: str
    generated_at: str | None = None
    executive_summary: list[EvidenceItem] = Field(default_factory=list)
    official: TopicSection = Field(default_factory=TopicSection)
    media: MediaSection = Field(default_factory=MediaSection)
    public_opinion: TopicSection = Field(default_factory=TopicSection)
    timeline: list[TimelineItem] = Field(default_factory=list)
    key_metrics: list[KeyMetric] = Field(default_factory=list)
    synthesis: SynthesisSection = Field(default_factory=SynthesisSection)
    sources: list[BriefSource] = Field(default_factory=list)


@dataclass(frozen=True)
class BriefResult:
    data: dict[str, Any]
    markdown: str


_LIST_LIMITS = {
    "executive_summary": 5,
    "timeline": 8,
    "key_metrics": 8,
    "topics": 6,
    "consensus": 4,
    "differences": 4,
    "risks": 4,
    "watch_points": 4,
    "supporting_views": 6,
    "social_views": 6,
}


def _limit_payload(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {name: _limit_payload(item, name) for name, item in value.items()}
    if isinstance(value, list):
        limited = value[: _LIST_LIMITS.get(key, len(value))]
        return [_limit_payload(item) for item in limited]
    return value


def _clean_source_references(value: Any, known_ids: set[str]) -> Any:
    if isinstance(value, dict):
        cleaned = {
            key: _clean_source_references(item, known_ids)
            for key, item in value.items()
        }
        if isinstance(cleaned.get("source_ids"), list):
            cleaned["source_ids"] = list(dict.fromkeys(
                item for item in cleaned["source_ids"] if item in known_ids
            ))
        if "source_id" in cleaned and cleaned.get("source_id") not in known_ids:
            cleaned["source_id"] = None
        return cleaned
    if isinstance(value, list):
        return [_clean_source_references(item, known_ids) for item in value]
    return value


def normalize_brief_data(
    payload: dict[str, Any],
    *,
    title: str,
    sources: list[dict[str, Any]],
) -> BriefData:
    """Apply hard list limits and replace LLM sources with the trusted catalog."""
    limited = _limit_payload(dict(payload))
    official = limited.get("official")
    if isinstance(official, dict) and isinstance(official.get("topics"), list):
        official["topics"] = official["topics"][:5]
    media = limited.get("media")
    if isinstance(media, dict):
        domestic = media.get("domestic")
        overseas = media.get("overseas")
        if isinstance(domestic, dict) and isinstance(domestic.get("topics"), list):
            domestic["topics"] = domestic["topics"][:6]
        if isinstance(overseas, dict) and isinstance(overseas.get("topics"), list):
            overseas["topics"] = overseas["topics"][:4]
    public_opinion = limited.get("public_opinion")
    if isinstance(public_opinion, dict) and isinstance(public_opinion.get("topics"), list):
        public_opinion["topics"] = public_opinion["topics"][:5]
    limited["title"] = title
    limited["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    limited["sources"] = sources
    known_ids = {str(item.get("id")) for item in sources}
    cleaned = _clean_source_references(limited, known_ids)
    return BriefData.model_validate(cleaned)


def _links(source_ids: list[str], sources: dict[str, BriefSource]) -> str:
    links = []
    for source_id in source_ids:
        source = sources.get(source_id)
        if source:
            links.append(f"[{source.id}]({source.url})")
    return " " + " ".join(links) if links else ""


def _render_topics(lines: list[str], section: TopicSection, sources: dict[str, BriefSource]) -> None:
    if section.overview:
        lines.extend([section.overview, ""])
    for topic in section.topics:
        if not topic.title and not topic.summary:
            continue
        lines.append(f"#### {topic.title or '主要关注'}")
        if topic.summary:
            lines.extend([f"{topic.summary}{_links(topic.source_ids, sources)}", ""])
        for view in topic.supporting_views:
            attribution = " / ".join(item for item in (view.speaker, view.organization) if item)
            prefix = f"{attribution}：" if attribution else ""
            source_ids = [view.source_id] if view.source_id else []
            lines.append(f"- {prefix}{view.point}{_links(source_ids, sources)}")
        for view in topic.social_views:
            metrics = []
            for label, count in (("赞", view.likes), ("转发", view.shares), ("评论", view.comments)):
                if count is not None:
                    metrics.append(f"{label} {count}")
            suffix = f"（{'，'.join(metrics)}）" if metrics else ""
            source_ids = [view.source_id] if view.source_id else []
            lines.append(f"- {view.account + '：' if view.account else ''}{view.point}{suffix}{_links(source_ids, sources)}")
        if topic.supporting_views or topic.social_views:
            lines.append("")


def render_brief_markdown(data: BriefData) -> str:
    """Render the fixed report layout from the same data used by the dashboard."""
    sources = {item.id: item for item in data.sources}
    lines = [f"# {data.title}", ""]

    if data.executive_summary:
        lines.extend(["## 核心摘要", ""])
        lines.extend(
            f"- {item.text}{_links(item.source_ids, sources)}"
            for item in data.executive_summary if item.text
        )
        lines.append("")

    if data.timeline:
        lines.extend(["## 事件时间线", ""])
        for item in data.timeline:
            prefix = f"**{item.date}** — " if item.date else ""
            lines.append(f"- {prefix}{item.event}{_links(item.source_ids, sources)}")
        lines.append("")

    if data.key_metrics:
        lines.extend(["## 关键数据", "", "| 指标 | 数值 | 说明 | 来源 |", "|---|---:|---|---|"])
        for item in data.key_metrics:
            lines.append(
                f"| {item.label} | {item.value} | {item.context} |{_links(item.source_ids, sources).strip()} |"
            )
        lines.append("")

    if data.official.overview or data.official.topics:
        lines.extend(["## 一、官方层面", ""])
        _render_topics(lines, data.official, sources)

    media = data.media
    if media.overview or media.domestic.overview or media.domestic.topics or media.overseas.overview or media.overseas.topics:
        lines.extend(["## 二、媒体层面", ""])
        if media.overview:
            lines.extend([media.overview, ""])
        if media.domestic.overview or media.domestic.topics:
            lines.extend(["### 境内媒体", ""])
            _render_topics(lines, media.domestic, sources)
        if media.overseas.overview or media.overseas.topics:
            lines.extend(["### 境外及港澳媒体", ""])
            _render_topics(lines, media.overseas, sources)

    if data.public_opinion.overview or data.public_opinion.topics:
        lines.extend(["## 三、社会舆论层面", ""])
        _render_topics(lines, data.public_opinion, sources)

    synthesis_groups = [
        ("主要共识", data.synthesis.consensus),
        ("主要差异", data.synthesis.differences),
        ("争议与风险", data.synthesis.risks),
        ("后续观察", data.synthesis.watch_points),
    ]
    if any(items for _, items in synthesis_groups):
        lines.extend(["## 四、综合研判", ""])
        for heading, items in synthesis_groups:
            if not items:
                continue
            lines.extend([f"### {heading}", ""])
            lines.extend(f"- {item.text}{_links(item.source_ids, sources)}" for item in items if item.text)
            lines.append("")

    if data.sources:
        lines.extend(["## 来源", "", "| ID | 类型 | 来源 | 标题 | 日期 |", "|---|---|---|---|---|"])
        labels = {"official": "官方", "media": "媒体", "social": "社交"}
        for source in data.sources:
            title = source.title.replace("|", "／")
            name = source.source_name.replace("|", "／")
            lines.append(
                f"| {source.id} | {labels[source.source_type]} | {name} | [{title}]({source.url}) | {source.published_at or ''} |"
            )

    return "\n".join(lines).strip() + "\n"
