"""NewsNow、RSS、Tavily 的串行统一发现出口。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .media_models import DiscoveryResult, MediaCandidate, SourceFetchResult
from ..utils.dedup import select_candidates


class MediaDiscovery:
    def __init__(self, providers: dict[str, object]) -> None:
        self.providers = providers

    def run(
        self,
        queries: list[str],
        *,
        limit: int = 20,
        max_per_source: int = 3,
        max_per_source_overrides: dict[str, int] | None = None,
        max_age_days: int | None = None,
        provider_queries: dict[str, list[str]] | None = None,
        cancel_check=None,
        progress=None,
    ) -> DiscoveryResult:
        if not queries:
            raise ValueError("媒体发现至少需要一个查询词")
        if limit < 1 or max_per_source < 1:
            raise ValueError("limit 和 max_per_source 必须是正整数")

        raw: list[MediaCandidate] = []
        errors: dict[str, str] = {}
        sources: list[SourceFetchResult] = []
        counts = {name: 0 for name in self.providers}
        provider_stats: dict[str, int] = {}
        for name, provider in self.providers.items():
            if progress:
                progress(f"开始 {name} 发现……")
            try:
                if cancel_check and cancel_check():
                    raise RuntimeError("任务已中止")
                effective_queries = (
                    provider_queries.get(name, queries)
                    if provider_queries else queries
                )
                items = provider.search(
                    effective_queries,
                    limit=max(limit * 3, 20),
                    progress=progress,
                )
                if cancel_check and cancel_check():
                    raise RuntimeError("任务已中止")
                counts[name] = len(items)
                raw.extend(items)
                diagnostics = getattr(provider, "diagnostics", None)
                if diagnostics is not None:
                    for source, detail in diagnostics.successful_sources.items():
                        sources.append(
                            SourceFetchResult(
                                provider=name,
                                name=source,
                                ok=True,
                                detail=detail,
                            )
                        )
                    for source, error in diagnostics.failed_sources.items():
                        errors[f"{name}/{source}"] = error
                        sources.append(
                            SourceFetchResult(
                                provider=name,
                                name=source,
                                ok=False,
                                detail=error,
                            )
                        )
                    provider_stats[f"{name}_failed_sources"] = len(
                        diagnostics.failed_sources
                    )
                    provider_stats[f"{name}_successful_sources"] = len(
                        diagnostics.successful_sources
                    )
                    for status, count in diagnostics.status_counts.items():
                        provider_stats[f"{name}_{status}_responses"] = count
                elif items:
                    sources.append(
                        SourceFetchResult(
                            provider=name,
                            name=name,
                            ok=True,
                            detail=f"{len(items)} 条",
                        )
                    )
                if progress:
                    progress(f"{name} 完成：{len(items)} 条")
            except Exception as exc:
                if cancel_check and cancel_check():
                    raise
                errors[name] = str(exc)
                sources.append(
                    SourceFetchResult(
                        provider=name,
                        name=name,
                        ok=False,
                        detail=str(exc),
                    )
                )
                if progress:
                    progress(f"{name} 失败：{exc}")

        filtered = []
        time_filtered_count = 0
        for candidate in raw:
            effective_max_age = (
                candidate.max_age_days
                if candidate.max_age_days is not None
                else max_age_days
            )
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=effective_max_age)
                if effective_max_age is not None and effective_max_age > 0
                else None
            )
            if cutoff is not None and candidate.published_at:
                try:
                    published = datetime.fromisoformat(
                        candidate.published_at.replace("Z", "+00:00")
                    )
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                    if published < cutoff:
                        time_filtered_count += 1
                        continue
                except ValueError:
                    pass
            filtered.append(candidate)

        selected, dedup_stats = select_candidates(
            filtered,
            queries,
            limit=limit,
            max_per_source=max_per_source,
            max_per_source_overrides=max_per_source_overrides,
        )
        stats = {
            **{f"{name}_count": count for name, count in counts.items()},
            **provider_stats,
            "fetched_count": len(raw),
            "time_filtered_count": time_filtered_count,
            **dedup_stats,
            "selected_count": len(selected),
        }
        return DiscoveryResult(selected, stats, errors, tuple(sources))
