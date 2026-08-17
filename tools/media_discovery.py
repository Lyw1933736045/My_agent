"""NewsNow、RSS、Tavily 的串行统一发现出口。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .media_models import DiscoveryResult, MediaCandidate, SourceFetchResult
from .media_relevance import is_media_candidate_relevant
from ..utils.dedup import canonical_url, select_candidates, valid_provider_candidates


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
        tavily_queries: list[str] | None = None,
        weibo_queries: list[str] | None = None,
        newsnow_rss_core: list[str] | None = None,
        newsnow_rss_support: list[str] | None = None,
        topic: str = "",
        retrieval_check_node=None,
        adaptive_retrieval_node=None,
        adaptive_config: dict | None = None,
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
        provider_candidates: dict[str, list[MediaCandidate]] = {}
        provider_raw_results: dict[str, list[dict]] = {}
        effective_tavily_queries = list(tavily_queries or queries)
        effective_weibo_queries = list(weibo_queries or queries)
        for name, provider in self.providers.items():
            effective_queries = (
                effective_tavily_queries if name == "tavily"
                else effective_weibo_queries if name == "weibo" else queries
            )
            provider_candidates[name] = []
            if progress:
                progress(f"开始 {name} 发现……")
            try:
                if cancel_check and cancel_check():
                    raise RuntimeError("任务已中止")
                items = provider.search(
                    effective_queries,
                    limit=max(limit * 3, 20),
                    progress=progress,
                )
                if cancel_check and cancel_check():
                    raise RuntimeError("任务已中止")
                counts[name] = len(items)
                if name in {"newsnow", "rss"} and newsnow_rss_core:
                    fetched_items = items
                    items = [
                        item for item in fetched_items
                        if is_media_candidate_relevant(
                            item.title,
                            item.snippet,
                            newsnow_rss_core,
                            newsnow_rss_support or [],
                            name,
                        )
                    ]
                    provider_stats[f"{name}_relevance_filtered_count"] = (
                        len(fetched_items) - len(items)
                    )
                provider_candidates[name] = list(items)
                provider_raw_results[name] = list(
                    getattr(provider, "raw_results", [])
                )
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

        retrieval_reflection: dict[str, dict] = {}
        adaptive = adaptive_config or {}
        if (
            adaptive.get("enabled", False)
            and retrieval_check_node is not None
            and adaptive_retrieval_node is not None
        ):
            retrieval_reflection = retrieval_check_node.run({
                "provider_candidates": provider_candidates,
                "tavily_queries": effective_tavily_queries,
                "weibo_queries": effective_weibo_queries,
                "weibo_queries": effective_weibo_queries,
                "thresholds": {
                    "tavily": int(adaptive.get("tavily_min_valid_results", 3)),
                    "weibo": int(adaptive.get("weibo_min_valid_results", 2)),
                },
            })
            retry_queries = {}
            if any(
                item.get("adaptive_triggered")
                for item in retrieval_reflection.values()
            ):
                retry_queries = adaptive_retrieval_node.run({
                    "topic": topic,
                    "provider_candidates": provider_candidates,
                    "trace": retrieval_reflection,
                })
            for name, queries_for_retry in retry_queries.items():
                provider = self.providers.get(name)
                if provider is None or not queries_for_retry:
                    continue
                if cancel_check and cancel_check():
                    raise RuntimeError("任务已中止")
                if progress:
                    progress(
                        f"{name} 首轮有效结果不足，执行一次自适应补搜："
                        f"{'；'.join(queries_for_retry)}"
                    )
                try:
                    retry_items = provider.search(
                        queries_for_retry,
                        limit=max(limit * 3, 20),
                        progress=progress,
                    )
                    provider_candidates.setdefault(name, []).extend(retry_items)
                    if hasattr(provider, "raw_results"):
                        merged_raw_results: list[dict] = []
                        seen_raw: set[str] = set()
                        for item in (
                            provider_raw_results.get(name, [])
                            + list(getattr(provider, "raw_results", []))
                        ):
                            key = str(item.get("wid") or item.get("url") or "")
                            if not key or key in seen_raw:
                                continue
                            seen_raw.add(key)
                            merged_raw_results.append(item)
                        provider.raw_results = merged_raw_results
                        provider_raw_results[name] = merged_raw_results
                    raw.extend(retry_items)
                    counts[name] = len(provider_candidates[name])
                    trace = retrieval_reflection[name]
                    trace["retry_valid_count"] = len(
                        valid_provider_candidates(name, retry_items)
                    )
                    trace["final_valid_count"] = len(
                        valid_provider_candidates(name, provider_candidates[name])
                    )
                    if progress:
                        progress(
                            f"{name} 自适应补搜完成：新增有效 "
                            f"{trace['retry_valid_count']} 条，合并后 "
                            f"{trace['final_valid_count']} 条"
                        )
                except Exception as exc:
                    retrieval_reflection[name]["retry_error"] = str(exc)
                    errors[f"{name}/adaptive"] = str(exc)
                    if progress:
                        progress(f"{name} 自适应补搜失败：{exc}")

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

        # Stage 1 snapshot: only time-valid and prefiltered candidates are persisted.
        stage1_by_url: dict[str, MediaCandidate] = {}
        for candidate in filtered:
            url = candidate.url.strip()
            key = canonical_url(url) if url else ""
            if not key:
                continue
            existing = stage1_by_url.get(key)
            if existing is not None:
                existing_appearances = list(existing.metadata.get("appearances") or [])
                new_appearances = list(candidate.metadata.get("appearances") or [])
                merged_appearances = []
                seen_appearances: set[tuple] = set()
                for appearance in existing_appearances + new_appearances:
                    if not isinstance(appearance, dict):
                        continue
                    appearance_key = (
                        appearance.get("query"), appearance.get("channel"),
                        appearance.get("rank"), appearance.get("score"),
                    )
                    if appearance_key in seen_appearances:
                        continue
                    seen_appearances.add(appearance_key)
                    merged_appearances.append(appearance)
                metadata = dict(existing.metadata)
                metadata.update({
                    key: value for key, value in candidate.metadata.items()
                    if key != "appearances" and key not in metadata
                })
                metadata["appearances"] = merged_appearances
                stage1_by_url[key] = replace(
                    existing,
                    snippet=(
                        existing.snippet
                        if len(existing.snippet) >= len(candidate.snippet)
                        else candidate.snippet
                    ),
                    discovered_by=tuple(dict.fromkeys(
                        existing.discovered_by + candidate.discovered_by
                    )),
                    metadata=metadata,
                )
                continue
            stage1_by_url[key] = candidate
        stage1 = list(stage1_by_url.values())

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
        return DiscoveryResult(
            selected, stats, errors, tuple(sources), stage1,
            provider_candidates, retrieval_reflection,
        )
