"""可信官方来源配置加载与 URL 校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "official_sources.yaml"


class OfficialSourcesConfigError(ValueError):
    """官方来源配置缺失或格式错误。"""


class UnsafeOfficialUrlError(ValueError):
    """URL 格式或协议不符合安全要求。"""


@dataclass(frozen=True)
class OfficialSource:
    source_id: str
    name: str
    organization: str
    source_type: str
    priority: int
    trust_level: str
    enabled: bool
    require_https: bool
    allowed_domain_suffixes: tuple[str, ...]
    allowed_path_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class SourceVerification:
    url: str
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    trust_level: Optional[str] = None
    source_priority: Optional[int] = None
    domain_verified: bool = False
    path_verified: bool = False
    verification_status: str = "unverified"
    verification_message: str = ""


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OfficialSourcesConfigError(f"配置字段 {field} 必须是对象")
    return value


def _require_nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OfficialSourcesConfigError(f"配置字段 {field} 必须是非空字符串")
    return value.strip()


def _require_string_list(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        suffix = "且不能为空" if not allow_empty else ""
        raise OfficialSourcesConfigError(f"配置字段 {field} 必须是字符串列表{suffix}")
    result = []
    for index, item in enumerate(value):
        result.append(_require_nonempty_text(item, f"{field}[{index}]"))
    return result


class OfficialSourcesRegistry:
    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH) -> None:
        self.config_path = Path(config_path)
        raw = self._load_yaml()
        self.version, self.defaults, self.sources, self.global_filters = self._validate(raw)
        self._sources_by_id = {source.source_id: source for source in self.sources}

    def _load_yaml(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            raise OfficialSourcesConfigError(f"官方来源配置文件不存在：{self.config_path}")
        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise OfficialSourcesConfigError(
                f"无法读取官方来源配置 {self.config_path}：{exc}"
            ) from exc
        return _require_mapping(raw, "root")

    @staticmethod
    def _validate(
        raw: dict[str, Any],
    ) -> tuple[int, dict[str, Any], tuple[OfficialSource, ...], dict[str, list[str]]]:
        for field in ("version", "defaults", "sources", "global_filters"):
            if field not in raw:
                raise OfficialSourcesConfigError(f"官方来源配置缺少必要字段：{field}")

        version = raw["version"]
        if not isinstance(version, int) or version < 1:
            raise OfficialSourcesConfigError("配置字段 version 必须是正整数")

        defaults = _require_mapping(raw["defaults"], "defaults")
        for field in ("enabled", "trust_level", "require_https"):
            if field not in defaults:
                raise OfficialSourcesConfigError(f"配置字段 defaults 缺少：{field}")
        if not isinstance(defaults["enabled"], bool):
            raise OfficialSourcesConfigError("配置字段 defaults.enabled 必须是布尔值")
        if not isinstance(defaults["require_https"], bool):
            raise OfficialSourcesConfigError("配置字段 defaults.require_https 必须是布尔值")
        default_trust = _require_nonempty_text(
            defaults["trust_level"], "defaults.trust_level"
        )

        raw_sources = raw["sources"]
        if not isinstance(raw_sources, list) or not raw_sources:
            raise OfficialSourcesConfigError("配置字段 sources 必须是非空列表")

        sources = []
        source_ids: set[str] = set()
        for index, item in enumerate(raw_sources):
            source_data = _require_mapping(item, f"sources[{index}]")
            prefix = f"sources[{index}]"
            required = (
                "source_id", "name", "organization", "source_type", "priority",
                "allowed_domain_suffixes",
            )
            for field in required:
                if field not in source_data:
                    raise OfficialSourcesConfigError(f"配置字段 {prefix} 缺少：{field}")

            source_id = _require_nonempty_text(
                source_data["source_id"], f"{prefix}.source_id"
            )
            if source_id in source_ids:
                raise OfficialSourcesConfigError(f"source_id 重复：{source_id}")
            source_ids.add(source_id)

            priority = source_data["priority"]
            if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
                raise OfficialSourcesConfigError(f"配置字段 {prefix}.priority 必须是正整数")
            enabled = source_data.get("enabled", defaults["enabled"])
            require_https = source_data.get("require_https", defaults["require_https"])
            if not isinstance(enabled, bool) or not isinstance(require_https, bool):
                raise OfficialSourcesConfigError(
                    f"配置字段 {prefix}.enabled/require_https 必须是布尔值"
                )

            domains = _require_string_list(
                source_data["allowed_domain_suffixes"],
                f"{prefix}.allowed_domain_suffixes",
                allow_empty=False,
            )
            normalized_domains = []
            for domain in domains:
                normalized = domain.lower().strip().rstrip(".")
                if "://" in normalized or "/" in normalized or not normalized:
                    raise OfficialSourcesConfigError(
                        f"配置字段 {prefix}.allowed_domain_suffixes 包含无效域名：{domain}"
                    )
                normalized_domains.append(normalized)

            paths = _require_string_list(
                source_data.get("allowed_path_prefixes", []),
                f"{prefix}.allowed_path_prefixes",
            )
            if any(not path.startswith("/") for path in paths):
                raise OfficialSourcesConfigError(
                    f"配置字段 {prefix}.allowed_path_prefixes 必须以 / 开头"
                )

            sources.append(
                OfficialSource(
                    source_id=source_id,
                    name=_require_nonempty_text(source_data["name"], f"{prefix}.name"),
                    organization=_require_nonempty_text(
                        source_data["organization"], f"{prefix}.organization"
                    ),
                    source_type=_require_nonempty_text(
                        source_data["source_type"], f"{prefix}.source_type"
                    ),
                    priority=priority,
                    trust_level=_require_nonempty_text(
                        source_data.get("trust_level", default_trust),
                        f"{prefix}.trust_level",
                    ),
                    enabled=enabled,
                    require_https=require_https,
                    allowed_domain_suffixes=tuple(normalized_domains),
                    allowed_path_prefixes=tuple(paths),
                )
            )

        filters = _require_mapping(raw["global_filters"], "global_filters")
        for field in ("exclude_title_keywords", "accepted_document_keywords"):
            if field not in filters:
                raise OfficialSourcesConfigError(
                    f"配置字段 global_filters 缺少：{field}"
                )
        global_filters = {
            "exclude_title_keywords": _require_string_list(
                filters["exclude_title_keywords"],
                "global_filters.exclude_title_keywords",
            ),
            "accepted_document_keywords": _require_string_list(
                filters["accepted_document_keywords"],
                "global_filters.accepted_document_keywords",
            ),
        }
        return version, defaults, tuple(sources), global_filters

    def get_source(self, source_id: str) -> OfficialSource:
        try:
            return self._sources_by_id[source_id]
        except KeyError as exc:
            raise KeyError(f"未找到官方来源 source_id：{source_id}") from exc

    @staticmethod
    def _domain_matches(hostname: str, suffix: str) -> bool:
        return hostname == suffix or hostname.endswith("." + suffix)

    def match_url(self, url: str) -> SourceVerification:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeOfficialUrlError("official_url 必须是有效的 HTTP(S) URL")
        if parsed.username or parsed.password:
            raise UnsafeOfficialUrlError("official_url 不允许包含用户名或密码")
        if parsed.scheme != "https":
            raise UnsafeOfficialUrlError("official_url 必须使用 HTTPS")

        hostname = parsed.hostname.lower().rstrip(".")
        matches: list[tuple[int, int, OfficialSource, str]] = []
        for source in self.sources:
            if not source.enabled:
                continue
            for suffix in source.allowed_domain_suffixes:
                if self._domain_matches(hostname, suffix):
                    matches.append((len(suffix), -source.priority, source, suffix))
        if not matches:
            return SourceVerification(
                url=url,
                verification_message=(
                    f"域名 {hostname} 未识别为预定义官方来源；允许继续读取，但来源未验证"
                ),
            )

        _, _, source, matched_suffix = max(matches, key=lambda item: (item[0], item[1]))
        decoded_path = unquote(parsed.path or "/")
        if source.allowed_path_prefixes:
            path_verified = any(
                decoded_path.startswith(prefix) for prefix in source.allowed_path_prefixes
            )
        else:
            path_verified = True

        if path_verified:
            status = "verified"
            message = (
                f"域名 {hostname} 匹配 {source.name} 的官方域名 {matched_suffix}；"
                + (
                    f"路径 {decoded_path} 位于允许范围"
                    if source.allowed_path_prefixes
                    else "该来源未限制路径范围"
                )
            )
        else:
            status = "unverified"
            message = (
                f"域名 {hostname} 匹配 {source.name}，但路径 {decoded_path} "
                "不在 allowed_path_prefixes 允许范围；允许继续读取但来源未验证"
            )

        return SourceVerification(
            url=url,
            source_id=source.source_id,
            source_name=source.name,
            source_type=source.source_type,
            trust_level=source.trust_level,
            source_priority=source.priority,
            domain_verified=True,
            path_verified=path_verified,
            verification_status=status,
            verification_message=message,
        )

    def is_excluded_title(self, title: str) -> bool:
        text = title or ""
        return any(
            keyword in text
            for keyword in self.global_filters["exclude_title_keywords"]
        )

    def has_accepted_document_keyword(self, text: str) -> bool:
        """辅助判断是否含常见文件词；False 不代表它不是政策文件。"""
        value = text or ""
        return any(
            keyword in value
            for keyword in self.global_filters["accepted_document_keywords"]
        )
