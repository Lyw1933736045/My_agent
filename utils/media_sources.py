"""媒体来源配置加载器。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "media_sources.yaml"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class MediaSourcesConfigError(ValueError):
    """媒体来源配置缺失或格式错误。"""


def _mapping(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise MediaSourcesConfigError(f"配置字段 {field} 必须是对象")
    return value


def load_media_sources(config_path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    path = Path(config_path)
    if not path.is_file():
        raise MediaSourcesConfigError(f"媒体来源配置文件不存在：{path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MediaSourcesConfigError(f"无法读取媒体来源配置：{exc}") from exc

    root = _mapping(raw, "root")
    newsnow = _mapping(root.get("newsnow"), "newsnow")
    rss = _mapping(root.get("rss"), "rss")
    tavily = _mapping(root.get("tavily", {"enabled": False}), "tavily")
    weibo = _mapping(root.get("weibo", {"enabled": False}), "weibo")
    adaptive_retrieval = _mapping(
        root.get("adaptive_retrieval", {"enabled": False}),
        "adaptive_retrieval",
    )
    selection = _mapping(root.get("selection"), "selection")
    if not isinstance(newsnow.get("sources"), list):
        raise MediaSourcesConfigError("配置字段 newsnow.sources 必须是列表")
    newsnow_groups = {"news_media", "social_media"}
    for source in newsnow["sources"]:
        if not isinstance(source, dict) or source.get("source_group") not in newsnow_groups:
            raise MediaSourcesConfigError(
                "newsnow.sources 每项必须配置 news_media 或 social_media"
            )
    if not isinstance(rss.get("feeds"), list):
        raise MediaSourcesConfigError("配置字段 rss.feeds 必须是列表")
    rss_media_groups = {"official_media", "news_media", "social_media"}
    for feed in rss["feeds"]:
        if (
            not isinstance(feed, dict)
            or feed.get("layer") != "media"
            or feed.get("source_group") not in rss_media_groups
        ):
            raise MediaSourcesConfigError(
                "rss.feeds 每项必须配置 layer=media，并指定 "
                "official_media、news_media 或 social_media"
            )
    if "enabled" in tavily and not isinstance(tavily.get("enabled"), bool):
        raise MediaSourcesConfigError("配置字段 tavily.enabled 必须是布尔值")
    if "enabled" in weibo and not isinstance(weibo.get("enabled"), bool):
        raise MediaSourcesConfigError("配置字段 weibo.enabled 必须是布尔值")
    if weibo.get("enabled") and not str(weibo.get("cookie_file", "")).strip():
        raise MediaSourcesConfigError("启用 weibo 时必须配置 cookie_file")
    if not isinstance(adaptive_retrieval.get("enabled", False), bool):
        raise MediaSourcesConfigError("配置字段 adaptive_retrieval.enabled 必须是布尔值")
    for field in ("tavily_min_valid_results", "weibo_min_valid_results"):
        value = adaptive_retrieval.get(field)
        if value is not None and (not isinstance(value, int) or value < 1):
            raise MediaSourcesConfigError(
                f"配置字段 adaptive_retrieval.{field} 必须是正整数"
            )
    return {
        "newsnow": newsnow,
        "rss": rss,
        "tavily": tavily,
        "weibo": weibo,
        "adaptive_retrieval": adaptive_retrieval,
        "selection": selection,
    }


def resolve_feed_url(url: str) -> str:
    """展开 RSS URL 中的环境变量，并拒绝未配置的占位符。"""
    # Settings 不会把未声明字段写进 os.environ；这里显式加载本项目 .env。
    load_dotenv(ENV_FILE, override=False)
    template = str(url).strip()
    if "${RSSHUB_BASE}" in template:
        base = os.environ.get("RSSHUB_BASE", "").strip().rstrip("/")
        if not base:
            raise MediaSourcesConfigError(
                "RSSHub 地址未配置，请在 My_agent/.env 设置 RSSHUB_BASE，"
                "例如 http://localhost:1200"
            )
        template = template.replace("${RSSHUB_BASE}", base)
    resolved = os.path.expandvars(template).rstrip("/")
    if not resolved or "${" in resolved:
        raise MediaSourcesConfigError(
            "RSSHub 地址未配置，请设置 RSSHUB_BASE，例如 https://rsshub.app"
        )
    return resolved


def resolve_env_path(value: str, field: str) -> str:
    """展开外部文件路径中的环境变量，并拒绝未解析的占位符。"""
    load_dotenv(ENV_FILE, override=False)
    resolved = os.path.expandvars(str(value).strip())
    if not resolved or "${" in resolved:
        raise MediaSourcesConfigError(f"{field} 未配置或包含未解析的环境变量")
    return resolved
